"""Extract 10x Genomics Cell Ranger .h5 files to parquet format."""

import io
import logging
import tarfile
from pathlib import Path
from typing import cast

import h5py
import numpy as np
import pandas as pd
from scipy.sparse import csc_matrix

from scryo.extract.combine import write_chunked_parquet

logger = logging.getLogger(__name__)

# Sorted by precedence — first match wins. Cell Ranger emits graphclust + kmeans;
# leiden is included for hand-built sidecars from scanpy.
_SORT_CLUSTER_PRIORITY: tuple[str, ...] = ("graphclust", "kmeans_10_clusters", "leiden")


def _make_unique(names: list[str]) -> list[str]:
    """Disambiguate duplicate names by appending -1, -2, ... .

    Matches the default separator of ``anndata.utils.make_index_unique`` so the
    h5ad and 10x extractors stay aligned. Kept local to avoid pulling in the
    optional anndata dep on the 10x path.
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen[name] = 0
            out.append(name)
        else:
            seen[name] += 1
            out.append(f"{name}-{seen[name]}")
    return out


def extract_h5(
    h5_path: Path,
    output_path: Path,
    *,
    analysis_path: Path | None = None,
) -> Path:
    """Extract a 10x Cell Ranger .h5 file to a single combined parquet.

    A 10x .h5 holds only the sparse count matrix; scryo additionally needs
    reductions (t-SNE / UMAP / PCA) and ideally a clustering to display the
    viewer. Those live in Cell Ranger's ``analysis/`` output, which this
    function loads from ``analysis_path`` (a directory or a ``*_analysis.tar.gz``
    tarball). When ``analysis_path`` is None, scryo looks for an
    ``<basename>_analysis.tar.gz`` or ``<basename>_analysis/`` next to the h5.
    """
    logger.info("Loading 10x h5: %s", h5_path)
    with h5py.File(h5_path, "r") as f:
        barcodes, gene_names, X_genes_cells = _read_10x_matrix(f)
    n_genes = len(gene_names)
    n_cells = len(barcodes)
    logger.info("  %d cells x %d genes", n_cells, n_genes)

    unique_genes = set(gene_names)
    if len(unique_genes) < n_genes:
        logger.info("  Disambiguating %d duplicate gene names", n_genes - len(unique_genes))
        gene_names = _make_unique(gene_names)

    # On-disk layout is genes x cells (CSC). Transpose then convert to CSR
    # in the cells x genes orientation: write_chunked_parquet streams by row
    # batches, and the sort_idx scatter below is O(len(sort_idx)) on CSR
    # vs O(nnz) on CSC.
    X = X_genes_cells.T.tocsr()

    if analysis_path is None:
        analysis_path = _find_analysis_sidecar(h5_path)
    if analysis_path is None:
        raise RuntimeError(
            "10x .h5 contains only the count matrix; scryo needs at least one "
            "reduction (t-SNE / UMAP / PCA) to render the viewer. Pass "
            "--analysis pointing to a Cell Ranger `analysis/` directory or "
            "`*_analysis.tar.gz` tarball, or place one next to the input h5 "
            "with the matching basename."
        )

    logger.info("Loading Cell Ranger analysis: %s", analysis_path)
    meta = _load_cellranger_analysis(analysis_path, barcodes)

    sort_idx = _build_sort_index(meta)
    if sort_idx is not None:
        meta = meta.iloc[sort_idx].reset_index(drop=True)
        X = X[sort_idx, :]

    return write_chunked_parquet(meta, X, gene_names, output_path)


def _read_10x_matrix(f: h5py.File) -> tuple[list[str], list[str], csc_matrix]:
    """Read a 10x v2 or v3 h5 file open handle into (barcodes, genes, csc_matrix).

    Returns the matrix in its on-disk orientation: genes x cells, CSC.
    """
    if "matrix" in f:
        # Cell Ranger 3+ layout
        g = cast(h5py.Group, f["matrix"])
        barcodes = [b.decode() for b in g["barcodes"][...]]  # type: ignore[index]
        features = cast(h5py.Group, g["features"])
        gene_names = [b.decode() for b in features["name"][...]]  # type: ignore[index]
    else:
        # Cell Ranger 2.x layout: a single named genome group at the root
        genome_keys = [k for k in f if isinstance(f[k], h5py.Group)]
        if len(genome_keys) != 1:
            raise RuntimeError(
                f"Expected exactly one genome group in 10x v2 h5; found {genome_keys}"
            )
        g = cast(h5py.Group, f[genome_keys[0]])
        barcodes = [b.decode() for b in g["barcodes"][...]]  # type: ignore[index]
        gene_names = [b.decode() for b in g["gene_names"][...]]  # type: ignore[index]

    shape = tuple(g["shape"][:].tolist())  # type: ignore[index]
    if len(shape) != 2:
        raise RuntimeError(f"Expected 2D shape, got {shape}")
    n_genes = shape[0]
    if n_genes >= 2**31:
        raise RuntimeError(f"Gene count {n_genes} exceeds int32 row-index range")

    data = np.asarray(g["data"][...])  # type: ignore[index]
    indices = np.asarray(g["indices"][...], dtype=np.int32)  # type: ignore[index]
    indptr = np.asarray(g["indptr"][...])  # type: ignore[index]

    X = csc_matrix((data, indices, indptr), shape=shape)
    return barcodes, gene_names, X


def _find_analysis_sidecar(h5_path: Path) -> Path | None:
    """Look for a Cell Ranger analysis tarball or directory next to ``h5_path``."""
    parent = h5_path.parent
    stem = h5_path.stem

    named: list[Path] = [
        parent / f"{stem}_analysis.tar.gz",
        parent / f"{stem}_analysis",
        parent / "analysis.tar.gz",
        parent / "analysis",
    ]
    # Also try the dataset prefix without the trailing 10x suffix words.
    # Source for these suffixes: 10x Cell Ranger output filename conventions.
    for suffix in ("_filtered_gene_bc_matrices_h5", "_filtered_feature_bc_matrix"):
        if stem.endswith(suffix):
            base = stem[: -len(suffix)]
            named.append(parent / f"{base}_analysis.tar.gz")
            named.append(parent / f"{base}_analysis")

    for c in named:
        if c.exists():
            return c

    # Last resort: any *_analysis.tar.gz in the same directory.
    for c in sorted(parent.glob("*_analysis.tar.gz")):
        if c.exists():
            logger.warning(
                "Falling back to analysis tarball %s (no name match for %s)",
                c,
                h5_path.name,
            )
            return c
    return None


def _is_relevant_analysis_csv(name: str) -> bool:
    """Return True if ``name`` is a Cell Ranger projection.csv or clusters.csv."""
    if name.endswith("/projection.csv"):
        return any(f"/{d}/" in name for d in ("tsne", "umap", "pca"))
    if name.endswith("/clusters.csv"):
        return "/clustering/" in name
    return False


def _read_relevant_columns(buf: bytes, kind: str) -> pd.DataFrame:
    """Read only the columns scryo actually consumes from a Cell Ranger CSV.

    For projection.csv we keep ``Barcode`` plus the first two coordinate
    columns. For clusters.csv we keep ``Barcode`` plus the cluster label
    column. Avoids parsing 10+ unused PC components on a 1.3M-row CSV.
    """
    cols = list(pd.read_csv(io.BytesIO(buf), nrows=0).columns)
    if "Barcode" not in cols:
        return pd.read_csv(io.BytesIO(buf))
    non_barcode = [c for c in cols if c != "Barcode"]
    n_keep = 2 if kind in ("tsne", "umap", "pca") else 1
    keep = {"Barcode", *non_barcode[:n_keep]}
    return pd.read_csv(io.BytesIO(buf), usecols=lambda c: c in keep)


def _load_cellranger_analysis(analysis_path: Path, barcodes: list[str]) -> pd.DataFrame:
    """Build a metadata DataFrame from a Cell Ranger ``analysis/`` directory or tarball.

    Pulls every projection (t-SNE / UMAP / PCA — first two components) found
    under ``analysis/{tsne,umap,pca}/<n>_components/projection.csv`` and the
    graphclust + k-means clusterings under
    ``analysis/clustering/{graphclust,kmeans_*_clusters}/clusters.csv``.

    Rows are aligned to the input ``barcodes`` order via index alignment on
    the Barcode column.
    """
    if analysis_path.is_file() and analysis_path.suffixes[-2:] == [".tar", ".gz"]:
        parsed = _parse_tarball(analysis_path)
    elif analysis_path.is_dir():
        parsed = _parse_directory(analysis_path)
    else:
        raise RuntimeError(
            f"--analysis path is not a directory or .tar.gz tarball: {analysis_path}"
        )
    return _assemble_metadata(parsed, barcodes)


def _parse_tarball(tar_path: Path) -> dict[str, pd.DataFrame]:
    parsed: dict[str, pd.DataFrame] = {}
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or not _is_relevant_analysis_csv(member.name):
                continue
            kind = _classify(member.name)
            if kind is None:
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            try:
                buf = fh.read()
            finally:
                fh.close()
            parsed[member.name] = _read_relevant_columns(buf, kind.split(":", 1)[0])
    return parsed


def _parse_directory(analysis_dir: Path) -> dict[str, pd.DataFrame]:
    parsed: dict[str, pd.DataFrame] = {}
    for csv_path in [
        *analysis_dir.rglob("projection.csv"),
        *analysis_dir.rglob("clusters.csv"),
    ]:
        rel = csv_path.relative_to(analysis_dir.parent).as_posix()
        if not _is_relevant_analysis_csv(f"/{rel}"):
            continue
        kind = _classify(rel)
        if kind is None:
            continue
        parsed[rel] = _read_relevant_columns(csv_path.read_bytes(), kind.split(":", 1)[0])
    return parsed


def _assemble_metadata(parsed: dict[str, pd.DataFrame], barcodes: list[str]) -> pd.DataFrame:
    """Index-align Cell Ranger CSVs against the input barcode list."""
    if not parsed:
        raise RuntimeError(
            "Cell Ranger analysis directory contains no projection.csv or "
            "clusters.csv files (expected under analysis/{tsne,umap,pca}/.../"
            "projection.csv or analysis/clustering/.../clusters.csv)."
        )

    base = pd.DataFrame(index=pd.Index(barcodes, name="Barcode"))
    for name, df in parsed.items():
        kind = _classify(name)
        if kind is None:
            continue
        if "Barcode" not in df.columns:
            logger.warning("  Skipping %s (no Barcode column)", name)
            continue
        sub = df.set_index("Barcode")

        if kind in ("tsne", "umap", "pca"):
            if sub.shape[1] < 2:
                continue
            base[f"{kind}_1"] = sub.iloc[:, 0]
            base[f"{kind}_2"] = sub.iloc[:, 1]
            logger.info("  Added reduction: %s", kind)
        elif kind.startswith("clustering:"):
            if sub.shape[1] < 1:
                continue
            label = kind.split(":", 1)[1]
            col = sub.iloc[:, 0]
            base[label] = pd.Categorical(col.reindex(base.index).astype("string").fillna(""))
            n_missing = (base[label] == "").sum()
            if n_missing:
                logger.warning("  %d cells have no %s assignment", n_missing, label)
            logger.info("  Added clustering: %s", label)

    base = base.reset_index(drop=True)
    if not any(c in base.columns for c in ("tsne_1", "umap_1", "pca_1")):
        raise RuntimeError(
            "Cell Ranger analysis loaded but no t-SNE / UMAP / PCA projection "
            "was found; cannot render the viewer."
        )
    return base


def _classify(name: str) -> str | None:
    parts = name.split("/")
    if "tsne" in parts and parts[-1] == "projection.csv":
        return "tsne"
    if "umap" in parts and parts[-1] == "projection.csv":
        return "umap"
    if "pca" in parts and parts[-1] == "projection.csv":
        return "pca"
    if "clustering" in parts and parts[-1] == "clusters.csv":
        idx = parts.index("clustering")
        if idx + 1 < len(parts):
            return f"clustering:{parts[idx + 1]}"
    return None


def _build_sort_index(meta: pd.DataFrame) -> np.ndarray | None:
    """Sort cells by graphclust (or first cluster column) for chunk compression."""
    cluster_col: str | None = None
    for candidate in _SORT_CLUSTER_PRIORITY:
        if candidate in meta.columns:
            cluster_col = candidate
            break
    if cluster_col is None:
        return None
    logger.info("  Sorting cells by: %s", cluster_col)
    return meta.sort_values(by=cluster_col, kind="stable").index.to_numpy()
