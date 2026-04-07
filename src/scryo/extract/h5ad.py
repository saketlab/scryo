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
    from anndata.utils import make_index_unique
    from scipy.sparse import csc_matrix, issparse

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

    gene_names = list(make_index_unique(pd.Index(adata.var_names)))

    logger.info("Extracting expression matrix as RNA assay...")
    X = adata.X
    if X is None:
        raise RuntimeError(
            "AnnData.X is None; scryo needs a primary expression matrix. "
            "Try `adata.X = adata.layers['counts']` (or another layer) before saving."
        )

    # Use CSC for sparse: column slicing is O(1) on indptr, vs CSR which walks
    # every row per chunk. Apply the row permutation once up-front so per-chunk
    # densification doesn't repeat the scatter.
    if issparse(X):
        X_out: csc_matrix | np.ndarray = csc_matrix(X)
        if sort_idx is not None:
            X_out = X_out[sort_idx, :]
    else:
        X_out = np.asarray(X)
        if sort_idx is not None:
            X_out = np.ascontiguousarray(X_out[sort_idx, :])

    return write_chunked_parquet(meta, X_out, gene_names, output_path)


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
