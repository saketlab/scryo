"""scryo CLI entry point."""

import logging
from pathlib import Path

import click

from scryo import __version__

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure logging for scryo."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@click.command()
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--host", default="0.0.0.0", help="Server host address.")
@click.option("--port", default=8050, type=int, help="Server port.")
@click.option(
    "--duckdb",
    "duckdb_mode",
    default="server",
    type=click.Choice(["wasm", "server"]),
    help="DuckDB mode: 'server' (lazy loading) or 'wasm' (in-browser).",
)
@click.option(
    "--force-extract",
    is_flag=True,
    default=False,
    help="Force re-extraction of source file even if parquet cache exists.",
)
@click.option(
    "--analysis",
    "analysis_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "For 10x .h5 input: path to the Cell Ranger analysis directory or "
        "*_analysis.tar.gz tarball that holds reductions/clusters. If omitted, "
        "scryo looks for one next to the h5."
    ),
)
@click.version_option(version=__version__, package_name="scryo")
def main(
    input_path: Path,
    host: str,
    port: int,
    duckdb_mode: str,
    force_extract: bool,
    analysis_path: Path | None,
) -> None:
    """scryo — Single-cell RNA-seq visualization.

    Launch a visualization server for a Seurat RDS, AnnData h5ad, 10x Cell
    Ranger h5, or parquet file.

    \b
    Examples:
        scryo /path/to/data.rds
        scryo /path/to/data.h5ad
        scryo /path/to/data.h5 --analysis /path/to/analysis.tar.gz
        scryo /path/to/data.rds --duckdb wasm
        scryo /path/to/data.parquet
    """
    setup_logging()

    input_path = input_path.resolve()
    if analysis_path is not None:
        analysis_path = analysis_path.resolve()
    suffix = input_path.suffix.lower()

    if suffix in (".rds", ".h5ad", ".h5"):
        parquet_path = input_path.with_suffix(".scryo.parquet")
        if (
            parquet_path.exists()
            and not force_extract
            and parquet_path.stat().st_mtime >= input_path.stat().st_mtime
        ):
            logger.info("Using cached parquet: %s", parquet_path)
        elif suffix == ".rds":
            from scryo.extract.seurat import extract_seurat

            extract_seurat(input_path, parquet_path)
        elif suffix == ".h5ad":
            from scryo.extract.h5ad import extract_h5ad

            extract_h5ad(input_path, parquet_path)
        else:
            from scryo.extract.tenx import extract_h5

            extract_h5(input_path, parquet_path, analysis_path=analysis_path)
    elif suffix in (".parquet", ".pq"):
        parquet_path = input_path
    else:
        raise click.BadParameter(
            f"Unsupported file format: {suffix}. Use .rds, .h5ad, .h5, or .parquet",
            param_hint="INPUT_PATH",
        )

    _launch_server(parquet_path, host=host, port=port, duckdb_mode=duckdb_mode)


def _launch_server(
    parquet_path: Path,
    *,
    host: str,
    port: int,
    duckdb_mode: str,
) -> None:
    """Launch the scryo visualization server."""
    import uvicorn

    from scryo.server.app import create_scryo_server

    app, host, port = create_scryo_server(
        parquet_path,
        duckdb_mode=duckdb_mode,
        host=host,
        port=port,
    )

    click.echo()
    click.echo(click.style("-" * 60, dim=True))
    click.echo()
    click.echo(f"  {click.style('scryo', fg='green', bold=True)}  v{__version__}")
    click.echo()
    click.echo(f"  Data:   {click.style(str(parquet_path), dim=True)}")
    click.echo(f"  DuckDB: {click.style(duckdb_mode, fg='cyan')}")
    click.echo(f"  URL:    {click.style(f'http://{host}:{port}', fg='cyan', bold=True)}")
    click.echo()
    click.echo(click.style("  Press CTRL+C to quit", dim=True))
    click.echo()
    click.echo(click.style("-" * 60, dim=True))

    uvicorn.run(app, host=host, port=port, access_log=False, log_level=logging.ERROR)


if __name__ == "__main__":
    main()
