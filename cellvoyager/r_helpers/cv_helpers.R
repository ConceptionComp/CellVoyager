# cv_helpers.R — curated single-cell house helpers for CellVoyager runs.
#
# Faithful, lightly-adapted ports of the in-memory `cell_data_set` helpers from
# the Conception `10X_merge` repo (ConceptionComp/10X_merge), file
# `10X_merge_utils.R` (last touched commit 28cfb5e; repo HEAD 1b01136). Only the
# helpers that operate on an already-loaded cds and depend on packages available
# in the CellVoyager-r env are included here; the GCS / Postgres-explorer / atlas
# ingestion functions are intentionally omitted (they need Seurat / RPostgres /
# googleCloudStorageR / gargle and target Conception-internal infrastructure).
#
# Adaptations from the originals (kept minimal):
#   - assertthat::assert_that(...) -> if (...) stop(...)   (drops the assertthat dep)
#   - rlang::is_null(...)          -> is.null(...)
#   - gene/cell subsetting that relied on a rowData$id column now uses logical
#     row masks / rownames, so the helpers work on any cds (not just 10X_merge's).
#   - detectQC()'s mitochondrial / Y-chromosome tallies are guarded so they yield
#     0 (rather than erroring) when no matching gene symbols are present, and
#     min_expr defaults to 1 (the value every original caller passed).
#
# Dependencies are limited to the packages whitelisted for generated analyses:
#   monocle3, SingleCellExperiment, Matrix, ggplot2, patchwork
# (get_avg_expr additionally calls MatrixGenerics::rowMedians, available in the
# env; it is only needed when that function is actually called).
#
# These functions are sourced into the live R session by the legacy executor so
# generated analysis cells can call them directly. Loading is wrapped so a load
# failure logs but does not abort the run.

suppressPackageStartupMessages({
  library(monocle3)
  library(SingleCellExperiment)
  library(Matrix)
  library(ggplot2)
  library(patchwork)
})

# --- QC -----------------------------------------------------------------------

# detectQC(cds, min_expr = 1): annotate per-gene and per-cell QC metrics.
# Adds rowData$num_cells_expressed and colData {num_genes_expressed, totalUMI,
# mitocounts, mitoload, ysums, yload}. "Expressed" means count > min_expr,
# matching the original (min_expr = 1). Mito / Y tallies match human + mouse gene
# symbols and are 0 when none are present.
detectQC <- function(cds, min_expr = 1) {
  m <- exprs(cds)
  SummarizedExperiment::rowData(cds)$num_cells_expressed <- Matrix::rowSums(m > min_expr)
  SummarizedExperiment::colData(cds)$num_genes_expressed <- Matrix::colSums(m > min_expr)
  SummarizedExperiment::colData(cds)$totalUMI <- Matrix::colSums(m)

  gs <- SummarizedExperiment::rowData(cds)$gene_short_name

  # mitochondrial load
  mt_rows <- if (!is.null(gs)) grepl("MT-|mt-", gs) else rep(FALSE, nrow(cds))
  mitocounts <- if (any(mt_rows)) Matrix::colSums(m[mt_rows, , drop = FALSE]) else 0
  SummarizedExperiment::colData(cds)$mitocounts <- mitocounts
  SummarizedExperiment::colData(cds)$mitoload <-
    (mitocounts / SummarizedExperiment::colData(cds)$totalUMI) * 100

  # Y-chromosome load (human + mouse symbols)
  y_symbols <- c(
    "DDX3Y", "SRY", "RPS4Y1", "EIF1AY", "ZFY",
    "Eif2s3y", "Kdm5d", "Uty", "Usp9y", "Zfy", "Ddx3y", "Sry"
  )
  y_rows <- if (!is.null(gs)) gs %in% y_symbols else rep(FALSE, nrow(cds))
  ysums <- if (any(y_rows)) Matrix::colSums(m[y_rows, , drop = FALSE]) else 0
  SummarizedExperiment::colData(cds)$ysums <- ysums
  SummarizedExperiment::colData(cds)$yload <-
    (ysums / SummarizedExperiment::colData(cds)$totalUMI) * 100

  cds
}

