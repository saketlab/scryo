#!/usr/bin/env Rscript
# scryo: Extract Seurat RDS to parquet
# Usage: Rscript extract_seurat.R <input.rds> <output_dir>
# Writes: output_dir/metadata.parquet, output_dir/RNA.parquet, output_dir/SCT.parquet

suppressPackageStartupMessages({
  library(Seurat)
  library(arrow)
  library(Matrix)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop("Usage: Rscript extract_seurat.R <input.rds> <output_dir>")
}

rds_path <- args[1]
output_dir <- args[2]
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

cat("Loading Seurat object:", rds_path, "\n")
obj <- readRDS(rds_path)

# Join layers if Seurat v5
for (assay_name in Assays(obj)) {
  assay_obj <- obj[[assay_name]]
  layer_names <- Layers(assay_obj)
  data_layers <- layer_names[grepl("^data", layer_names)]
  if (length(data_layers) > 1) {
    cat("Joining", length(data_layers), "layers in", assay_name, "assay...\n")
    obj[[assay_name]] <- JoinLayers(assay_obj)
  }
}

# --- Metadata + DimReducs ---
cat("Extracting metadata...\n")
meta <- obj@meta.data
n_cells <- nrow(meta)
cell_ids <- rownames(meta)
cat("  Cells:", n_cells, "\n")

# Remove barcode columns
for (col in colnames(meta)) {
  vals <- as.character(meta[[col]])
  if (is.character(vals) && mean(grepl("-1$", vals, perl = TRUE), na.rm = TRUE) > 0.5) {
    cat("  Removing barcode column:", col, "\n")
    meta[[col]] <- NULL
  }
}

# DimReducs
cat("Extracting reductions...\n")
for (reduc_name in Reductions(obj)) {
  embeddings <- Embeddings(obj, reduction = reduc_name)
  n_dims <- min(ncol(embeddings), 2)
  for (d in 1:n_dims) {
    meta[[paste0(reduc_name, "_", d)]] <- as.numeric(embeddings[cell_ids, d])
  }
  cat("  Added:", reduc_name, "\n")
}

# Sort
celltype_col <- NULL
for (candidate in c("azimuth", "celltype", "cell_type", "CellType", "cluster", "seurat_clusters")) {
  matches <- grep(candidate, colnames(meta), ignore.case = TRUE, value = TRUE)
  if (length(matches) > 0) { celltype_col <- matches[1]; break }
}
if (!is.null(celltype_col) && "nCount_RNA" %in% colnames(meta)) {
  cat("  Sorting by", celltype_col, "then nCount_RNA\n")
  sort_order <- order(meta[[celltype_col]], meta[["nCount_RNA"]])
} else {
  sort_order <- seq_len(n_cells)
}
cell_ids <- cell_ids[sort_order]
meta <- meta[sort_order, , drop = FALSE]
rownames(meta) <- NULL

# Write metadata
meta_path <- file.path(output_dir, "metadata.parquet")
cat("Writing metadata:", meta_path, "\n")
write_parquet(meta, meta_path, compression = "zstd")

# --- Gene expression: write as sparse triplets ---
assays_to_extract <- intersect(c("RNA", "SCT"), Assays(obj))

for (assay_name in assays_to_extract) {
  cat("Extracting", assay_name, "...\n")

  expr_data <- tryCatch({
    LayerData(obj, assay = assay_name, layer = "data")
  }, error = function(e) {
    GetAssayData(obj, assay = assay_name, slot = "data")
  })

  # Convert to dgCMatrix (CSC) if not already
  if (!inherits(expr_data, "dgCMatrix")) {
    expr_data <- as(expr_data, "dgCMatrix")
  }

  gene_names <- rownames(expr_data)
  expr_cells <- colnames(expr_data)
  cat("  Genes:", length(gene_names), "Cells:", length(expr_cells), "\n")

  # Map cell_ids to expression matrix columns
  cell_map <- match(cell_ids, expr_cells)
  valid_mask <- !is.na(cell_map)
  valid_map <- cell_map[valid_mask]
  cat("  Cells with data:", sum(valid_mask), "/", length(cell_ids), "\n")

  # Reorder columns to match sorted cell order
  expr_data <- expr_data[, valid_map, drop = FALSE]
  gc(verbose = FALSE)

  # Write in chunks of 2000 genes as parquet files
  chunk_size <- 2000
  n_genes <- length(gene_names)
  n_chunks <- ceiling(n_genes / chunk_size)

  for (chunk_idx in seq_len(n_chunks)) {
    start <- (chunk_idx - 1) * chunk_size + 1
    end <- min(chunk_idx * chunk_size, n_genes)
    chunk_genes <- gene_names[start:end]

    # Extract chunk, convert to dense, transpose to cells x genes
    chunk_mat <- as.matrix(t(expr_data[chunk_genes, , drop = FALSE]))

    # Create data frame with proper column names
    chunk_df <- as.data.frame(chunk_mat)
    colnames(chunk_df) <- paste0(chunk_genes, "_", assay_name)

    # Pad rows if not all cells have data
    if (sum(valid_mask) < length(cell_ids)) {
      full_df <- as.data.frame(matrix(0, nrow = length(cell_ids), ncol = ncol(chunk_df)))
      colnames(full_df) <- colnames(chunk_df)
      full_df[valid_mask, ] <- chunk_df
      chunk_df <- full_df
    }

    chunk_path <- file.path(output_dir, sprintf("%s_chunk_%03d.parquet", assay_name, chunk_idx))
    write_parquet(chunk_df, chunk_path, compression = "zstd", compression_level = 3)

    cat("  Chunk", chunk_idx, "/", n_chunks, ":", length(chunk_genes), "genes written\n")
    rm(chunk_mat, chunk_df)
    gc(verbose = FALSE)
  }

  rm(expr_data)
  gc(verbose = FALSE)
  cat("  Done:", assay_name, "\n")
}

cat("All done! Output in:", output_dir, "\n")
