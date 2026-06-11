# Spec: Run-quality improvements for the legacy/Gemini executor

**Status:** Scoping — not yet implemented.
**Branch:** `monocle3-migration`
**Source plan:** `~/.claude/plans/twinkling-plotting-frost.md`
**Motivating runs:** `outputs/iPSC_test_monocle3_2026061{0_154807,0_161508,0_171335}/`

## 1. Problem

Three review runs on the iPSC Monocle3 dataset surfaced three recurring quality problems:

1. **`fit_models` frequently fails** — and when it does, downstream steps degrade.
2. **Conclusions don't read like conclusions** — the run appears to "just stop" rather than
   synthesizing findings.
3. **Too few figures** — 4–5 plots across ~34 cells per notebook.

All three runs were produced by **`--execution-mode legacy`** (`IdeaExecutor` in
`cellvoyager/execution/legacy.py`) with **Gemini** as the model via the OpenAI-compatible client
(confirmed by `QS.md` and `example/prompts.txt`). The legacy path passes the **full** `coding_guidelines`
(no `[:3000]` truncation) into generation/fix prompts and **does** call `get_documentation()` in its fix
loop — so the issue is the *content* of the guidance, not dropped context. Fixes therefore live in the
legacy path and the shared prompt files (not `claude.py`/`opencode.py`).

## 2. Root-cause analysis

### 2.1 `fit_models` failures

Guidance is a single thin line in `cellvoyager/prompts/DeepResearch_Analyses.txt:21`. Two mistakes recur:

- **NB1 (`154807`):** `No coefficients found for expected term 'assigned_cell_typechr5-triploidy'.
  Available terms were: NA`. The model subset to a *single* cell type and then fit `~assigned_cell_type`,
  so the grouping variable had **< 2 levels present** → no contrast term exists. (The `stop()` text is
  model-generated R, not native monocle3 — verified absent from the repo.)
- **NB2 (`161508`):** `undefined columns selected` on
  `head(deg[, c("gene_short_name","estimate","p_value","q_value")])`. The model **guessed** both the
  coefficient term name (`cellline_detailHL052`) and the returned column set, instead of inspecting them.

### 2.2 Weak conclusions / "just stops"

