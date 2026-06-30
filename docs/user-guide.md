# CellVoyager User Guide

CellVoyager is an AI agent for automated single-cell RNA sequencing (scRNA-seq) analysis. You give it a dataset and biological context; it generates hypotheses, writes **R (monocle3) analysis code**, executes it in a Jupyter notebook, and iterates — producing a fully documented notebook you can explore, extend, and build on.

This guide covers everything you need to run CellVoyager from the terminal using **legacy mode**, which works with local models via LM Studio and with Google Gemini. (The default `claude` execution mode targets Anthropic models and is not covered here.)

---

## Table of Contents

- [CellVoyager User Guide](#cellvoyager-user-guide)
  - [Table of Contents](#table-of-contents)
  - [What CellVoyager Does](#what-cellvoyager-does)
  - [Installation](#installation)
  - [Setting Up Your Model](#setting-up-your-model)
    - [Option A: Local Models via LM Studio](#option-a-local-models-via-lm-studio)
    - [Option B: Google Gemini](#option-b-google-gemini)
  - [Preparing Your Inputs](#preparing-your-inputs)
    - [The Dataset File (.RDS)](#the-dataset-file-rds)
    - [The Dataset Summary File](#the-dataset-summary-file)
  - [Running an Analysis](#running-an-analysis)
    - [Minimal Command](#minimal-command)
    - [All Flags Reference](#all-flags-reference)
  - [Interactive Mode](#interactive-mode)
  - [Logging](#logging)
    - [Additional logging flags](#additional-logging-flags)
  - [Understanding Your Outputs](#understanding-your-outputs)
  - [Worked Example: iPSC Dataset](#worked-example-ipsc-dataset)
    - [Step 1: The example data](#step-1-the-example-data)
    - [Step 2: Run with Gemini](#step-2-run-with-gemini)
    - [Step 3: Run with a local model via LM Studio](#step-3-run-with-a-local-model-via-lm-studio)
    - [Step 4: Run interactively with logging](#step-4-run-interactively-with-logging)
    - [What to expect](#what-to-expect)
  - [Troubleshooting](#troubleshooting)
    - ["OpenAI-compatible configuration required"](#openai-compatible-configuration-required)
    - [LM Studio: connection refused or no response](#lm-studio-connection-refused-or-no-response)
    - [Gemini: 401 or "API key not valid"](#gemini-401-or-api-key-not-valid)
    - [RDS or paper file not found](#rds-or-paper-file-not-found)
    - [Analysis produces generic or irrelevant results](#analysis-produces-generic-or-irrelevant-results)
    - [Code execution errors (agent keeps failing the same step)](#code-execution-errors-agent-keeps-failing-the-same-step)

---

## What CellVoyager Does

CellVoyager runs a two-phase loop for each analysis:

1. **Hypothesis generation** — The LLM reads your dataset metadata and biological summary, then proposes a specific analysis idea (e.g. "compare naive-like cell proportions between the PXGL and PXGGA media conditions and test the difference statistically").
2. **Code execution** — The agent writes **R code that runs through monocle3** on the loaded `cell_data_set`, executes it in a Jupyter kernel (via the `rpy2` `%%R` bridge), checks the output, fixes errors, and iterates up to a configurable number of steps.

At the end of each analysis you get a Jupyter notebook (`.ipynb`) with all code, figures, and interpretations. You can run multiple independent analyses in one command, each exploring a different biological angle.

**Available R packages** (the agent is restricted to these — it cannot install others): `monocle3`, `SingleCellExperiment`, `Matrix`, `ggplot2`.

> **Why R?** monocle3 is R-only — there is no Python monocle3. CellVoyager bridges to R with `rpy2` inside one Python Jupyter kernel; the LLM writes R inside `%%R` cells operating on an in-memory `cell_data_set` named `cds`. Because models are generally more fluent in scanpy than in monocle3, CellVoyager injects a dedicated **R/monocle3 skill** ([`cellvoyager/prompts/r_skill.txt`](../cellvoyager/prompts/r_skill.txt)) into the coding guidelines on every run to improve the generated R code. This applies to all models, including Gemini and local models.

---

## Installation

Clone the repository and create the conda environment. CellVoyager's monocle3 backend runs in a dedicated env (`CellVoyager-r`) that bundles the Python orchestrator, `rpy2`, and the R `monocle3` stack together:

```bash
git clone https://github.com/zou-group/CellVoyager.git
cd CellVoyager
conda env create -f environment-monocle3.yml
conda activate CellVoyager-r
```

> **Do not use system R.** On many machines system R segfaults loading the `monocle3` native modules. The `CellVoyager-r` env installs a conda-provided R 4.3 + monocle3 1.3.1 and points `R_HOME` at it. Always run from this env.

---

## Setting Up Your Model

In **legacy mode**, the chosen model both generates hypotheses and writes the R/monocle3 code. The R code still executes in the `CellVoyager-r` kernel regardless of which model wrote it — so you always run from the `CellVoyager-r` env.

### Option A: Local Models via LM Studio

1. Download and install [LM Studio](https://lmstudio.ai).
2. Search for and download a model (llama, gemma, qwen, or any OpenAI-compatible model).
3. Load the model. Before starting the server, **increase the context window**:
   - In the model settings panel, find **Context Length** and set it to at least **32768** (32k). 70k works too if your hardware allows it.
   - CellVoyager sends long prompts that include dataset metadata, the R/monocle3 skill, and prior code — a small context window will cause the model to truncate or fail mid-analysis.
4. Start the local server (default port: `1234`).
5. Set these environment variables before running CellVoyager:

```bash
export OPENAI_BASE_URL=http://localhost:1234/v1
export OPENAI_API_KEY=local
```

You can add these to your shell profile or `.env` file in the project root so you don't have to set them each session:

```
# .env
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_API_KEY=local
```

**Finding the model identifier:** In LM Studio, go to the **Developer** tab (or the running server panel) and look for the model identifier listed under the loaded model — it looks like `google/gemma-4-27b-it` or `lmstudio-community/qwen3-14b`. Use that string exactly as `--model-name`:

```bash
--model-name google/gemma-4-27b-it
--model-name qwen/qwen3-14b
--model-name meta-llama/llama-3.1-8b-instruct
```

### Option B: Google Gemini

1. Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Set the environment variable:

```bash
export GEMINI_API_KEY=your_key_here
```

Or add it to `.env`:

```
GEMINI_API_KEY=your_key_here
```

That key alone is enough for Gemini — you do **not** need `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Available Gemini models:

| Model              | Notes                        |
| ------------------ | ---------------------------- |
| `gemini-2.5-flash` | Fast, good for most analyses |
| `gemini-2.5-pro`   | More capable, slower         |

---

## Preparing Your Inputs

### The Dataset File (.RDS)

CellVoyager reads a **Monocle3 `cell_data_set` saved as an `.RDS` file** — the native single-cell object format in R. (A Bioconductor `SingleCellExperiment` also works, since `cell_data_set` extends it.) Point to it with `--rds-path`; inside the notebook it is loaded once into the R global environment under the variable name `cds`.

The agent automatically extracts a text summary of the object for planning:

| AnnData (scanpy) equivalent | Monocle3 `cds` accessor                |
| --------------------------- | -------------------------------------- |
| `.obs` (cell metadata)      | `colData(cds)`                         |
| `.var` (gene metadata)      | `rowData(cds)` / `fData(cds)`          |
| `.obsm` (embeddings)        | `reducedDims(cds)` (PCA, UMAP, …)      |
| `.X` / layers (expression)  | `assay(cds, "counts")` / `exprs(cds)`  |

No preprocessing required — just point to the file. Note that `dim(cds)` returns `c(n_genes, n_cells)` (genes first), and gene symbols typically live in `rowData(cds)$gene_short_name`.

> **Don't have a `cell_data_set`?** Build one from a matrix with monocle3's `new_cell_data_set(...)`, or convert a Seurat object with `SeuratWrappers::as.cell_data_set(...)`, then `saveRDS(cds, "your_data.RDS")`. Converting from `.h5ad`/Seurat is the user's responsibility and is out of scope for this guide.

### The Dataset Summary File

The dataset summary is a plain `.txt` file that tells the agent what your data contains and what biological questions matter. **This is the most important input for getting useful analyses.** A vague or empty summary leads to generic analyses; a rich summary leads to targeted, biologically meaningful hypotheses.

**What to include:**

- What the dataset is (tissue, organism, experimental conditions, media/protocols)
- How many cells and what cell types are present
- What comparisons are meaningful (e.g. cell line A vs B, condition vs control, timepoints)
- What analyses have already been done (so the agent doesn't repeat them)
- Specific biological questions or pathways you want explored
- Any relevant background from the paper or prior knowledge

**Format:** Free text, organized into paragraphs or labeled sections. Length of 300–1000 words works well. See the [worked example](#worked-example-ipsc-dataset) for a real example shipped with the repo.

---

## Running an Analysis

### Minimal Command

```bash
conda activate CellVoyager-r

python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name MODEL_NAME \
  --rds-path path/to/data.RDS \
  --paper-path path/to/summary.txt \
  --analysis-name my_analysis
```

Replace `MODEL_NAME` with your model (e.g. `gemini-2.5-flash` or `google/gemma-4-27b-it`).

`--rds-path` and `--paper-path` default to the bundled iPSC example, so you can omit them to do a quick test run on the example data.

### All Flags Reference

| Flag                 | Default                                                                 | Description                                                                              |
| -------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `--execution-mode`   | `claude`                                                                | Must be `legacy` for this guide                                                          |
| `--model-name`       | `claude-sonnet-4-6`                                                     | LLM for hypothesis generation and R code writing                                         |
| `--rds-path`         | `example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS` | Path to your Monocle3 `cell_data_set` `.RDS` file                                 |
| `--paper-path`       | `example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt`     | Path to your dataset summary `.txt` file                                          |
| `--analysis-name`    | `iPSC`                                                                  | Name for this run; used in output and log filenames                                      |
| `--num-analyses`     | `1`                                                                     | Number of independent analyses to run sequentially                                       |
| `--max-iterations`   | `8`                                                                     | Max code-generation steps per analysis                                                   |
| `--max-fix-attempts` | `3`                                                                     | Max retries per step when code fails                                                     |
| `--interactive`      | off                                                                     | Pause before each prompt for review/editing (see [Interactive Mode](#interactive-mode)) |
| `--log-prompts`      | off                                                                     | Log full prompts sent to the LLM                                                         |
| `--log-responses`    | off                                                                     | Save each LLM response as a separate `.txt` file                                         |
| `--no-self-critique` | off                                                                     | Disable the agent's self-evaluation step                                                 |
| `--no-vlm`           | off                                                                     | Disable vision/image analysis of generated figures                                      |
| `--no-documentation` | off                                                                     | Disable the R-help documentation lookup used by the fix loop                             |
| `--output-home`      | `.`                                                                     | Base directory for the `outputs/` folder                                                 |
| `--log-home`         | `.`                                                                     | Base directory for the `logs/` folder                                                    |

---

## Interactive Mode

With `--interactive`, the agent pauses before sending each prompt to the LLM and waits for you to press Enter. This lets you review — and optionally edit — the prompt before the model sees it.

**What happens at each pause:**

1. The agent prepares the next prompt and saves it to a `.txt` file.
2. The terminal prints the path to that file:
   ```
   [interactive] Prompt: outputs/my_analysis_20260529/prompts/analysis_0_step2_code.txt
   Press Enter to send (or Ctrl+C to abort)...
   ```
3. Open the file from `outputs/<analysis_name>_<timestamp>/prompts/` in any text editor, read it, and edit it if you want to change what the model receives.
4. Press **Enter** to send the (possibly modified) prompt. The agent continues.

**Tip:** Run with `--interactive --log-prompts --log-responses` to get a full record of every prompt file and its corresponding response alongside your edits.

---

## Logging

CellVoyager writes two log files automatically to `logs/` for every run:

| File                           | Contents                                                          |
| ------------------------------ | ----------------------------------------------------------------- |
| `<name>_log_<timestamp>.log`   | Human-readable log of prompts and outputs                         |
| `<name>_trace_<timestamp>.log` | Chronological trace: planner revisions, executor events, results  |

### Additional logging flags

**`--log-prompts`** — saves each prompt as an individual `.txt` file in `outputs/<analysis_name>_<timestamp>/prompts/`. Useful when debugging why the agent produced unexpected R code (and for inspecting how the R/monocle3 skill appears in context). Also enabled automatically by `--interactive`.

**`--log-responses`** — saves each LLM response as an individual `.txt` file in `outputs/<analysis_name>_<timestamp>/responses/`. Useful for automated pipelines where you want to inspect or replay model outputs. Also enabled automatically by `--interactive`.

Both flags can be used together:

```bash
python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name gemini-2.5-flash \
  --log-prompts \
  --log-responses \
  --rds-path path/to/data.RDS \
  --paper-path path/to/summary.txt \
  --analysis-name my_run
```

---

## Understanding Your Outputs

After a run, CellVoyager creates:

```
outputs/
└── <analysis_name>_<timestamp>/
    ├── <analysis_name>_analysis_1.ipynb   # Jupyter notebook (main output)
    ├── <analysis_name>_analysis_2.ipynb   # If --num-analyses > 1
    ├── .run_config.json                   # Run configuration (for resume)
    ├── snapshots/                         # notebook is saved at every step
        ├── analysis_name_step_details_001.ipynb
    ├── prompts/                           # prompts saved before sending to LLM
        ├── prompt_001.txt
    └── responses/                         # LLM response files (if --log-responses)
        ├── response_001.txt
        └── ...

logs/
├── <analysis_name>_log_<timestamp>.log
└── <analysis_name>_trace_<timestamp>.log
```

**The notebook (`.ipynb`)** is the primary output. The setup cell loads the `cds` via `rpy2`, and each analysis step is an `%%R` code cell plus a markdown interpretation. Open it in Jupyter Lab or Jupyter Notebook to:

- Read the agent's narrative and biological interpretations
- View all figures (rendered inline as PNGs from ggplot2 / `plot_cells`) and tables
- Re-run or modify individual cells
- Extend the analysis manually

**The trace log** is useful for understanding what the agent decided at each step — which hypotheses it considered, what R code it wrote, what errors it encountered, and how it recovered.

---

## Worked Example: iPSC Dataset

This example uses the human iPSC dataset bundled with the repo — a 10X scRNA-seq comparison of two iPSC lines (HL034 vs HL052) cultured in two naive media protocols (PXGL vs PXGGA). It is the default `--rds-path` / `--paper-path`, so you can run it with no data of your own.

### Step 1: The example data

The dataset and its summary already ship in the repo:

- `example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS` — the Monocle3 `cell_data_set`
- `example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt` — the dataset summary

The summary covers:

- Biological background on the two iPSC lines and the naive culture protocols
- The key questions (is HL052 the better line; is PXGL or PXGGA better; off-target/aneuploidy populations)
- Prior observations (e.g. a chr5 trisomy cluster largely from HL034 cells)

This is a good template for writing your own summary.

### Step 2: Run with Gemini

```bash
conda activate CellVoyager-r
export GEMINI_API_KEY=your_key_here   # or put it in .env

python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name gemini-2.5-flash \
  --analysis-name iPSC_gemini
```

(`--rds-path` and `--paper-path` are omitted because the example dataset is the default.)

### Step 3: Run with a local model via LM Studio

```bash
conda activate CellVoyager-r
export OPENAI_BASE_URL=http://localhost:1234/v1
export OPENAI_API_KEY=local

python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name google/gemma-4-27b-it \
  --analysis-name iPSC_local
```

### Step 4: Run interactively with logging

```bash
conda activate CellVoyager-r
export GEMINI_API_KEY=your_key_here

python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name gemini-2.5-flash \
  --interactive \
  --log-prompts \
  --log-responses \
  --analysis-name iPSC_interactive
```

### What to expect

The agent will print its progress to the terminal. A typical run looks like:

```
🚀 Starting CellVoyager Analysis Agent (v2)
   RDS file: example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS
   ...
🔬 Running analyses...
[Hypothesis generation] Generating hypothesis for analysis 1...
[Execution] Step 1/8: Inspecting colData and reducedDims of cds...
[Execution] Step 2/8: plot_cells colored by cell line and media condition...
...
✅ Analysis complete!
```

Open `outputs/iPSC_gemini_<timestamp>/iPSC_gemini_analysis_1.ipynb` in Jupyter to see the results.

---

## Troubleshooting

### "OpenAI-compatible configuration required"

```
❌ Error: OpenAI-compatible configuration required for --execution-mode legacy
```

**Fix:** Set `OPENAI_API_KEY` and optionally `OPENAI_BASE_URL` (for local models), or `GEMINI_API_KEY` (for Gemini):

```bash
# For LM Studio
export OPENAI_BASE_URL=http://localhost:1234/v1
export OPENAI_API_KEY=local

# For Gemini (the API key alone is enough — no OPENAI_API_KEY needed)
export GEMINI_API_KEY=your_key_here
```

### LM Studio: connection refused or no response

The agent hangs or errors with a connection message.

**Fix checklist:**

- Is LM Studio running? Check the app is open and the server is started (green indicator).
- Is a model loaded? LM Studio requires you to explicitly load a model before starting the server.
- Is the port correct? Default is `1234`. Check LM Studio's server settings and update `OPENAI_BASE_URL` if different.
- Is `OPENAI_BASE_URL` set to `http://localhost:1234/v1` (with `/v1` at the end)?

### Gemini: 401 or "API key not valid"

```
❌ Error: 401 API key not valid
```

**Fix:** Check your `GEMINI_API_KEY` is set correctly and hasn't expired. You can verify with:

```bash
echo $GEMINI_API_KEY
```

### RDS or paper file not found

```
❌ Error: RDS file not found: path/to/data.RDS
```

**Fix:** Use the absolute path or verify the relative path from the `CellVoyager/` directory:

```bash
ls path/to/data.RDS   # confirm the file exists
```

Also confirm the file is a Monocle3 `cell_data_set` (or `SingleCellExperiment`) saved with `saveRDS(...)` — not a Seurat object or a raw matrix.

### Analysis produces generic or irrelevant results

The agent generates hypotheses it has seen before or ignores key aspects of your data.

**Fix:** Improve your dataset summary file:

- Add specific cell types, conditions, and comparisons present in your dataset
- List analyses you've already done so the agent doesn't repeat them
- Add explicit biological questions: _"I want to understand whether PXGGA produces fewer off-target neuronal cells than PXGL in HL052"_
- Include relevant pathway names or gene sets you care about

### Code execution errors (agent keeps failing the same step)

The agent hits `max-fix-attempts` (default 3) and moves on or stops.

**Fix options:**

- Run with `--interactive` to review the failing R code at each step and give corrective feedback
- Run with `--log-prompts` to inspect what context the agent had when it wrote the failing code
- Increase `--max-fix-attempts 5` to give the agent more retries
- Remember the agent is restricted to `monocle3`, `SingleCellExperiment`, `Matrix`, and `ggplot2` — a hypothesis that needs another package will keep failing. Steer it toward analyses those packages support.
- If R itself fails to load (segfaults, arch mismatch, missing `monocle3`), you are likely not in the `CellVoyager-r` env — `conda activate CellVoyager-r` and retry.
