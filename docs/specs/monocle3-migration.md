# Spec: Switching CellVoyager from scanpy to monocle3

**Status:** Draft for implementation
**Branch:** `monocle3-migration`
**Source scoping doc:** `~/.claude/plans/can-you-look-at-twinkly-octopus.md`

## 1. Summary

CellVoyager is an LLM-driven single-cell analysis agent. Today it is a pure-Python
pipeline: it loads an `.h5ad` with `anndata`, an LLM writes **scanpy** code as strings,
and that code runs in a Python Jupyter kernel. This change replaces scanpy with
**monocle3**.

Decisive constraint: **monocle3 is R-only — there is no Python monocle3.** This is a
cross-language migration, not an API swap. We bridge to R with **`rpy2`** inside the
existing Python kernel (LLM writes R via `%%R` magic / `rpy2.robjects`); no separate R
kernel. Input data switches from `.h5ad` to native Monocle3/SCE **`.RDS`**
(`cell_data_set`, canonical R handle: `cds`).

The orchestration machinery (hypothesis loop, self-critique, kernel/MCP execution, cell
insertion/execution, fix-on-error loop, notebook snapshots, VLM interpretation) is
backend-agnostic and **stays**. Only the *content* of generated strings (R vs Python)
and the *input/summary path* change.

## 2. Confirmed decisions

| Decision | Choice |
| --- | --- |
| R bridge | `rpy2` in the existing Python kernel via `%%R` / `rpy2.robjects` |
| Scope | Full replacement of scanpy (not an added option) |
| Executors | All three: `legacy`, `claude`, `opencode` |
| Input data | `.RDS` (`cell_data_set`); canonical handle `cds` |
| **Plotting** | **R-only (ggplot2 / `plot_cells`)**, captured inline as PNG for the VLM |
| **Docs helper** | **Reimplement `get_documentation()` against R help via rpy2** |
| **CLI** | **Hard rename `--h5ad-path` → `--rds-path` (no back-compat alias)** |
| **Environment** | **Extend the existing `r_h5ad`-style conda env** (system R segfaults — see memory `rds-to-h5ad-conversion`) |
| Testing | No TDD; verification is manual/smoke per §6 |

## 3. Difficulty: High (major change)

Touches dependencies/runtime, data loading, data summarization, all prompt text, the
docs-extraction helper, the CLI, and example data.

**Biggest risk — environment:** getting `rpy2` + R + monocle3 to run reliably inside the
Jupyter kernel, including inline R plot capture for the VLM. System R is known to
segfault; we extend the dedicated conda env instead.

**Secondary risk:** the LLM is far more fluent in scanpy than monocle3-via-rpy2 — expect
more failed cells and heavier reliance on the fix loop until prompts are tuned.

## 4. Work items

### 4.1 Runtime / dependencies (riskiest)
- Add `rpy2` to Python deps. Extend the existing `r_h5ad`-style conda env to include the
  R packages: `monocle3`, `SingleCellExperiment`, `Matrix`, `ggplot2`. Do **not** use
  system R.
- Verify the kernel can `%load_ext rpy2.ipython`, `readRDS(...)`, and render R graphics
  **inline as PNG** (required so the VLM step keeps working).
- Update `environment.yml` (or document the extended env spec) accordingly.

### 4.2 Setup cells in all three executors (the core swap)
Replace the scanpy import + `sc.read_h5ad(...)` block in each:
- `cellvoyager/execution/legacy.py:550-573` — `create_initial_notebook`
- `cellvoyager/execution/claude.py:896-905` — `_write_initial_notebook`
- `cellvoyager/execution/opencode.py:119-128` — `_write_initial_notebook`