# filter_cds_cells(cds, ...): drop low-quality cells by gene count, mito load, and
# total UMI, with an optional upper-UMI (doublet-ish) bound at mean + 2 SD on the
# log10 scale. Recomputes QC with detectQC(cds, 1) if needed.
filter_cds_cells <- function(cds,
                             num_genes_threshold = 300,
                             mitoload_thresh = 20,
                             totalUMI_thresh = 2500,
                             upperUMI_threshold = TRUE) {
  cd <- SummarizedExperiment::colData(cds)
  if (!all(c("num_genes_expressed", "mitoload", "totalUMI") %in% colnames(cd)) ||
    any(is.na(cd$num_genes_expressed))) {
    cds <- detectQC(cds, 1)
  }

  QC_check_dat <- as.data.frame(SummarizedExperiment::colData(cds))
  cells_to_keep <- QC_check_dat[which(
    QC_check_dat$num_genes_expressed > num_genes_threshold &
      QC_check_dat$mitoload < mitoload_thresh &
      QC_check_dat$totalUMI > totalUMI_thresh
  ), ]

  if (upperUMI_threshold) {
    upper_bound <- 10^(mean(log10(cells_to_keep$totalUMI)) + 2 * sd(log10(cells_to_keep$totalUMI)))
    cells_to_keep <- cells_to_keep[cells_to_keep$totalUMI < upper_bound, ]
  }

  cds[, rownames(cells_to_keep)]
}

# filter_cds_genes(cds, num_cells_expressed_cutoff = 100): drop genes detected in
# fewer than (strictly) `num_cells_expressed_cutoff` cells. Besides removing
# uninformative genes, this materially improves fit_models() convergence (per-gene
# GLMs on near-all-zero genes are what produce non-convergence / missing-term
# errors). Use a small cutoff (e.g. 10) when subsetting to few cells.
filter_cds_genes <- function(cds, num_cells_expressed_cutoff = 100) {
  if (!"num_cells_expressed" %in% colnames(SummarizedExperiment::rowData(cds))) {
    cds <- detectQC(cds, 1)
  }
  keep <- SummarizedExperiment::rowData(cds)$num_cells_expressed > num_cells_expressed_cutoff
  keep[is.na(keep)] <- FALSE
  cds[keep, ]
}

# get_avg_expr(cds): annotate rowData with per-gene mean (mean_exp) and median
# (med_exp) expression across all cells.
get_avg_expr <- function(cds) {
  m <- exprs(cds)
  SummarizedExperiment::rowData(cds)$mean_exp <- Matrix::rowMeans(m)
  SummarizedExperiment::rowData(cds)$med_exp <- MatrixGenerics::rowMedians(m)
  cds
}

# --- downsampling -------------------------------------------------------------

# downsample_cds_bysample(downsample_pct, cds): keep `downsample_pct` of cells
# from each `sample`, giving every sample an equal sampling rate. Requires a
# `sample` column in colData. Seeded for reproducibility.
downsample_cds_bysample <- function(downsample_pct, cds) {
  if (!"sample" %in% colnames(SummarizedExperiment::colData(cds))) {
    stop("Missing required column 'sample' in colData")
  }
  df <- as.data.frame(SummarizedExperiment::colData(cds))
  df$Row.names <- rownames(df)
  unique_samples <- unique(df$sample)

  barcodes <- vector()
  for (s in unique_samples) {
    tmp_barcodes <- df[df$sample == s, ]$Row.names
    select_size <- floor(length(tmp_barcodes) * downsample_pct)
    set.seed(100)
    barcodes <- append(barcodes, sample(tmp_barcodes, size = select_size))
  }

  cds[, df$Row.names %in% barcodes]
}

# downsample_cds_bysample_bycluster(downsample_pct, cds): keep `downsample_pct` of
# cells from each (cluster, sample) pair, so each sample contributes equally
# within every cluster. Requires `sample` and `clusters` columns in colData.
# Returns a fresh cds carrying counts, colData, rowData, and the UMAP embedding.
downsample_cds_bysample_bycluster <- function(downsample_pct, cds) {
  if (!all(c("sample", "clusters") %in% colnames(SummarizedExperiment::colData(cds)))) {
    stop("Missing one or more required columns: must have fields called 'sample' and 'clusters' in colData")
  }

  df <- as.data.frame(SummarizedExperiment::colData(cds))
  df$Row.names <- rownames(df)
  unique_pairs <- unique(df[c("clusters", "sample")])

  barcodes <- vector()
  for (row in seq_len(nrow(unique_pairs))) {
    filter_label <- unique_pairs[row, "clusters"]
    filter_sample <- unique_pairs[row, "sample"]
    tmp_barcodes <- df[df$clusters == filter_label & df$sample == filter_sample, ]$Row.names
    select_size <- floor(length(tmp_barcodes) * downsample_pct)
    set.seed(100)
    barcodes <- append(barcodes, sample(tmp_barcodes, size = select_size))
  }

  cds_small <- cds[, df$Row.names %in% barcodes]
  final_cds_small <- new_cell_data_set(
    counts(cds_small),
    cell_metadata = SummarizedExperiment::colData(cds_small),
    gene_metadata = SummarizedExperiment::rowData(cds_small)
  )
  reducedDim(final_cds_small, "UMAP") <- reducedDim(cds_small, "UMAP")
  final_cds_small
}

