"""scryo server — self-contained FastAPI app for single-cell visualization."""

import asyncio
import concurrent.futures
import json
import logging
import re
from collections.abc import Callable
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

STATIC_PATH = str((Path(__file__).parent.parent / "static").resolve())


def _to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    df.to_parquet(buf)
    return buf.getvalue()


def _arrow_to_bytes(arrow: pa.Table | pa.RecordBatchReader) -> bytes:
    sink = pa.BufferOutputStream()
    if isinstance(arrow, pa.Table):
        with pa.ipc.new_stream(sink, arrow.schema) as writer:
            writer.write(arrow)
    else:
        with pa.ipc.new_stream(sink, arrow.schema) as writer:
            for batch in arrow:
                writer.write_batch(batch)
    return sink.getvalue().to_pybytes()


def _mount_bytes(
    app: FastAPI, url: str, media_type: str, make_content: Callable[[], bytes]
) -> None:
    @lru_cache(maxsize=1)
    def get_content() -> bytes:
        return make_content()

    def _parse_range(request: Request, content_length: int) -> tuple[int, int] | None:
        value = request.headers.get("Range")
        if value is not None:
            m = re.match(r"^ *bytes *= *([0-9]+) *- *([0-9]+) *$", value)
            if m is not None:
                r0, r1 = int(m.group(1)), int(m.group(2)) + 1
                if r0 < r1 <= content_length:
                    return (r0, r1)
        return None

    @app.head(url)
    async def head(request: Request) -> Response:
        content = get_content()
        rng = _parse_range(request, len(content))
        length = (rng[1] - rng[0]) if rng else len(content)
        return Response(headers={"Content-Length": str(length), "Content-Type": media_type})

    @app.get(url)
    async def get(request: Request) -> Response:
        content = get_content()
        rng = _parse_range(request, len(content))
        if rng is None:
            return Response(content=content)
        r0, r1 = rng
        return Response(
            content=content[r0:r1],
            headers={
                "Content-Length": str(r1 - r0),
                "Content-Range": f"bytes {r0}-{r1 - 1}/{len(content)}",
                "Content-Type": media_type,
            },
            media_type=media_type,
            status_code=206,
        )


def detect_reductions(columns: list[str]) -> dict[str, tuple[str, str]]:
    """Detect DimReduc coordinate pairs from column names."""
    reductions: dict[str, tuple[str, str]] = {}
    suffix_1 = [c for c in columns if c.endswith("_1")]
    for col in suffix_1:
        base = col[:-2]
        col_2 = f"{base}_2"
        if col_2 in columns:
            reductions[base] = (col, col_2)
    return reductions


def detect_default_reduction(reductions: dict[str, tuple[str, str]]) -> str | None:
    """Pick the best default reduction. Prefer UMAP > tSNE > PCA > first available."""
    priorities = ["umap", "tsne", "pca"]
    names_lower = {name.lower(): name for name in reductions}
    for priority in priorities:
        for lower_name, original_name in names_lower.items():
            if priority in lower_name:
                return original_name
    return next(iter(reductions), None)


