# cv_helpers.R — curated single-cell house helpers for CellVoyager runs.
#
# Adapted from the `10X_merge` analysis repo's `10X_merge_utils.R`
# (detectQC / filter_cds_genes ~L946; feature_plot_flexible / plot_genes_flexible
# ~L1529-1592). Re-implemented here to drop heavy deps: `assertthat::assert_that`
# -> `stopifnot`, `rlang::is_null` -> `is.null`. Dependencies are limited to the
# packages whitelisted for generated analyses:
#   monocle3, SingleCellExperiment, Matrix, ggplot2, patchwork
#
# These functions are sourced into the live R session by the legacy executor so
# generated analysis cells can call them directly (e.g. filter_cds_genes(cds),
# plot_genes_flexible(cds, genes)). Loading is wrapped so a failure logs but does
# not abort the run.

suppressPackageStartupMessages({
  library(monocle3)
  library(SingleCellExperiment)
  library(Matrix)
  library(ggplot2)
  library(patchwork)
})

# --- QC / gene filtering -----------------------------------------------------

# detectQC(cds): (re)compute per-gene `num_cells_expressed` from the counts
# assay and store it in rowData(cds). monocle3 normally populates this during
# preprocessing, but recomputing makes filtering robust to subset CDS objects
# whose metadata was not refreshed after bracket-subsetting.
detectQC <- function(cds) {
  stopifnot(methods::is(cds, "cell_data_set"))
  counts <- SingleCellExperiment::counts(cds)
  num_cells_expressed <- Matrix::rowSums(counts > 0)
  SummarizedExperiment::rowData(cds)$num_cells_expressed <- num_cells_expressed
  cds
}

# filter_cds_genes(cds, num_cells_expressed_cutoff = 100): drop genes detected in
# fewer than `num_cells_expressed_cutoff` cells. Besides removing uninformative
# genes, this materially improves fit_models() convergence — per-gene GLMs on
# near-all-zero genes are exactly what produce non-convergence / missing-term
# errors. Use a small cutoff (e.g. 10) when subsetting to few cells.
filter_cds_genes <- function(cds, num_cells_expressed_cutoff = 100) {
  stopifnot(methods::is(cds, "cell_data_set"))
  cds <- detectQC(cds)
  keep <- SummarizedExperiment::rowData(cds)$num_cells_expressed >= num_cells_expressed_cutoff
  keep[is.na(keep)] <- FALSE
  cds[keep, ]
}

# --- gene name resolution ----------------------------------------------------

# .cv_resolve_genes(cds, genes): map a vector of gene identifiers (rownames or
# gene_short_name symbols) to the rownames present in `cds`, silently dropping
# any that are absent. Returns a character vector (possibly empty).
.cv_resolve_genes <- function(cds, genes) {
  rn <- rownames(cds)
  gs <- SummarizedExperiment::rowData(cds)$gene_short_name
  resolved <- character(0)
  for (g in genes) {
    if (g %in% rn) {
      resolved <- c(resolved, g)
    } else if (!is.null(gs) && g %in% gs) {
      resolved <- c(resolved, rn[match(g, gs)])
    }
  }
  unique(resolved)
}

# --- flexible expression plots ----------------------------------------------

# feature_plot_flexible(cds, gene, ...): single-gene expression on the existing
# embedding via plot_cells, using log-normalized values with scale_to_range=FALSE
# (absolute, comparable color scale) and a small min_expr floor so empty cells do
# not dominate the scale. Returns a ggplot object (or NULL if the gene is absent).
feature_plot_flexible <- function(cds, gene, norm_method = "log", min_expr = 0.1, ...) {
  resolved <- .cv_resolve_genes(cds, gene)
  if (length(resolved) == 0) return(NULL)
  plot_cells(
    cds,
    genes = resolved[1],
    scale_to_range = FALSE,
    norm_method = norm_method,
    min_expr = min_expr,
    label_cell_groups = FALSE,
    ...
  )
}

# plot_genes_flexible(cds, genes, ncol = 3, ...): grid of per-gene expression
# plots, 3 per row by default, dropping any genes not found in the CDS. Returns a
# patchwork object — print it to render. Use scale_to_range=FALSE so panels share
# an absolute, comparable scale.
plot_genes_flexible <- function(cds, genes, ncol = 3, norm_method = "log", min_expr = 0.1, ...) {
  resolved <- .cv_resolve_genes(cds, genes)
  if (length(resolved) == 0) {
    stop("plot_genes_flexible: none of the requested genes are present in the CDS")
  }
  plots <- lapply(resolved, function(g) {
    feature_plot_flexible(cds, g, norm_method = norm_method, min_expr = min_expr, ...) +
      ggtitle(g)
  })
  patchwork::wrap_plots(plots, ncol = ncol)
}
