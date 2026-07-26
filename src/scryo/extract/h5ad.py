"""Extract AnnData h5ad files to parquet format."""

import importlib.util
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from scryo.extract.combine import write_chunked_parquet

logger = logging.getLogger(__name__)

_CELLTYPE_CANDIDATES = (
    "azimuth",
    "celltype",
    "cell_type",
    "cluster",
    "leiden",
    "louvain",
    "seurat_clusters",
)

# gene-symbol columns to try in adata.var when var_names are IDs; first match wins
_GENE_SYMBOL_CANDIDATES = (
    "feature_name",
    "gene_symbols",
    "gene_symbol",
    "gene_name",
    "gene_names",
    "symbol",
    "Symbol",
    "hgnc_symbol",
)


def check_anndata_available() -> bool:
    """Check if the anndata package is importable."""
    return importlib.util.find_spec("anndata") is not None


def extract_h5ad(h5ad_path: Path, output_path: Path) -> Path:
    """Extract an AnnData .h5ad file to a single combined parquet.

    Mirrors the layout produced by ``extract_seurat``: metadata + reduction
    columns (``<reduc>_1``/``<reduc>_2``) plus expression columns named
    ``<gene>_RNA``. Sparse matrices are densified one chunk at a time so the
    full matrix never has to fit in RAM.
    """
    if not check_anndata_available():
        raise RuntimeError(
            "anndata is not installed. Install with: pip install 'scryo[h5ad]'\n"
            "or directly: pip install anndata"
        )

    import anndata
    from scipy.sparse import csr_matrix, issparse

    logger.info("Loading AnnData: %s", h5ad_path)
    adata = anndata.read_h5ad(h5ad_path)
    n_cells, n_genes = adata.n_obs, adata.n_vars
    logger.info("  %d cells x %d genes", n_cells, n_genes)

    meta = pd.DataFrame(adata.obs).copy()
    # _build_sort_index returns positional indices; keep meta.index as RangeIndex.
    meta.index = pd.RangeIndex(len(meta))

    for col in list(meta.columns):
        values = meta[col]
        if values.dtype != object and not isinstance(values.dtype, pd.CategoricalDtype):
            continue
        if isinstance(values.dtype, pd.CategoricalDtype):
            cats = values.cat.categories.astype(str)
            barcode_frac = float(cats.str.endswith("-1").mean()) if len(cats) else 0.0
        else:
            sample = values.dropna().astype(str).head(1000)
            barcode_frac = float(sample.str.endswith("-1").mean()) if len(sample) else 0.0
        if barcode_frac > 0.5:
            logger.info("  Removing barcode column: %s", col)
            meta = meta.drop(columns=[col])

    logger.info("Extracting reductions...")
    for key in adata.obsm:
        arr = np.asarray(adata.obsm[key])
        if arr.ndim != 2 or arr.shape[1] < 2:
            continue
        name = key[2:] if key.startswith("X_") else key
        meta[f"{name}_1"] = arr[:, 0]
        meta[f"{name}_2"] = arr[:, 1]
        logger.info("  Added: %s", name)

    sort_idx = _build_sort_index(meta)
    if sort_idx is not None:
        meta = meta.iloc[sort_idx].reset_index(drop=True)

    gene_names = _resolve_gene_names(adata)

    logger.info("Extracting expression matrix as RNA assay...")
    X = adata.X
    if X is None:
        raise RuntimeError(
            "AnnData.X is None; scryo needs a primary expression matrix. "
            "Try `adata.X = adata.layers['counts']` (or another layer) before saving."
        )

    # CSR: parquet is written by row batch, and row indexing is O(len(sort_idx)) not O(nnz)
    if issparse(X):
        X_out: csr_matrix | np.ndarray = csr_matrix(X)
        if sort_idx is not None:
            X_out = X_out[sort_idx, :]
    else:
        X_out = np.asarray(X)
        if sort_idx is not None:
            X_out = np.ascontiguousarray(X_out[sort_idx, :])

    return write_chunked_parquet(meta, X_out, gene_names, output_path)


def _resolve_gene_names(adata) -> list[str]:
    """Pick gene display names, preferring symbols from ``adata.var`` when present.

    CellxGene h5ad files use Ensembl IDs for ``var_names`` and store human-
    readable symbols in ``var['feature_name']``; Scanpy/10x sometimes use
    ``gene_symbols``. Rows with empty/NaN symbols fall back to the underlying
    ``var_names`` entry so no column becomes anonymous (cellxgene itself
    follows this convention for unnamed loci).
    """
    from anndata.utils import make_index_unique

    var = adata.var
    var_names_arr = np.asarray(adata.var_names, dtype=str)

    for candidate in _GENE_SYMBOL_CANDIDATES:
        if candidate not in var.columns:
            continue
        col = var[candidate]
        str_values = col.astype(str).to_numpy()
        missing = col.isna().to_numpy() | (str_values == "")
        values = np.where(missing, var_names_arr, str_values)
        logger.info("  Using var['%s'] for gene display names", candidate)
        return list(make_index_unique(pd.Index(values)))

    return list(make_index_unique(pd.Index(var_names_arr)))


def _build_sort_index(meta: pd.DataFrame) -> np.ndarray | None:
    """Return a row order that groups cells by celltype + nCount_RNA, or None."""
    celltype_col: str | None = None
    for candidate in _CELLTYPE_CANDIDATES:
        matches = [c for c in meta.columns if candidate.lower() in c.lower()]
        if matches:
            celltype_col = matches[0]
            break

    if celltype_col is None:
        return None

    secondary: str | None = None
    for candidate in ("nCount_RNA", "n_counts", "total_counts"):
        if candidate in meta.columns:
            secondary = candidate
            break

    sort_cols: list[str] = [celltype_col]
    if secondary is not None:
        sort_cols.append(secondary)
    logger.info("  Sorting cells by: %s", sort_cols)
    return meta.sort_values(by=sort_cols).index.to_numpy()
