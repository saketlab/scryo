#!/usr/bin/env Rscript
# scryo: Extract a Seurat RDS to a metadata parquet + a gene-major sparse sidecar.
#
# Usage: Rscript extract_seurat.R <input.rds> <output.parquet>
#
# Writes:
#   <output.parquet>            metadata + reduction columns (one row per cell)
#   <output>.genes/             gene-major sparse expression sidecar:
#       genes.txt               gene names, in column order
#       indptr.bin   int32      length n_genes+1   (CSC col pointers, gene-major)
#       indices.bin  int32      length nnz         (cell row ids)
#       data.bin     float64    length nnz         (expression values)
#       info.json                {n_cells, n_genes, nnz, assay, value_dtype}
#
# Design: the expression matrix at single-cell scale is ~97% zeros, so we never
# densify it. We transpose to cells x genes (CSC => each gene is one contiguous
# column) and dump the raw slots. The server memory-maps these and reads one
# gene's slice on demand -- O(nnz_in_gene), no dense matrix anywhere.
#
# Progress markers (consumed by seurat.py to drive progress bars) are printed to
# stdout as `@@SCRYO <event> <payload>` and flushed immediately.

suppressPackageStartupMessages({
  library(Seurat)
  library(arrow)
  library(Matrix)
})

emit <- function(event, ...) {
  cat(sprintf("@@SCRYO %s %s\n", event, paste0(..., collapse = " ")))
  flush(stdout())
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop("Usage: Rscript extract_seurat.R <input.rds> <output.parquet>")
}
rds_path <- args[1]
out_parquet <- args[2]
sidecar <- paste0(tools::file_path_sans_ext(out_parquet, compression = FALSE), ".genes")
if (grepl("\\.parquet$", out_parquet)) {
  sidecar <- sub("\\.parquet$", ".genes", out_parquet)
}
dir.create(sidecar, recursive = TRUE, showWarnings = FALSE)

emit("READRDS_START", rds_path)
t0 <- Sys.time()
obj <- readRDS(rds_path)
emit("READRDS_DONE", round(as.numeric(Sys.time() - t0, units = "secs"), 1))

assay_name <- DefaultAssay(obj)
if (!assay_name %in% Assays(obj)) assay_name <- Assays(obj)[1]
emit("INFO", "assay", assay_name)

# Join split data layers if this is a Seurat v5 object with per-batch layers.
assay_obj <- obj[[assay_name]]
data_layers <- grep("^data", Layers(assay_obj), value = TRUE)
if (length(data_layers) > 1) {
  emit("INFO", "joining", length(data_layers), "data layers")
  obj[[assay_name]] <- JoinLayers(assay_obj)
}

# --- metadata + reductions -------------------------------------------------
emit("META_START")
meta <- obj@meta.data
cell_ids_meta <- rownames(meta)

# Drop barcode-like columns (mostly values ending in -<digits>).
for (col in colnames(meta)) {
  vals <- meta[[col]]
  if (is.character(vals) || is.factor(vals)) {
    vals <- as.character(vals)
    if (mean(grepl("-1$", vals, perl = TRUE), na.rm = TRUE) > 0.5) {
      emit("INFO", "drop barcode column", col)
      meta[[col]] <- NULL
    }
  }
}

# Grab the expression matrix now so we can free the rest of the object.
expr <- tryCatch(
  LayerData(obj, assay = assay_name, layer = "data"),
  error = function(e) GetAssayData(obj, assay = assay_name, slot = "data")
)
if (!inherits(expr, "dgCMatrix")) expr <- as(expr, "dgCMatrix")
expr_cells <- colnames(expr)
gene_names <- rownames(expr)

# Add reduction coordinates (first 2 dims) keyed by the EXPR cell order.
for (reduc_name in Reductions(obj)) {
  emb <- Embeddings(obj, reduction = reduc_name)
  for (d in 1:min(ncol(emb), 2)) {
    meta[[paste0(reduc_name, "_", d)]] <- as.numeric(emb[expr_cells, d])
  }
  emit("INFO", "reduction", reduc_name)
}

# Align metadata rows to the expression column order, then drop the object.
ord <- match(expr_cells, cell_ids_meta)
meta <- meta[ord, , drop = FALSE]
rownames(meta) <- NULL
rm(obj); gc(verbose = FALSE)

write_parquet(meta, out_parquet, compression = "zstd")
emit("META_DONE", out_parquet, nrow(meta), "cells", ncol(meta), "cols")

# --- gene-major sparse sidecar ---------------------------------------------
# t(expr): genes x cells (CSC) -> cells x genes (CSC), so each gene is one
# contiguous column. nnz is unchanged and <= .Machine$integer.max because the
# input was a valid dgCMatrix, so the int32 @p/@i slots stay valid.
emit("TRANSPOSE_START", length(gene_names), "genes", length(expr_cells), "cells")
texpr <- t(expr)
rm(expr); gc(verbose = FALSE)

nnz <- length(texpr@x)
n_genes <- length(gene_names)
n_cells <- length(expr_cells)
emit("TRANSPOSE_DONE", "nnz", nnz)

writeLines(gene_names, file.path(sidecar, "genes.txt"))
writeLines(expr_cells, file.path(sidecar, "cells.txt"))

# indptr (small): gene-major column pointers.
con <- file(file.path(sidecar, "indptr.bin"), "wb")
writeBin(as.integer(texpr@p), con, size = 4L)
close(con)

# Helper: write a long vector to a binary file in segments, emitting progress.
write_binary_chunked <- function(vec, path, size, label, n_segments = 40L) {
  con <- file(path, "wb")
  on.exit(close(con))
  total <- length(vec)
  if (total == 0) return(invisible())
  seg <- ceiling(total / n_segments)
  done <- 0
  while (done < total) {
    hi <- min(done + seg, total)
    writeBin(vec[(done + 1):hi], con, size = size)
    done <- hi
    emit("WRITE_PROGRESS", label, done, total)
  }
}

emit("WRITE_START", "indices")
write_binary_chunked(texpr@i, file.path(sidecar, "indices.bin"), 4L, "indices")
emit("WRITE_START", "data")
write_binary_chunked(texpr@x, file.path(sidecar, "data.bin"), 8L, "data")

info <- sprintf(
  '{"n_cells": %d, "n_genes": %d, "nnz": %.0f, "assay": "%s", "value_dtype": "float64", "index_dtype": "int32", "indptr_dtype": "int32"}',
  n_cells, n_genes, nnz, assay_name
)
writeLines(info, file.path(sidecar, "info.json"))

emit("ALL_DONE", sidecar)
