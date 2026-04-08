"""Stream meta + gene expression columns into a single parquet."""

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.sparse import csc_matrix, csr_matrix, issparse

logger = logging.getLogger(__name__)

CELL_BATCH = 50_000

ExpressionMatrix = csr_matrix | csc_matrix | np.ndarray


def write_chunked_parquet(
    meta: pd.DataFrame,
    X: ExpressionMatrix,
    gene_names: list[str],
    output_path: Path,
    *,
    cell_batch: int = CELL_BATCH,
    assay: str = "RNA",
) -> Path:
    """Stream metadata + dense gene columns into a single parquet via row groups.

    Both ``meta`` and ``X`` must already be in their final row order. ``X`` is
    cells x genes; sparse inputs that aren't already CSR are converted up
    front because per-batch row slicing is O(nnz_in_batch) on CSR vs O(nnz)
    on CSC. Each gene becomes a column named ``<gene>_<assay>`` -- the suffix
    is load-bearing for ``server/app.py:gene_suffixes``.

    Peak memory ~ ``cell_batch * len(gene_names) * 4`` bytes (one row group
    of dense float32), independent of total cell count.
    """
    n_cells, n_genes = X.shape  # type: ignore[misc]
    if n_cells != len(meta):
        raise ValueError(f"X has {n_cells} rows but meta has {len(meta)} rows")
    if n_genes != len(gene_names):
        raise ValueError(f"X has {n_genes} cols but {len(gene_names)} gene names given")

    if issparse(X):
        X_csr: csr_matrix = X if isinstance(X, csr_matrix) else X.tocsr()  # type: ignore[union-attr]

        def _read(start: int, end: int) -> np.ndarray:
            block: csr_matrix = X_csr[start:end]  # type: ignore[assignment]
            return block.toarray().astype(np.float32, copy=False)
    else:
        X_dense = np.asarray(X)

        def _read(start: int, end: int) -> np.ndarray:
            return X_dense[start:end].astype(np.float32, copy=False)

    return write_streamed_parquet(
        meta=meta,
        n_cells=n_cells,
        n_genes=n_genes,
        gene_names=gene_names,
        output_path=output_path,
        read_dense_rows=_read,
        cell_batch=cell_batch,
        assay=assay,
    )


def write_streamed_parquet(
    *,
    meta: pd.DataFrame,
    n_cells: int,
    n_genes: int,
    gene_names: list[str],
    output_path: Path,
    read_dense_rows: Callable[[int, int], np.ndarray],
    cell_batch: int = CELL_BATCH,
    assay: str = "RNA",
) -> Path:
    """Stream meta + caller-supplied dense row blocks into one parquet.

    ``read_dense_rows(start, end)`` must return a contiguous float32 ndarray
    of shape ``(end - start, n_genes)``. Used by the orion extract script to
    feed h5py CSR slices straight into pyarrow without ever materialising
    the full sparse matrix or a wide pandas DataFrame.
    """
    if n_cells != len(meta):
        raise ValueError(f"n_cells={n_cells} but meta has {len(meta)} rows")
    if n_genes != len(gene_names):
        raise ValueError(f"n_genes={n_genes} but {len(gene_names)} gene names given")

    meta_table = pa.Table.from_pandas(meta, preserve_index=False)
    gene_field_names = [f"{g}_{assay}" for g in gene_names]
    gene_fields = [pa.field(name, pa.float32()) for name in gene_field_names]
    schema = pa.schema(list(meta_table.schema) + gene_fields)

    n_batches = (n_cells + cell_batch - 1) // cell_batch
    logger.info(
        "Writing %s: %d cells x %d cols, %d row groups (batch=%d)",
        output_path, n_cells, len(schema), n_batches, cell_batch,
    )

    with pq.ParquetWriter(
        output_path, schema, compression="zstd", compression_level=3
    ) as writer:
        for bi in range(n_batches):
            start = bi * cell_batch
            end = min(start + cell_batch, n_cells)
            dense = read_dense_rows(start, end)
            if dense.shape != (end - start, n_genes):
                raise ValueError(
                    f"read_dense_rows({start},{end}) returned shape {dense.shape}, "
                    f"expected ({end - start}, {n_genes})"
                )

            arrays: list[pa.Array | pa.ChunkedArray] = [
                meta_table.column(name).slice(start, end - start)
                for name in meta_table.column_names
            ]
            for j in range(n_genes):
                arrays.append(pa.array(dense[:, j], type=pa.float32()))

            row_group = pa.Table.from_arrays(arrays, schema=schema)
            writer.write_table(row_group)
            logger.info("  row group %d/%d (cells %d-%d)", bi + 1, n_batches, start, end)

    size_gb = output_path.stat().st_size / 1e9
    logger.info("Done: %s (%.2f GB)", output_path, size_gb)
    return output_path


def combine_chunks(
    chunks_dir: Path,
    output_path: Path,
    *,
    cell_batch: int = CELL_BATCH,
) -> Path:
    """Combine R-produced extractor chunks (metadata + per-gene-chunk parquets) into one parquet.

    Used only by ``extract_seurat`` -- the R script writes ``metadata.parquet``
    and ``<assay>_chunk_NNN.parquet`` files into ``chunks_dir``. We load each
    chunk eagerly as a pyarrow Table (zero-copy column references), then for
    each cell batch slice all chunks and emit one row group. Peak memory
    ~ size of dataset in arrow form, vs ~10x for the previous pandas
    pd.concat loop.
    """
    meta_path = chunks_dir / "metadata.parquet"
    if not meta_path.exists():
        raise RuntimeError(f"Metadata parquet not found: {meta_path}")
    chunk_files = sorted(chunks_dir.glob("*_chunk_*.parquet"))
    if not chunk_files:
        raise RuntimeError(f"No expression chunk files found in: {chunks_dir}")

    logger.info("Combining %d chunks + metadata", len(chunk_files))
    meta_table = pq.read_table(meta_path)
    n_cells = meta_table.num_rows

    chunk_tables: list[pa.Table] = []
    for i, p in enumerate(chunk_files):
        logger.info("  Loading chunk %d/%d: %s", i + 1, len(chunk_files), p.name)
        ct = pq.read_table(p)
        if ct.num_rows != n_cells:
            raise RuntimeError(f"{p.name}: {ct.num_rows} rows, expected {n_cells}")
        chunk_tables.append(ct)

    schema = meta_table.schema
    for ct in chunk_tables:
        for f in ct.schema:
            schema = schema.append(f)

    n_batches = (n_cells + cell_batch - 1) // cell_batch
    logger.info(
        "Writing %s: %d cells x %d cols, %d row groups (batch=%d)",
        output_path, n_cells, len(schema), n_batches, cell_batch,
    )

    with pq.ParquetWriter(
        output_path, schema, compression="zstd", compression_level=3
    ) as writer:
        for bi in range(n_batches):
            start = bi * cell_batch
            end = min(start + cell_batch, n_cells)
            arrays: list[pa.Array | pa.ChunkedArray] = list(
                meta_table.slice(start, end - start).columns
            )
            for ct in chunk_tables:
                arrays.extend(ct.slice(start, end - start).columns)
            row_group = pa.Table.from_arrays(arrays, schema=schema)
            writer.write_table(row_group)
            logger.info("  row group %d/%d (cells %d-%d)", bi + 1, n_batches, start, end)

    size_gb = output_path.stat().st_size / 1e9
    logger.info("Done: %s (%.2f GB)", output_path, size_gb)
    return output_path