There is **no synthesis step**. The loop at `legacy.py:784` runs exactly `max_iterations` steps, then
saves. The final notebook cell is just the last step's per-step **interpretation** produced by
`cellvoyager/prompts/interp_results.txt`, which is *forward-looking by design* ("which results seem
promising, how to iterate"). So the "conclusion" is really mid-stream next-step feedback, and the run
halts at the iteration budget with no holistic wrap-up. When a mid-run step exhausts its 3 fix attempts,
the injected "Current analysis step failed…" interpretation also poisons downstream interpretations
(see NB2's over-optimistic ending).

### 2.3 Too few figures

`cellvoyager/prompts/coding_guidelines.txt:4` (item 3) is opt-in ("When relevant, create figures"),
`patchwork` is not in `AVAILABLE_PACKAGES` (so grid/multi-panel plots were effectively disallowed), and
NB3 *computed* UMAP plots but never `print()`ed them (so they never appeared).

## 3. 10X_merge findings (re-verified)

Local `10X_merge` is in sync with `origin/main` (`1b01136`). There is **no `fit_models`/`coefficient_table`
anywhere in the repo** — its DE/marker path is **`top_markers()`** (all three vignettes), its
preprocessing uses `align_cds(... residual_model_formula_str="~num_genes_expressed")`, and it ships clean,
dependency-light house helpers worth adopting:

- `filter_cds_genes(cds, num_cells_expressed_cutoff=100)` (`10X_merge_utils.R:946`) and its `detectQC()`
  helper — gene filtering that also addresses `fit_models` convergence.
- `feature_plot_flexible()` / `plot_genes_flexible()` (`10X_merge_utils.R:1529-1592`) — `plot_cells`
  wrappers using **`scale_to_range=FALSE`**, `norm_method="log"`, `min_expr=0.1`, with a 3-per-row
  patchwork grid that drops missing genes.

## 4. Design decisions (confirmed with user)

- DE: **`top_markers()` is the primary marker/DE path**; `fit_models` is reserved for explicit condition
  contrasts and wrapped in a defensive recipe.
- Conclusions: a **reserved synthesis step**, guaranteed to run outside the iteration loop.
- 10X_merge: **source curated helpers into the R session** (not prompt-only), and `plot_cells` gene plots
  must use **`scale_to_range=FALSE`**.

## 5. Planned changes

### 5.1 Source curated 10X_merge helpers into the R session
- **New `cellvoyager/r_helpers/cv_helpers.R`** — lightly-adapted copies of `detectQC`, `filter_cds_genes`,
  `feature_plot_flexible`, `plot_genes_flexible`, with `assertthat::assert_that`→`stopifnot` and
  `rlang::is_null`→`is.null` so deps are only `monocle3, SingleCellExperiment, Matrix, ggplot2, patchwork`.
  Header comment cites source repo/commit.
- **`source()` it in the setup cell** (`legacy.py:620-635`, after `library(monocle3)`), wrapped so a load
  failure logs but doesn't abort the run.
- **Whitelist `patchwork`** in `AVAILABLE_PACKAGES` (`agent.py:16`, `legacy.py:18`); already installed
  (`environment-monocle3.yml:51`).

### 5.2 `fit_models` reliability (prompt)
- Rewrite `DeepResearch_Analyses.txt:21` to lead with `top_markers(cds, group_cells_by=..., cores=1)` and
  present `fit_models` only for explicit contrasts, with this defensive recipe:
  ```r
  cds_sub <- filter_cds_genes(cds[, cells_of_interest], num_cells_expressed_cutoff = 10)
  colData(cds_sub)$grp <- droplevels(factor(colData(cds_sub)$grp))
  stopifnot(nlevels(colData(cds_sub)$grp) >= 2)            # else no contrast exists
  fits <- fit_models(cds_sub, model_formula_str = "~grp",
                     expression_family = "quasipoisson", cores = 1)
  ct <- coefficient_table(fits)
  print(unique(ct$term)); print(colnames(ct))             # discover real term/column names — never guess
  res <- ct[ct$status == "OK" & ct$term != "(Intercept)", ]
  res <- res[, intersect(c("gene_short_name","term","estimate","p_value","q_value"), colnames(res))]
  ```
- Harden the fix-loop system prompt (`legacy.py:318-325`, rule 2): ≥2 levels in subset; inspect
  `unique(ct$term)`/`colnames(ct)`; filter `status=="OK"` + actual non-intercept term; select columns via
  `intersect(..., colnames(ct))`; mention `filter_cds_genes()` and prefer `top_markers()` for marker work.
- Add a pointer in the pitfalls list (`coding_guidelines.txt:17-24`).
- Note the example RDS is already processed/annotated — do **not** re-run
  `preprocess_cds`/`align_cds`/`reduce_dimension`.

### 5.3 Reserved synthesis / conclusion step (prompt + legacy code)
- **New `cellvoyager/prompts/conclusion.txt`** — backward-looking synthesis (distinct from
  `interp_results.txt`). Inputs `{hypothesis}`, `{analysis_plan}`, full `{jupyter_notebook}` summary (via
  `generate_jupyter_summary()`, `legacy.py:125`). Must: restate hypothesis; give an explicit verdict
  (supported / partial / not supported); cite specific quantitative results **actually present**; list
  limitations and failed/abandoned steps; never reference figures not displayed.
- **New `generate_conclusion(...)` in `legacy.py`**, modeled on `interpret_results()` (same client,
  logging, prompt/response-save plumbing; pass displayed figures when `use_VLM`).
- **Call once after the loop** (after `legacy.py:987`, before notebook save ~990), appending a single
  `## Conclusion` markdown cell. Outside the loop ⇒ guaranteed to run.

### 5.4 More visualizations (prompt)
- Rewrite item 3 (`coding_guidelines.txt:4`) from opt-in to default-on: every result-yielding step MUST
  produce at least one printed figure (pure QC/printout steps may skip); prefer a plot over a text table.
- Reinforce print-the-plot in item 12 + pitfalls.
- Add a plotting cookbook to `DeepResearch_Analyses.txt` built on the sourced helpers
  (`feature_plot_flexible`, `plot_genes_flexible` with `scale_to_range=FALSE`; `geom_violin`/`geom_boxplot`
  by group; scatter-with-correlation; `facet_wrap`/patchwork).
- Nudge the planner in `first_draft.txt` and `next_step.txt` to include a printed visualization per step.

## 6. Files touched

| File | Change |
|------|--------|
| `cellvoyager/r_helpers/cv_helpers.R` | **new** — curated helpers from 10X_merge |
| `cellvoyager/execution/legacy.py` | source helpers (620-635); whitelist patchwork (18); harden fix-loop prompt (318-325); new `generate_conclusion()` + call after loop (~987) |
| `cellvoyager/agent.py` | whitelist patchwork in `AVAILABLE_PACKAGES` (16) |
| `cellvoyager/prompts/DeepResearch_Analyses.txt` | top_markers-first DE + hardened fit_models recipe + plotting cookbook |
| `cellvoyager/prompts/coding_guidelines.txt` | require figures (4); print plots / fit_models pointer / don't re-preprocess (12-24) |
| `cellvoyager/prompts/conclusion.txt` | **new** — synthesis prompt |
| `cellvoyager/prompts/first_draft.txt`, `next_step.txt` | visualization nudge |

## 7. Verification

1. Re-run the iPSC example:
   `python run_cellvoyager.py --execution-mode legacy --model-name <gemini-model> --rds-path "example/iPSC_dataset/...RDS" --paper-path "example/iPSC_dataset/...txt" --analysis-name iPSC_verify`
2. Confirm in the new notebook:
   - setup cell sources `cv_helpers.R` cleanly and the helpers are callable;
   - any `fit_models` cell pre-filters with `filter_cds_genes`, prints `unique(ct$term)`/`colnames(ct)`,
     filters `status=="OK"`, and no longer errors on undefined columns / missing terms; marker steps use
     `top_markers()`;
   - the notebook ends with a single `## Conclusion` cell giving a hypothesis verdict + cited numbers +
     limitations, referencing only displayed figures;
   - figure count is materially higher (most result steps show a printed plot via the flexible helpers).
3. Spot-check the run's `prompts/`/`responses/` logs: the `conclusion` prompt fired once; the
   top_markers/fit_models guidance reached the model.
4. Sanity-check the `CellVoyager-r` env still loads (rpy2 + monocle3 + patchwork) end-to-end.
