# Spec: Switching CellVoyager from scanpy to monocle3

**Status:** In progress — steps 1–6 complete; steps 7–8 pending
**Branch:** `monocle3-migration`
**Source scoping doc:** `~/.claude/plans/can-you-look-at-twinkly-octopus.md`

### Progress log

- **Step 1 (environment) — ✅ DONE.** Built the `CellVoyager-r` conda env
  (`environment-monocle3.yml`); rpy2↔R bridge, monocle3 load, and inline-PNG plotting
  all verified on the example CDS. See §4.1 for the resolved runtime gotchas and §4.2 for
  the kernel-launch follow-up this created.
- **Step 2 (summarizer + `AVAILABLE_PACKAGES`) — ✅ DONE.** Replaced the four
  anndata/h5py summarizer methods in `cellvoyager/agent.py`
  (`_summarize_adata_full`, `_summarize_adata_obs_only`, `_load_h5ad_obs`,
  `_summarize_df`) with a single rpy2-based `_summarize_cds()` (plus an `_ensure_r()`
  helper that sets `R_HOME=<sys.prefix>/lib/R` so the **orchestrator process** — not just
  the kernel — finds the conda R). The R routine reports CDS equivalents (colData≈.obs,
  rowData≈.var, reducedDims≈.obsm, assays≈.X/layers) in the same text-block shape, so
  downstream prompts are untouched. `AVAILABLE_PACKAGES` is now
  `"monocle3, SingleCellExperiment, Matrix, ggplot2"`. Dropped the now-unused
  `pandas/numpy/h5py/anndata` imports from `agent.py`. **Verified (§6.2):** ran
  `_summarize_cds` on the example RDS — sensible block (28809 cells × 17465 genes,
  colData columns, reducedDims PCA/Aligned/UMAP, counts assay); `py_compile` clean.
  Note: `self.h5ad_path`/`self.adata_summary` names are intentionally kept — the rename
  is step 4 (§4.6).
