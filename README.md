# scryo

scryo is a command line tool which takes a single-cell object (Seurat `.rds`, AnnData `.h5ad`, or 10x Cell Ranger `.h5`) into an interactive scatter plot in your browser.

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

# With h5ad / AnnData support
pip install 'scryo[h5ad] @ git+ssh://git@github.com/saketlab/scryo.git'
```

You also need:

- Python 3.11+
- For `.rds` input: R with `Seurat`, `arrow`, and `Matrix`: `install.packages(c("Seurat", "arrow", "Matrix"))`
- For `.h5ad` input: install scryo with the `[h5ad]` extra (pulls in `anndata`)
- For 10x `.h5` input: also pass `--analysis path/to/<dataset>_analysis.tar.gz` (or place an `<dataset>_analysis.tar.gz` next to the h5) — Cell Ranger's `.h5` file holds only the count matrix; scryo reads reductions/clusters from the analysis tarball it ships alongside

## Usage

```bash
scryo /path/to/data.rds                  # extract + serve
scryo /path/to/data.h5ad                 # AnnData h5ad input
scryo /path/to/data.h5 \                 # 10x Cell Ranger h5 + sidecar analysis
      --analysis /path/to/data_analysis.tar.gz
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

scryo caches the extracted parquet next to the input file and re-extracts only when the source `.rds` / `.h5ad` / `.h5` is newer.


## Acknowledgements

scryo is built on [Embedding Atlas](https://github.com/apple/embedding-atlas) (MIT, Apple Inc.). See [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md).
