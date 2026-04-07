# scryo

scryo is a command line tool which takes a single-cell object (rds) into an interactive scatter plot in your browser.

Built on [Embedding Atlas](https://apple.github.io/embedding-atlas/) (Apple).

It comes packaged with everything that you need to get started in one command.

## Install


```bash
# SSH
pip install git+ssh://git@github.com/saketlab/scryo.git

# HTTPS with a personal access token (PAT needs the `repo` scope)
pip install git+https://<TOKEN>@github.com/saketlab/scryo.git

# Pin a tag, branch, or commit
pip install git+ssh://git@github.com/saketlab/scryo.git@v0.1.0
```

You also need:

- Python 3.11+
- R with `Seurat`, `arrow`, and `Matrix`: `install.packages(c("Seurat", "arrow", "Matrix"))`

## Usage

```bash
scryo /path/to/data.rds                  # extract + serve
scryo /path/to/data.parquet              # skip extraction
scryo data.rds --port 8080
scryo data.rds --duckdb wasm             # in-browser DuckDB
scryo data.rds --duckdb server           # default; lazy gene loading
```

The viewer opens at `http://0.0.0.0:8050`. 

Features:

- Type-ahead gene search with multiasasy assay toggle
- Reduction selector
- WebGPU rendering with density downsampling 

scryo caches the extracted parquet next to the input `.rds` and re-extracts only when the `.rds` is newer.


## Acknowledgements

scryo is built on [Embedding Atlas](https://github.com/apple/embedding-atlas) (MIT, Apple Inc.). See [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md).