- **Step 3 (setup cells + kernel launch) — ✅ DONE.** Swapped the scanpy/h5ad setup cell
  for the rpy2/monocle3 shape in all three executors
  (`legacy.py:create_initial_notebook`, `claude.py:_write_initial_notebook`,
  `opencode.py:_write_initial_notebook`). New cell: `%load_ext rpy2.ipython`;
  `import rpy2.robjects as ro`; `ro.r('library(monocle3)')`;
  `ro.r('cds <- readRDS("<path>")')`; prints `Loaded N cells x N genes` via `dim(cds)`
  (`dim` is `[genes, cells]`, so cells=`_dims[1]`, genes=`_dims[0]`). The setup cell stays a
  single Python cell (keeps the executors' one-setup-cell auto-exec logic intact) but uses
  `ro.r(...)` so `cds` lands in `R_GlobalEnv`, visible to every later `%%R` cell. **Kernel
  launch:** added a `CELLVOYAGER_KERNEL_NAME` constant (env-overridable, default
  `cellvoyager-r`) to `legacy.py` and `claude.py`; `legacy.start_persistent_kernel`,
  `claude.NotebookSession.__init__`, and `claude.restart_kernel` now launch
  `KernelManager(kernel_name=CELLVOYAGER_KERNEL_NAME)` (opencode reuses claude's
  `NotebookSession`, so it inherits this). Also flipped `legacy.AVAILABLE_PACKAGES` to the
  monocle3 set. **Coupled prompt fix:** the *inline* executor prompts that name the setup
  handle/loader were updated in lockstep (`adata`→`cds`, `sc.read_h5ad`→`readRDS`,
  "AnnData"→"Monocle3 cell_data_set", code fences `python`→`r`, plus a "write R inside
  `%%R`, reuse `cds`" instruction) so the executors aren't self-contradictory — the broader
  prompt-*file* rewrite in `cellvoyager/prompts/` (and `legacy.fix_code`'s scanpy-API
  system prompt) remains step 6. `notebook_tools.py` was left untouched: it's unused
  (imported nowhere). **Verified:** launched `KernelManager(kernel_name="cellvoyager-r")`,
  ran the new setup cell on the example RDS → `Data loaded: 28809 cells and 17465 genes`;
  a follow-up `%%R plot_cells(cds)` cell rendered **inline as image/png**, confirming `cds`
  persists across cells and VLM-critical PNG capture works. `py_compile` clean on all three.

- **Step 4 (CLI/param rename) — ✅ DONE.** Hard-renamed the data-path argument
  `--h5ad-path` → `--rds-path` (no alias) and the `h5ad_path` parameter/attribute →
  `rds_path` across the whole pipeline: `run_cellvoyager.py` (arg, `args.rds_path`
  usages, validation/print messages, both `AnalysisAgentV2(rds_path=…)` call sites, and
  the resume `cfg["rds_path"]` key), `agent.py` (`AnalysisAgentV2.__init__` param +
  `self.rds_path` + `_summarize_cds` call + `shared_executor_kwargs`), and all three
  executors (`legacy.IdeaExecutor`, `claude.NotebookSession`/`ClaudeJupyterExecutor`,
  `opencode.OpenCodeJupyterExecutor` — param, `self.rds_path`, and the `readRDS("…")`
  setup-cell interpolation). **Defaults** in `run_cellvoyager.py` now point at the example
  RDS (`--rds-path` → `example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS`,
  `--paper-path` → the matching `…10X comparisons.txt`, `--analysis-name` → `iPSC`).
  **GUI:** `gui/app.py` launch now passes `--rds-path` and writes `rds_path` in
  `.run_config.json` (so resume reads the new key). Scope was **path-only by decision** —
  `adata_summary`/`{adata_summary}` and the GUI's internal `home_h5ad_path` session keys +
  `.h5ad` file-uploader UX are left for step 6 (prompt placeholders) / step 7 (example data
  & docs); the `adata_path=` keyword in `coding_guidelines.format(...)` is kept to match the
  still-unrenamed `{adata_path}` placeholder. `cellvoyager/_assemble_h5ad.py` is the
  RDS→h5ad converter and legitimately keeps its `h5ad` names. **Verified:** `py_compile`
  clean on all six touched files; `grep h5ad_path` over `cellvoyager/` + `run_cellvoyager.py`
  is empty (excluding the converter).

- **Step 5 (docs helper → R help) — ✅ DONE.** Rewrote `cellvoyager/utils.py`'s
  `get_documentation()` (the fix/critique-loop docs section) from scanpy/`inspect.getdoc`
  to monocle3-via-rpy2. The old Python-AST machinery (`extract_call_names`, `resolve_obj`,
  `load_namespace`) and the **import-time scanpy demo block** (which would have triggered R
  init on every `import utils`) are gone. New pipeline: (1) `extract_r_call_names()` —
  regex that grabs identifiers (optionally `pkg::`-qualified) immediately before `(`, so it
  works on raw R, `%%R` cells, or `ro.r("…")` strings alike; (2) `_r_doc_tools()` —
  `lru_cache`d one-time R init that loads `monocle3, SingleCellExperiment, Matrix, ggplot2`,
  builds the union of their `getNamespaceExports` (cheap pre-filter, analog of the old
  `sc.*` gate), and compiles an R help function; (3) per call name, fetch the help page.
  The R helper uses `do.call(utils::help, list(name))` (not `help(name)` — `help()`
  `substitute()`s its arg, so a bare variable resolves to the literal symbol), filters by
  the resolved help **path's owning package** so base-R generics a target package re-exports
  (e.g. `print`) are dropped, renders Rd via `capture.output(tools::Rd2txt(...))`, and Python
  strips the terminal overstrike sequences (`_\x08C`) Rd2txt emits. `R_HOME` is set the same
  way as `agent._summarize_cds` (orchestrator-process rpy2 → point at `<sys.prefix>/lib/R`).
  Failures return a short marker string, never raise. **Verified (§6.3):** in the
  `CellVoyager-r` env, `get_documentation("reduce_dimension(cds)")` returns the real
  monocle3 help (~5k chars, clean text); a multi-call blob yields `cluster_cells` +
  `top_markers` docs with `print(...)` correctly filtered out; `py_compile` clean.

- **Step 6 (prompt rewrites) — ✅ DONE.** Rewrote the scanpy/Python prompt content to
  monocle3/R across `cellvoyager/prompts/`: `coding_guidelines.txt` (rule 7 now "R run
  through monocle3 inside `%%R`", `{available_packages}` framed as R packages; rules 8–14
  point at the in-memory `cds` cell_data_set and its accessors — colData/rowData/reducedDims/
  exprs; the scanpy "API pitfalls" block replaced with monocle3-via-rpy2 pitfalls — workflow
  ordering deps, return-value reassignment, R accessors/1-indexing, ggplot must be printed);
  `DeepResearch_Analyses.txt` fully rewritten from the scanpy catalog to the monocle3 workflow
  catalog (`preprocess_cds`, `reduce_dimension`, `cluster_cells`, `top_markers`, `fit_models`,
  `learn_graph`/`order_cells`/pseudotime, `graph_test`, `plot_cells`/ggplot2); and the
  "python code"/"AnnData"/"anndata object" wording in `first_draft.txt`, `next_step.txt`,
  `next_step_seeded.txt`, `critic.txt`, `incorporate_critque.txt`, `coding_system_prompt.txt`,
  `deepresearch.txt` swapped to "R (monocle3) code"/"Monocle3 cell_data_set (cds)". The
  parallel ablation prompts (`ablations/coding_guidelines_NO_VLM_ABLATION.txt`,
  `critic_NO_DOCUMENTATION.txt`, `analysis_from_hypothesis.txt`) were migrated in lockstep
  since they're live code paths in `hypothesis.py`/`agent.py`. **Executor code prompts** also
  done: `execution/legacy.py` `fix_code` system prompt (scanpy API rules → monocle3 rules),
  its prompt/`code_description` code fences (` ```python `→` ```r `), the two "feedback on
  Python code" critic system strings → "R (monocle3) code", and `clean_code`'s fence-stripper
  now also strips ` ```r `/` ```R `; `execution/claude.py` `strip_code_fences` likewise strips
  R fences. `interp_results.txt`/`summarization.txt` needed no change (no scanpy/Python/adata
  refs). **Decision — placeholder tokens kept:** the internal `.format()` tokens
  `{adata_summary}` and `{adata_path}` were **left as-is** (NOT renamed to `{cds_summary}`/
  `{rds_path}`). These prompt files are *shared* with the unmigrated top-level `legacy/`
  standalone (`legacy/run.py` → `legacy/agent.py`), which still `.format(adata_summary=…,
  adata_path=…)`s them; renaming the tokens would hard-crash it with a `KeyError`, and the
  tokens are internal — never shown to the LLM — so the rename buys nothing. This supersedes
  the step-4 note that tentatively deferred the token rename to step 6. Hence step 6 is a
  pure prompt-text change with **zero Python lockstep** (the `.format()` kwargs in `agent.py`,
  `hypothesis.py`, `deepresearch.py`, and the three executors are untouched). **Verified:**
  `py_compile` clean on `legacy.py`/`claude.py` (py3.8); `{adata_summary}`/`{adata_path}`
  tokens still present in every formatted template; a `string.Formatter` brace-stress over all
  `prompts/**/*.txt` passes (no stray/unescaped braces); residual `scanpy`/`anndata`/`adata`
  matches are all intentional ("NOT Python/scanpy", "analog of scanpy's rank_genes_groups").

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

### 4.1 Runtime / dependencies (riskiest) — ✅ DONE (step 1)
- Add `rpy2` to Python deps. Extend the existing `r_h5ad`-style conda env to include the
  R packages: `monocle3`, `SingleCellExperiment`, `Matrix`, `ggplot2`. Do **not** use
  system R.
- Verify the kernel can `%load_ext rpy2.ipython`, `readRDS(...)`, and render R graphics
  **inline as PNG** (required so the VLM step keeps working).
- Update `environment.yml` (or document the extended env spec) accordingly.

**Implemented as a new unified env `CellVoyager-r`** (spec: `environment-monocle3.yml`) —
modeled on the `r_h5ad`/`rds2h5ad` style but combining the R backend and the Python
orchestrator in one env (rpy2 must live in the kernel's Python). Key environment facts
discovered while building it (all the "fragile runtime" risks were real):

- **This machine's conda is osx-64 (Rosetta)**, not native arm64. Modern `r-monocle3`
  (1.3.1, R 4.3) is published for osx-64 on bioconda — so monocle3 installs from conda,
  **no GitHub/C++ compile**. (Native arm64 bioconda only has an ancient 0.2.0.)
- **`rpy2` pinned to 3.5.17, not 3.6.x.** rpy2 3.6 needs the `R_getVar` symbol added in
  R 4.5; conda r-monocle3 only builds against R 4.3/4.4, so 3.6 fails to load `libR`.
- **`r-terra` must be added explicitly** — it's an undeclared runtime dep of bioconda
  `r-monocle3` (the package fails to load without it).
- **`R_HOME` must point at the conda env's R.** Otherwise rpy2 defaults to the system
  arm64 R and dies on an x86_64-vs-arm64 arch mismatch. The registered Jupyter kernelspec
  (`~/Library/Jupyter/kernels/cellvoyager-r/kernel.json`) sets this via an `env` block.
- **Verified:** `library(monocle3)` loads without segfault (monocle3 1.3.1); the example
  CDS reads as a `cell_data_set` (17465 genes × 28809 cells, reducedDims PCA/Aligned/UMAP);
  `plot_cells(cds)` renders **inline as image/png** through the `cellvoyager-r` kernel.
- **Open question for step 3+:** the executors must launch their Jupyter kernel as
  `cellvoyager-r` (with the `R_HOME` env), not the current default kernel. Wire this into
  the executor kernel-launch code.

### 4.2 Setup cells in all three executors (the core swap) — ✅ DONE (step 3)
Replace the scanpy import + `sc.read_h5ad(...)` block in each:
- `cellvoyager/execution/legacy.py:550-573` — `create_initial_notebook`
- `cellvoyager/execution/claude.py:896-905` — `_write_initial_notebook`
- `cellvoyager/execution/opencode.py:119-128` — `_write_initial_notebook`

New shared setup cell shape: `%load_ext rpy2.ipython`; `library(monocle3)`;
`cds <- readRDS("<rds_path>")`; print dims. Canonical handle is the R `cds` object (the
analog of today's `adata`). Reproduce the "Loading data…/Loaded N cells × N genes" prints
via R.

**Kernel launch (carried over from step 1):** each executor must launch its Jupyter
kernel as **`cellvoyager-r`** (or otherwise ensure the kernel process has
`R_HOME=<CellVoyager-r prefix>/lib/R`), not the current default kernel. Without the right
`R_HOME`, rpy2 loads the system arm64 R and crashes on an arch mismatch. Audit each
executor's kernel-manager/kernel-name wiring as part of this step.

### 4.3 Data summarization — rewrite for CDS (substantial) — ✅ DONE (step 2)
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
- ✅ **DONE (step 2)** — `cellvoyager/agent.py` — replaced the scanpy-centric
  `AVAILABLE_PACKAGES` string with the monocle3/R set:
  `monocle3, SingleCellExperiment, Matrix, ggplot2` (R-only plotting, so scanpy/anndata/
  seaborn dropped). Docs helper below remains for step 5.
- ✅ **DONE (step 5)** — `cellvoyager/utils.py` — `get_documentation()` reimplemented to
  pull R help via rpy2 for the target packages (regex R-call extraction + `do.call(help)` +
  package-path filter + `Rd2txt`), replacing the scanpy `inspect.getdoc` path. Verified on
  `reduce_dimension` (§6.3).

### 4.6 CLI and naming (hard rename) — ✅ DONE (step 4)
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
1. ✅ **DONE** — Environment: `CellVoyager-r` conda env + `rpy2`, inline-PNG verified (§4.1, §6.1).
2. ✅ **DONE** — Summarizer rewrite + `AVAILABLE_PACKAGES` (§4.3, §4.5) — verified on example RDS (§6.2).
3. ✅ **DONE** — Setup cells across the three executors, incl. launching the `cellvoyager-r` kernel (§4.2) — verified on example RDS, inline PNG confirmed (§6.1).
4. ✅ **DONE** — CLI/param rename `--h5ad-path`→`--rds-path`, `h5ad_path`→`rds_path` through agent + 3 executors + GUI; example-RDS defaults (§4.6).
5. ✅ **DONE** — Docs helper R-help reimplementation (§4.5) — verified on `reduce_dimension` (§6.3).
6. ✅ **DONE** — Prompt rewrites (§4.4): scanpy→monocle3/R content across `cellvoyager/prompts/` (+ ablations) and the executor code prompts; placeholder tokens kept (shared with legacy standalone).
7. Example/docs updates (§4.7). **← next**
8. End-to-end per executor + fix loop (§6.4, §6.5).