New shared setup cell shape: `%load_ext rpy2.ipython`; `library(monocle3)`;
`cds <- readRDS("<rds_path>")`; print dims. Canonical handle is the R `cds` object (the
analog of today's `adata`). Reproduce the "Loading data…/Loaded N cells × N genes" prints
via R.

### 4.3 Data summarization — rewrite for CDS (substantial)
`cellvoyager/agent.py:204-389` summarizes an AnnData via `anndata.read_h5ad(backed="r")`
and raw `h5py` (`_summarize_adata_full`, `_summarize_adata_obs_only`, `_load_h5ad_obs`,
`_summarize_df`). None of this works on `.RDS`. Replace with an rpy2-based summarizer
reporting CDS equivalents:

| AnnData | CDS equivalent |
| --- | --- |
| `.obs` | `colData` |
| `.var` | `rowData` / `fData` |
| `.obsm` | `reducedDims` |
| `.X` / layers | `assays` / counts |

Keep the **same output shape** (a text block) so downstream prompts are unaffected.

### 4.4 Prompt files — rewrite scanpy content (broad but mechanical)
In `cellvoyager/prompts/`:
- `coding_guidelines.txt` — replace the scanpy "API pitfalls" block (lines 17-22) with
  monocle3-via-rpy2 pitfalls; update the `adata` handle references / "DO NOT LOAD"
  (lines 10-12) to the new `cds` handle; state that code must be R run via `%%R` (or
  `rpy2.robjects`); update the "Display all figures" rule for **R/ggplot2** plotting.
- `DeepResearch_Analyses.txt` — rewrite the all-scanpy workflow catalog to monocle3
  equivalents: `preprocess_cds`, `reduce_dimension`, `cluster_cells`, `learn_graph`,
  `order_cells`/pseudotime, `graph_test`, `top_markers`.
- `first_draft.txt`, `next_step.txt`, `next_step_seeded.txt`, `critic.txt`,
  `incorporate_critque.txt`, `interp_results.txt`, `summarization.txt`,
  `coding_system_prompt.txt` — update "python code" / `first_step_code` wording to
  R/monocle3 and the new `cds` handle. Mostly text, no structural change.
  - Reminder (AGENTS.md): prompt templates are `.format(...)`-ed; escape literal braces as
    `{{`/`}}`.

### 4.5 `AVAILABLE_PACKAGES` and docs helper
- `cellvoyager/agent.py:21` — replace the scanpy-centric `AVAILABLE_PACKAGES` string with
  the monocle3/R set: `monocle3, SingleCellExperiment, Matrix, ggplot2` (R-only plotting,
  so drop scanpy/anndata/seaborn; keep any Python libs only if Python actually remains).
- `cellvoyager/utils.py:102-126` — `get_documentation()` currently resolves only
  `sc.`/`scanpy.` calls via Python `inspect.getdoc`. **Reimplement to pull R help via
  rpy2** for monocle3 functions (e.g. capture `?function` / `help` text through rpy2), so
  the fix loop keeps a working docs section.

### 4.6 CLI and naming (hard rename)
- `run_cellvoyager.py:24-28` — rename `--h5ad-path` → `--rds-path` (no alias).
- Rename the `h5ad_path` parameter threaded through `AnalysisAgentV2` (`agent.py`) and all
  three executors → `data_path` (or `rds_path`; pick one and keep consistent).
- Update default example path and `--analysis-name` example to the RDS dataset.
- Update the GUI launch path (`gui/app.py`, which invokes `run_cellvoyager.py
  --execution-mode claude`) so it passes `--rds-path`. **Hard rename breaks any existing
  callers** — audit all call sites.

### 4.7 Example data & docs
- Use the existing example RDS:
  `example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS`.
- Update `README.md`, `docs/user-guide.md`, `QS.md`, and `AGENTS.md` references from
  scanpy/h5ad to monocle3/RDS.

## 5. Out of scope (does NOT change)
Hypothesis generation loop, self-critique, kernel/MCP execution machinery, cell
insertion/execution, fix-on-error loop, notebook snapshots, VLM interpretation step. These
shuttle strings and images and are backend-agnostic.

## 6. Verification (manual smoke tests)
1. **Environment smoke test:** in the target kernel, `%load_ext rpy2.ipython`, then
   `%%R library(monocle3); cds <- readRDS('<example.rds>'); dim(cds)`. Confirm
   `plot_cells(cds)` renders **inline as PNG**.
2. **Summarizer:** run the new CDS summarizer on the example `.RDS`; confirm a sensible
   text block (colData columns, reducedDims, dims).
3. **Docs helper:** confirm `get_documentation()` returns real R help text for a monocle3
   function (e.g. `reduce_dimension`).
4. **End-to-end per executor:** run
   `run_cellvoyager.py --rds-path example/iPSC_dataset/<file>.RDS` for `--execution-mode`
   `legacy`, `claude`, and `opencode` (one analysis, low iterations). Confirm a notebook
   with executed monocle3 cells, captured plots, and a VLM interpretation — **no scanpy
   import surviving**.
5. **Fix loop:** feed a known-bad monocle3 call; confirm the fix loop recovers using the
   R-based docs helper.
6. **Sanity:** `python -m py_compile` on all touched Python files (per AGENTS.md).

## 7. Open follow-ups (not blocking)
- None outstanding — plotting (R-only), docs helper (R help), CLI (hard rename), and
  environment (extend `r_h5ad`) are all decided above.

## 8. Suggested implementation order
1. Environment: extend conda env, add `rpy2`, verify inline PNG plotting (§4.1, §6.1).
2. Summarizer rewrite + `AVAILABLE_PACKAGES` (§4.3, §4.5) — verify on example RDS (§6.2).
3. Setup cells across the three executors (§4.2).
4. CLI/param rename + GUI call site (§4.6).
5. Docs helper R-help reimplementation (§4.5) — verify (§6.3).
6. Prompt rewrites (§4.4).
7. Example/docs updates (§4.7).
8. End-to-end per executor + fix loop (§6.4, §6.5).