# --- color palette ------------------------------------------------------------

# darken_color(hex_color, factor = 0.7): return a darkened version of a hex color.
darken_color <- function(hex_color, factor = 0.7) {
  rgb_vals <- col2rgb(hex_color)
  rgb(
    rgb_vals[1] * factor / 255,
    rgb_vals[2] * factor / 255,
    rgb_vals[3] * factor / 255
  )
}

# get_colors(num_colors): a colorblind-friendly categorical palette of up to 20
# distinct hues; beyond 20 it recycles the palette in progressively darker shades.
get_colors <- function(num_colors) {
  gene_set_colors <- c(
    "#0173B2", "#DE8F05", "#029E73", "#CC78BC", "#ECE133",
    "#56B4E9", "#CA9161", "#D55E00", "#949494", "#882255",
    "#EE9999", "#DDCC77", "#AA4499", "#117733", "#332288",
    "#999933", "#44AA99", "#CC6677", "#661100", "#6699CC"
  )

  if (num_colors == 0) {
    stop("Must request at least 1 color.")
  }
  if (num_colors > 0 & num_colors <= 20) {
    return(gene_set_colors[1:num_colors])
  }

  full_cycles <- (num_colors - 1) %/% 20
  remainder <- num_colors %% 20
  selected_colors <- gene_set_colors
  for (cycle in 1:full_cycles) {
    darkening_factor <- 0.7^cycle
    darkened <- unname(sapply(gene_set_colors, darken_color, factor = darkening_factor))
    selected_colors <- c(selected_colors, darkened)
  }
  if (remainder > 0) {
    darkening_factor <- 0.7^(full_cycles + 1)
    darkened <- unname(sapply(gene_set_colors[1:remainder], darken_color, factor = darkening_factor))
    selected_colors <- c(selected_colors, darkened)
  }
  selected_colors[1:num_colors]
}

# --- flexible expression plots ------------------------------------------------

# feature_plot_flexible(cds_object, gene, facetby = NULL): single-gene expression
# on the existing embedding via plot_cells, using log-normalized values with
# scale_to_range = FALSE (absolute, comparable color scale) and a small min_expr
# floor. Optionally facet by a colData column. Returns a ggplot object.
feature_plot_flexible <- function(cds_object, gene, facetby = NULL) {
  fp <- plot_cells(cds_object,
    cell_size = 0.5, genes = gene,
    norm_method = "log", scale_to_range = FALSE,
    min_expr = 0.1, show_trajectory_graph = FALSE
  ) +
    theme_bw() +
    theme(
      legend.position = "bottom",
      legend.text = element_text(angle = 60, vjust = 0.7),
      strip.text.x = element_text(size = 8)
    ) +
    ggtitle(gene)
  if (!is.null(facetby)) {
    if (!facetby %in% colnames(SummarizedExperiment::colData(cds_object))) {
      stop(paste0(facetby, " is not a column in colData(cds)."))
    }
    fp <- fp + facet_wrap(facetby)
  }
  fp
}

# plot_genes_flexible(cds_object, genes, facetbygene = TRUE, facetby = NULL):
# print expression plots for several genes — by default 3 per row (faceted by
# gene), or one plot per gene faceted by a colData variable. Genes absent from the
# cds are dropped with a warning. Prints the plots (side effect); returns NULL.
plot_genes_flexible <- function(cds_object, genes, facetbygene = TRUE, facetby = NULL) {
  if (!is.null(facetby)) {
    facetbygene <- FALSE
  }

  missing_genes <- genes[!genes %in% SummarizedExperiment::rowData(cds_object)$gene_short_name]
  if (length(missing_genes) >= 1) {
    warning(paste0("Genes ", toString(missing_genes), " not found in cds. Skipping those..."))
    genes <- genes[!genes %in% missing_genes]
  }

  empty <- ggplot() +
    theme_void() +
    xlab(NULL)

  if (length(genes) >= 1) {
    if (facetbygene) {
      for (i in seq(1, length(genes), by = 3)) {
        fp1 <- feature_plot_flexible(cds_object, genes[i])
        if (i + 1 <= length(genes)) {
          fp2 <- feature_plot_flexible(cds_object, genes[i + 1])
        } else {
          print(fp1 + empty + empty)
          next
        }
        if (i + 2 <= length(genes)) {
          fp3 <- feature_plot_flexible(cds_object, genes[i + 2])
          print(fp1 + fp2 + fp3)
        } else {
          print(fp1 + fp2 + empty)
        }
      }
    } else {
      for (i in seq(1, length(genes))) {
        fp <- feature_plot_flexible(cds_object, genes[i], facetby = facetby)
        print(fp)
      }
    }
  }
  invisible(NULL)
}
