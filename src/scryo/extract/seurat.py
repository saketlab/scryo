"""Extract Seurat RDS objects to a metadata parquet + sparse gene sidecar.

The R extractor (``extract_seurat.R``) writes the metadata parquet and a
gene-major sparse sidecar directly; there is no in-memory "combine" step, so
peak memory is bounded by the R session, not by the dense cell x gene matrix
(which is ~300 GB for a 1M cell x 74k gene atlas and would OOM).

Long-running stages stream ``@@SCRYO`` progress markers on stdout which this
module renders as tqdm progress bars.
"""

import contextlib
import logging
import subprocess
import threading
import time
from pathlib import Path

from tqdm import tqdm

logger = logging.getLogger(__name__)

_R_SCRIPT = Path(__file__).parent / "extract_seurat.R"


def check_r_available() -> bool:
    """Check if Rscript is available on PATH."""
    try:
        result = subprocess.run(
            ["Rscript", "--version"], capture_output=True, text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def sidecar_for(parquet_path: Path) -> Path:
    """Path of the gene sidecar directory for a given metadata parquet."""
    return parquet_path.with_suffix(".genes")


class _ProgressRenderer:
    """Render R extraction progress as tqdm bars.

    Indeterminate stages (readRDS, transpose) show a spinner whose elapsed time
    is advanced by a background ticker thread, since R emits no output while it
    blocks inside those calls. Determinate stages (binary writes) update from
    ``WRITE_PROGRESS`` markers.
    """

    def __init__(self) -> None:
        self._bar: tqdm | None = None
        self._spinner_stop: threading.Event | None = None
        self._spinner_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _close(self) -> None:
        self._stop_spinner()
        if self._bar is not None:
            self._bar.close()
            self._bar = None

    def _stop_spinner(self) -> None:
        if self._spinner_stop is not None:
            self._spinner_stop.set()
        if self._spinner_thread is not None:
            self._spinner_thread.join(timeout=1.0)
        self._spinner_stop = None
        self._spinner_thread = None

    def start_spinner(self, desc: str) -> None:
        with self._lock:
            self._close()
            bar = tqdm(
                total=None,
                desc=desc,
                bar_format="{desc} [{elapsed}] {postfix}",
                leave=True,
            )
            bar.set_postfix_str("working...")
            self._bar = bar
            stop = threading.Event()
            self._spinner_stop = stop

            def _tick() -> None:
                while not stop.wait(1.0):
                    with self._lock:
                        if self._bar is bar:
                            bar.refresh()

            t = threading.Thread(target=_tick, daemon=True)
            self._spinner_thread = t
            t.start()

    def finish_spinner(self, suffix: str = "done") -> None:
        with self._lock:
            if self._bar is not None:
                self._bar.set_postfix_str(suffix)
            self._close()

    def start_bar(self, desc: str, total: int) -> None:
        with self._lock:
            self._close()
            self._bar = tqdm(
                total=total, desc=desc, unit="nnz", unit_scale=True, leave=True
            )

    def update_bar(self, done: int) -> None:
        with self._lock:
            if self._bar is not None and self._bar.total:
                self._bar.update(done - self._bar.n)

    def close(self) -> None:
        with self._lock:
            self._close()


def _handle_marker(event: str, rest: list[str], render: _ProgressRenderer) -> None:
    if event == "READRDS_START":
        path = rest[0] if rest else ""
        size_gb = ""
        with contextlib.suppress(OSError):
            size_gb = f" ({Path(path).stat().st_size / 1e9:.1f} GB)"
        render.start_spinner(f"Reading RDS{size_gb}")
    elif event == "READRDS_DONE":
        secs = rest[0] if rest else "?"
        render.finish_spinner(f"done in {secs}s")
    elif event == "META_START":
        render.start_spinner("Extracting metadata + reductions")
    elif event == "META_DONE":
        render.finish_spinner("done")
        logger.info("[R] metadata: %s", " ".join(rest))
    elif event == "TRANSPOSE_START":
        render.start_spinner(f"Transposing to gene-major sparse ({' '.join(rest)})")
    elif event == "TRANSPOSE_DONE":
        render.finish_spinner(f"done ({' '.join(rest)})")
    elif event == "WRITE_START":
        # The bar's total arrives with the first WRITE_PROGRESS line.
        render.start_spinner(f"Writing {' '.join(rest)}.bin")
    elif event == "WRITE_PROGRESS":
        # rest = [label, done, total]
        if len(rest) == 3:
            done, total = int(rest[1]), int(rest[2])
            with render._lock:
                bar = render._bar
                is_spinner = render._spinner_thread is not None
            if bar is None or is_spinner or bar.total != total:
                render.start_bar(f"Writing {rest[0]}.bin", total)
            render.update_bar(done)
    elif event == "INFO":
        logger.info("[R] %s", " ".join(rest))
    elif event == "ALL_DONE":
        render.finish_spinner("done")


def extract_seurat(rds_path: Path, output_path: Path) -> Path:
    """Extract a Seurat RDS file to a metadata parquet + sparse gene sidecar.

    Args:
        rds_path: Path to the ``.rds`` file.
        output_path: Path for the metadata ``.parquet``. The gene sidecar is
            written next to it at ``output_path.with_suffix('.genes')``.

    Returns:
        Path to the generated metadata parquet.

    Raises:
        RuntimeError: If R is not installed or extraction fails.
    """
    if not check_r_available():
        raise RuntimeError(
            "Rscript not found. Install R and ensure it's on your PATH.\n"
            "Required R packages: Seurat, arrow, Matrix"
        )
    if not rds_path.exists():
        raise FileNotFoundError(f"RDS file not found: {rds_path}")

    sidecar = sidecar_for(output_path)
    logger.info("Extracting Seurat object: %s", rds_path)
    logger.info("  metadata parquet: %s", output_path)
    logger.info("  gene sidecar:     %s", sidecar)

    render = _ProgressRenderer()
    start = time.monotonic()
    proc = subprocess.Popen(
        ["Rscript", str(_R_SCRIPT), str(rds_path), str(output_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: list[str] = []
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith("@@SCRYO "):
                parts = line.split()
                _handle_marker(parts[1], parts[2:], render)
            elif line.strip():
                tail.append(line)
                if len(tail) > 50:
                    tail.pop(0)
                logger.debug("[R] %s", line)
    finally:
        render.close()
    proc.wait()

    if proc.returncode != 0:
        raise RuntimeError("R extraction failed:\n" + "\n".join(tail[-20:]))

    elapsed = time.monotonic() - start
    logger.info(
        "Extraction complete in %.0fs: %s (+ %s)", elapsed, output_path, sidecar.name
    )
    return output_path
