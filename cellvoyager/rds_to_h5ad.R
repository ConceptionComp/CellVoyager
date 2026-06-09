#!/usr/bin/env Rscript
#
# rds_to_h5ad.R — Convert an R single-cell object stored as .RDS into an .h5ad
# AnnData file that CellVoyager can ingest.
#
# Supports Monocle3 `cell_data_set` and Bioconductor `SingleCellExperiment`
# objects. Seurat objects are not handled here (use SeuratDisk/sceasy for those).
#
# Why the stub class: some machines have a broken monocle3 native install
# (`library(monocle3)` segfaults loading its 'spat' module). A `cell_data_set`
# is structurally a `SingleCellExperiment`, so we declare a lightweight stub
# class — letting readRDS reconstruct the object WITHOUT loading monocle3 — then
# coerce to a plain SingleCellExperiment and write it out with zellkonverter.
#
# The conversion preserves:
#   assays      -> adata.X (first assay) + adata.layers (the rest)
#   colData     -> adata.obs   (cell metadata)
#   rowData     -> adata.var   (gene metadata)
#   reducedDims -> adata.obsm  (e.g. UMAP, PCA)
#   metadata    -> adata.uns
#
# Usage:
#   Rscript cellvoyager/rds_to_h5ad.R <input.RDS> <output.h5ad> [X_assay_name]
#
# Example:
#   Rscript cellvoyager/rds_to_h5ad.R data/sample.RDS example/iPSC_dataset/sample.h5ad

suppressMessages({
  library(SingleCellExperiment)
  library(SummarizedExperiment)
  library(S4Vectors)
  library(Matrix)
})

log_msg <- function(...) {
  cat(format(Sys.time(), "%H:%M:%S"), "-", ..., "\n")
  flush.console()
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript rds_to_h5ad.R <input.RDS> <output.h5ad> [X_assay_name]")
}
inpath  <- args[[1]]
outpath <- args[[2]]
x_assay <- if (length(args) >= 3) args[[3]] else NA_character_

if (!file.exists(inpath)) stop("Input file does not exist: ", inpath)
dir.create(dirname(outpath), recursive = TRUE, showWarnings = FALSE)

# Stub class: lets readRDS rebuild a monocle3 cell_data_set without monocle3.
# The monocle3-specific slots are declared as ANY and dropped on coercion.
setClass("cell_data_set",
  contains = "SingleCellExperiment",
  representation(
    reduce_dim_aux      = "ANY",
    principal_graph_aux = "ANY",
    principal_graph     = "ANY",
    clusters            = "ANY"
  ))

log_msg("Reading RDS:", inpath)
log_msg("(a large compressed object can take 1-2 minutes to load)")
obj <- readRDS(inpath)
log_msg("Loaded object of class:", paste(class(obj), collapse = ","))

# The deserialized object's class is tagged with package="monocle3". Any S4
# dispatch (is(), accessors) would then try to load monocle3, whose native
# 'spat' module segfaults on this machine. Re-point the class at our in-process
# stub (.GlobalEnv) so dispatch resolves locally and never loads monocle3.
cl <- class(obj)
if (!is.null(attr(cl, "package")) && attr(cl, "package") != ".GlobalEnv") {
  log_msg("Re-pointing class '", cl, "' from package '", attr(cl, "package"),
          "' to .GlobalEnv stub", sep = "")
  attr(cl, "package") <- ".GlobalEnv"
  attr(obj, "class") <- cl
}

if (!is(obj, "SingleCellExperiment")) {
  stop("Object is not a SingleCellExperiment / cell_data_set. ",
       "Got class: ", paste(class(obj), collapse = ","))
}

log_msg("dim (genes x cells):", paste(dim(obj), collapse = " x "))
log_msg("assayNames:",      paste(assayNames(obj), collapse = ", "))
log_msg("reducedDimNames:", paste(reducedDimNames(obj), collapse = ", "))
log_msg("colData columns:", paste(colnames(colData(obj)), collapse = ", "))
log_msg("rowData columns:", paste(colnames(rowData(obj)), collapse = ", "))

