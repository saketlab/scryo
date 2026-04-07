"""Combine extracted parquet chunks into a single parquet."""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


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