def _get_parquet_columns(parquet_path: Path) -> list[str]:
    """Get column names from parquet without loading data."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(parquet_path)
    return pf.schema_arrow.names


def create_scryo_server(
    parquet_path: Path,
    *,
    duckdb_mode: str = "server",
    host: str = "0.0.0.0",
    port: int = 8050,
) -> tuple:
    """Create the scryo FastAPI application.

    Args:
        parquet_path: Path to the extracted parquet file.
        duckdb_mode: "wasm" or "server".
        host: Server host.
        port: Server port.

    Returns:
        Tuple of (FastAPI app, host, port).
    """
    logger.info("Reading parquet schema: %s", parquet_path)
    all_columns = _get_parquet_columns(parquet_path)
    logger.info("  %d total columns", len(all_columns))

    reductions = detect_reductions(all_columns)
    if not reductions:
        raise ValueError("No DimReduc coordinate pairs found in parquet columns")

    default_reduc = detect_default_reduction(reductions)
    if default_reduc is None:
        raise ValueError("No DimReduc coordinate pairs found in parquet columns")
    x_col, y_col = reductions[default_reduc]
    logger.info("Default reduction: %s (%s, %s)", default_reduc, x_col, y_col)
    logger.info("Available reductions: %s", list(reductions.keys()))

    gene_suffixes = ("_RNA", "_SCT")
    meta_columns = [c for c in all_columns if not any(c.endswith(s) for s in gene_suffixes)]
    gene_columns = [c for c in all_columns if any(c.endswith(s) for s in gene_suffixes)]
    logger.info(
        "  %d metadata/reduc columns, %d gene columns",
        len(meta_columns),
        len(gene_columns),
    )

    logger.info("Loading metadata columns...")
    df = pd.read_parquet(parquet_path, columns=meta_columns)

    # DuckDB interprets dots as struct access, so rename dotted columns.
    rename_map = {c: c.replace(".", "_") for c in df.columns if "." in c}
    if rename_map:
        df = df.rename(columns=rename_map)
        meta_columns = [rename_map.get(c, c) for c in meta_columns]
        reductions = {
            rename_map.get(k, k): (rename_map.get(v[0], v[0]), rename_map.get(v[1], v[1]))
            for k, v in reductions.items()
        }
        default_reduc = rename_map.get(default_reduc, default_reduc)
        x_col = rename_map.get(x_col, x_col)
        y_col = rename_map.get(y_col, y_col)
        logger.info("Renamed %d dotted columns", len(rename_map))

    logger.info("Loaded: %d cells x %d columns", len(df), len(df.columns))

    df["__row_index__"] = range(len(df))
    df["__rowid"] = range(len(df))

    props: dict = {
        "data": {
            "id": "__row_index__",
            "projection": {"x": x_col, "y": y_col},
        },
        "initialState": {
            "layoutStates": {
                "list": {"showTable": False, "showCharts": False, "showEmbedding": True}
            }
        },
    }
    metadata = {"props": props}

    app = _make_scryo_server(
        df=df,
        metadata=metadata,
        parquet_path=parquet_path,
        reductions=reductions,
        default_reduc=default_reduc,
    )

    return app, host, port


def _make_scryo_server(
    *,
    df: pd.DataFrame,
    metadata: dict,
    parquet_path: Path,
    reductions: dict[str, tuple[str, str]],
    default_reduc: str,
) -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    _mount_bytes(
        app,
        "/data/dataset.parquet",
        "application/octet-stream",
        lambda: _to_parquet_bytes(df),
    )

    @app.get("/data/metadata.json")
    async def get_metadata() -> dict:
        return {**metadata, "database": {"type": "rest"}}

    @app.get("/data/scryo/reductions")
    async def get_reductions() -> dict:
        return {
            "reductions": {name: {"x": xy[0], "y": xy[1]} for name, xy in reductions.items()},
            "default": default_reduc,
        }

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE dataset AS SELECT * FROM df")
    executor = concurrent.futures.ThreadPoolExecutor()

    def handle_query(query: dict) -> Response:
        sql = query["sql"]
        command = query["type"]
        with con.cursor() as cursor:
            try:
                result = cursor.execute(sql)
                if command == "exec":
                    return JSONResponse({})
                elif command == "arrow":
                    buf = _arrow_to_bytes(result.arrow())
                    return Response(buf, headers={"Content-Type": "application/octet-stream"})
                elif command == "json":
                    data = result.df().to_json(orient="records")
                    return Response(data, headers={"Content-Type": "application/json"})
                else:
                    raise ValueError(f"Unknown command {command}")
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/data/query")
    async def get_query(req: Request) -> Response:
        data = json.loads(req.query_params["query"])
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: handle_query(data))

    @app.post("/data/query")
    async def post_query(req: Request) -> Response:
        body = await req.body()
        data = json.loads(body)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: handle_query(data))

    all_parquet_columns = _get_parquet_columns(parquet_path)
    gene_suffixes = ("_RNA", "_SCT")
    gene_columns_all = [
        c for c in all_parquet_columns if any(c.endswith(s) for s in gene_suffixes)
    ]
    gene_names_unique = sorted({c.rsplit("_", 1)[0] for c in gene_columns_all})
    available_assays = sorted({c.rsplit("_", 1)[1] for c in gene_columns_all if "_" in c})
    loaded_columns: set[str] = set()

    @app.get("/data/scryo/genes")
    async def get_genes() -> dict:
        return {
            "genes": gene_names_unique,
            "assays": available_assays,
            "count": len(gene_names_unique),
        }

    @app.post("/data/scryo/load-column")
    async def load_column(req: Request) -> Response:
        body = await req.json()
        column = body.get("column")
        if not column or column not in gene_columns_all:
            return JSONResponse({"error": f"Unknown column: {column}"}, status_code=400)
        if column in loaded_columns:
            return JSONResponse({"status": "already_loaded", "column": column})

        def _load() -> None:
            import pyarrow.parquet as pq

            table = pq.read_table(str(parquet_path), columns=[column])
            with con.cursor() as cursor:
                cursor.execute(
                    f'ALTER TABLE dataset ADD COLUMN IF NOT EXISTS "{column}" DOUBLE DEFAULT 0'
                )
                cursor.register("_gene_arrow", table)
                cursor.execute(f"""
                    UPDATE dataset SET "{column}" = g."{column}"
                    FROM (SELECT "{column}", row_number() OVER () - 1 AS __rid FROM _gene_arrow) g
                    WHERE dataset.__rowid = g.__rid
                """)
                cursor.execute("DROP VIEW IF EXISTS _gene_arrow")
            loaded_columns.add(column)

        await asyncio.get_running_loop().run_in_executor(executor, _load)
        return JSONResponse({"status": "loaded", "column": column})

    _cache: dict[str, object] = {}

    @app.post("/data/cache/{name}")
    async def post_cache(request: Request, name: str) -> None:
        _cache[name] = await request.json()

    @app.get("/data/cache/{name}")
    async def get_cache(name: str) -> Response:
        if name not in _cache:
            return Response(status_code=404)
        return JSONResponse(_cache[name])

    app.mount("/", StaticFiles(directory=STATIC_PATH, html=True))

    return app