# Rebuild a clean SingleCellExperiment from the SCE components only. This drops
# the monocle3-specific slots (incl. the Annoy index that can't deserialize)
# and yields an object whose class lives in a loaded package.
log_msg("Rebuilding a clean SingleCellExperiment from SCE components...")
assay_list <- as.list(assays(obj, withDimnames = FALSE))
rdims      <- as.list(reducedDims(obj, withDimnames = FALSE))
sce <- SingleCellExperiment(
  assays      = assay_list,
  colData     = colData(obj),
  rowData     = rowData(obj),
  reducedDims = rdims
)
rownames(sce) <- rownames(obj)
colnames(sce) <- colnames(obj)
rm(obj); invisible(gc())

if (is.na(x_assay)) x_assay <- assayNames(sce)[[1]]
log_msg("Using assay '", x_assay, "' as adata.X", sep = "")

# Export components to a temp dir, then assemble the .h5ad with Python's
# anndata directly. This avoids zellkonverter/basilisk, which on this machine
# repeatedly fails trying to download+provision its own conda env.
tmpdir <- tempfile("rds2h5ad_")
dir.create(tmpdir)
log_msg("Exporting components to", tmpdir)

# Counts matrix (genes x cells), MatrixMarket. Python transposes to cells x genes.
mat <- assay(sce, x_assay)
if (!is(mat, "CsparseMatrix")) mat <- as(as(mat, "CsparseMatrix"), "generalMatrix")
Matrix::writeMM(mat, file.path(tmpdir, "matrix.mtx"))

# Cell metadata (obs) and gene metadata (var). Coerce list-columns to character
# so write.csv doesn't choke.
flatten_df <- function(df) {
  df <- as.data.frame(df, optional = TRUE)
  for (j in seq_along(df)) {
    if (is.list(df[[j]]) || !is.atomic(df[[j]])) {
      df[[j]] <- vapply(df[[j]], function(x) paste(as.character(x), collapse = ";"),
                        character(1))
    }
  }
  df
}
obs <- flatten_df(colData(sce)); rownames(obs) <- colnames(sce)
var <- flatten_df(rowData(sce)); rownames(var) <- rownames(sce)
write.csv(obs, file.path(tmpdir, "obs.csv"))
write.csv(var, file.path(tmpdir, "var.csv"))

# Reduced dims -> obsm, using scanpy-style keys (X_pca, X_umap, ...).
obsm <- list()
for (rn in reducedDimNames(sce)) {
  emb <- as.matrix(reducedDim(sce, rn))
  rownames(emb) <- colnames(sce)
  key <- paste0("X_", tolower(rn))
  fname <- paste0("obsm_", rn, ".csv")
  write.csv(emb, file.path(tmpdir, fname))
  obsm[[key]] <- fname
}

# Minimal hand-rolled JSON (avoids a jsonlite dependency).
jstr <- function(s) paste0('"', gsub('([\\\\"])', '\\\\\\1', s), '"')
obsm_json <- if (length(obsm) == 0) "{}" else paste0(
  "{", paste(sprintf("%s: %s", jstr(names(obsm)), jstr(unlist(obsm))),
             collapse = ", "), "}")
manifest <- sprintf(
  '{"tmpdir": %s, "matrix": %s, "obs": %s, "var": %s, "obsm": %s}',
  jstr(tmpdir), jstr("matrix.mtx"), jstr("obs.csv"), jstr("var.csv"), obsm_json)
writeLines(manifest, file.path(tmpdir, "manifest.json"))

log_msg("Assembling .h5ad with Python (anndata)...")
py <- Sys.getenv("RDS2H5AD_PYTHON", unset = "python")
script <- file.path(dirname(sub("^--file=", "",
              grep("^--file=", commandArgs(FALSE), value = TRUE)[1])),
              "_assemble_h5ad.py")
status <- system2(py, c(shQuote(script),
                        shQuote(file.path(tmpdir, "manifest.json")),
                        shQuote(outpath)))
if (status != 0) stop("Python assembly step failed (exit ", status, ")")
unlink(tmpdir, recursive = TRUE)
log_msg("DONE. Wrote:", outpath)
