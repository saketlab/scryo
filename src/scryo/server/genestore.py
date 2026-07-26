"""Gene-major sparse expression store (memory-mapped); one contiguous column per gene."""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_INFO = "info.json"


def has_gene_store(parquet_path: Path) -> bool:
    """True if a sparse gene sidecar exists next to ``parquet_path``."""
    sidecar = parquet_path.with_suffix(".genes")
    return (sidecar / _INFO).exists()


class GeneStore:
    """Read-only, memory-mapped gene-major sparse expression matrix."""

    def __init__(self, sidecar: Path) -> None:
        self.sidecar = sidecar
        info = json.loads((sidecar / _INFO).read_text())
        self.n_cells: int = int(info["n_cells"])
        self.n_genes: int = int(info["n_genes"])
        self.nnz: int = int(info["nnz"])
        self.assay: str = info.get("assay", "RNA")

        self.gene_names: list[str] = (sidecar / "genes.txt").read_text().splitlines()
        if len(self.gene_names) != self.n_genes:
            raise ValueError(
                f"genes.txt has {len(self.gene_names)} names, info says {self.n_genes}"
            )

        # indptr is small enough to load eagerly; indices/data page in lazily
        self.indptr = np.fromfile(sidecar / "indptr.bin", dtype=np.int32).astype(np.int64)
        self.indices = np.memmap(sidecar / "indices.bin", dtype=np.int32, mode="r")
        self.data = np.memmap(sidecar / "data.bin", dtype=np.float64, mode="r")
        if self.indptr.shape[0] != self.n_genes + 1:
            raise ValueError("indptr length does not match n_genes + 1")

        self._col_to_idx: dict[str, int] = {
            f"{g}_{self.assay}": i for i, g in enumerate(self.gene_names)
        }
        logger.info(
            "GeneStore: %d genes x %d cells, %d nnz (%.1f%% dense), assay=%s",
            self.n_genes,
            self.n_cells,
            self.nnz,
            100.0 * self.nnz / max(1, self.n_genes * self.n_cells),
            self.assay,
        )

    @property
    def column_names(self) -> list[str]:
        return list(self._col_to_idx.keys())

    def has_column(self, column: str) -> bool:
        return column in self._col_to_idx

    def read_column(self, column: str) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(cell_ids, values)`` for the gene's non-zero entries.

        ``cell_ids`` are int64 row indices into the (cell-ordered) dataset and
        ``values`` are float64 expression values. Zero entries are omitted.
        """
        gi = self._col_to_idx[column]
        s, e = int(self.indptr[gi]), int(self.indptr[gi + 1])
        cells = np.asarray(self.indices[s:e], dtype=np.int64)
        vals = np.asarray(self.data[s:e], dtype=np.float64)
        return cells, vals
