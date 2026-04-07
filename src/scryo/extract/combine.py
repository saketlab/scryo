"""Combine extracted parquet chunks into a single parquet."""

import logging
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csc_matrix

logger = logging.getLogger(__name__)

CHUNK_SIZE = 2000

ExpressionMatrix = csc_matrix | np.ndarray


def combine_chunks(chunks_dir: Path, output_path: Path) -> Path:
    """Combine metadata + gene expression chunks into a single parquet.

    Reads chunks one at a time and concatenates column-wise.

    Args:
        chunks_dir: Directory containing metadata.parquet and *_chunk_*.parquet files.
        output_path: Path for the combined output parquet.

    Returns:
        Path to the combined parquet file.
    """
    meta_path = chunks_dir / "metadata.parquet"
    if not meta_path.exists():
        raise RuntimeError(f"Metadata parquet not found: {meta_path}")

    chunk_files = sorted(chunks_dir.glob("*_chunk_*.parquet"))
    if not chunk_files:
        raise RuntimeError(f"No expression chunk files found in: {chunks_dir}")

    logger.info("Combining %d chunks + metadata", len(chunk_files))

    logger.info("Reading metadata...")
    result = pd.read_parquet(meta_path)
    n_cells = len(result)
    logger.info("  %d cells, %d columns", n_cells, len(result.columns))

    for i, chunk_file in enumerate(chunk_files):
        logger.info("  Reading chunk %d/%d: %s", i + 1, len(chunk_files), chunk_file.name)
        chunk = pd.read_parquet(chunk_file)

        if len(chunk) != n_cells:
            logger.warning("  Chunk has %d rows, expected %d", len(chunk), n_cells)

        chunk.index = result.index
        result = pd.concat([result, chunk], axis=1)

        if (i + 1) % 10 == 0:
            logger.info("  Total columns so far: %d", len(result.columns))

    logger.info("Combined: %d cells × %d columns", len(result), len(result.columns))

    logger.info("Writing to %s ...", output_path)
    result.to_parquet(output_path, compression="zstd")

    size_gb = output_path.stat().st_size / (1024**3)
    logger.info("Done: %.2f GB", size_gb)

    return output_path


def write_chunked_parquet(
    meta: pd.DataFrame,
    X: ExpressionMatrix,
    gene_names: list[str],
    output_path: Path,
    *,
    chunk_size: int = CHUNK_SIZE,
    assay: str = "RNA",
) -> Path:
    """Write metadata + chunked gene expression columns to a single parquet.

    Both ``meta`` and ``X`` must already be in their final row order (i.e. any
    cell-wise sorting has been applied). ``X`` must be cells x genes; sparse
    inputs should be CSC for efficient column slicing. Each gene becomes a
    column named ``<gene>_<assay>`` in the combined output (default ``RNA``);
    the suffix is load-bearing — see ``server/app.py:gene_suffixes`` and
    ``CLAUDE.md`` for the project-wide convention.
    """
    n_rows, n_cols = X.shape  # type: ignore[misc]
    if n_rows != len(meta):
        raise ValueError(f"X has {n_rows} rows but meta has {len(meta)} rows")
    if n_cols != len(gene_names):
        raise ValueError(
            f"X has {n_cols} columns but {len(gene_names)} gene names given"
        )

    chunks_dir = output_path.with_suffix(".chunks")
    chunks_dir.mkdir(parents=True, exist_ok=True)

    meta_path = chunks_dir / "metadata.parquet"
    logger.info("Writing metadata: %s", meta_path)
    meta.to_parquet(meta_path, compression="zstd")

    n_chunks = (n_cols + chunk_size - 1) // chunk_size
    for chunk_idx in range(n_chunks):
        start = chunk_idx * chunk_size
        end = min(start + chunk_size, n_cols)
        chunk_genes = gene_names[start:end]

        if isinstance(X, csc_matrix):
            chunk_mat = X[:, start:end].toarray().astype(np.float32, copy=False)
        else:
            chunk_mat = np.asarray(X[:, start:end], dtype=np.float32)
        chunk_df = pd.DataFrame(
            chunk_mat,
            columns=pd.Index([f"{g}_{assay}" for g in chunk_genes]),
        )
        chunk_path = chunks_dir / f"{assay}_chunk_{chunk_idx + 1:03d}.parquet"
        chunk_df.to_parquet(chunk_path, compression="zstd")
        logger.info(
            "  Chunk %d/%d: %d genes written",
            chunk_idx + 1,
            n_chunks,
            len(chunk_genes),
        )

    logger.info("Combining chunks into single parquet...")
    combine_chunks(chunks_dir, output_path)
    shutil.rmtree(chunks_dir, ignore_errors=True)

    logger.info("Extraction complete: %s", output_path)
    return output_path
