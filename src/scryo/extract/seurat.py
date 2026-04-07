"""Extract Seurat RDS objects to parquet format."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_R_SCRIPT = Path(__file__).parent / "extract_seurat.R"


def check_r_available() -> bool:
    """Check if Rscript is available on PATH."""
    try:
        result = subprocess.run(
            ["Rscript", "--version"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def extract_seurat(rds_path: Path, output_path: Path) -> Path:
    """Extract a Seurat RDS file to parquet.

    The R script writes chunked parquet files to a temp directory,
    then this function combines them into a single parquet file.

    Args:
        rds_path: Path to the .rds file.
        output_path: Path for the output .parquet file.

    Returns:
        Path to the generated parquet file.

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

    chunks_dir = output_path.with_suffix(".chunks")
    chunks_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting Seurat object: %s", rds_path)
    logger.info("Chunks directory: %s", chunks_dir)

    result = subprocess.run(
        ["Rscript", str(_R_SCRIPT), str(rds_path), str(chunks_dir)],
        capture_output=True,
        text=True,
    )

    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            logger.info("[R] %s", line)

    if result.returncode != 0:
        raise RuntimeError(f"R extraction failed:\n{result.stderr}")

    from scryo.extract.combine import combine_chunks

    logger.info("Combining chunks into single parquet...")
    combine_chunks(chunks_dir, output_path)

    import shutil

    shutil.rmtree(chunks_dir, ignore_errors=True)

    logger.info("Extraction complete: %s", output_path)
    return output_path


