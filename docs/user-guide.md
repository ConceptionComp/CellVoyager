# CellVoyager User Guide

CellVoyager is an AI agent for automated single-cell RNA sequencing (scRNA-seq) analysis. You give it a dataset and biological context; it generates hypotheses, writes Python analysis code, executes it in a Jupyter notebook, and iterates — producing a fully documented notebook you can explore, extend, and build on.

This guide covers everything you need to run CellVoyager from the terminal using **legacy mode**, which works with local models via LM Studio and with Google Gemini.

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
    - [The Dataset File (.h5ad)](#the-dataset-file-h5ad)
    - [Converting an RDS to h5ad](#converting-an-rds-to-h5ad)
    - [The Dataset Summary File](#the-dataset-summary-file)
  - [Running an Analysis](#running-an-analysis)
    - [Minimal Command](#minimal-command)
    - [All Flags Reference](#all-flags-reference)
  - [Interactive Mode](#interactive-mode)
  - [Logging](#logging)
    - [Additional logging flags](#additional-logging-flags)
  - [Understanding Your Outputs](#understanding-your-outputs)
  - [Worked Example: COVID-19 Dataset](#worked-example-covid-19-dataset)
    - [Step 1: Download the dataset](#step-1-download-the-dataset)
    - [Step 2: Run with Gemini](#step-2-run-with-gemini)
    - [Step 3: Run with a local model via LM Studio](#step-3-run-with-a-local-model-via-lm-studio)
    - [Step 4: Run interactively with logging](#step-4-run-interactively-with-logging)
    - [What to expect](#what-to-expect)
  - [Troubleshooting](#troubleshooting)
    - ["OpenAI-compatible configuration required"](#openai-compatible-configuration-required)
    - [LM Studio: connection refused or no response](#lm-studio-connection-refused-or-no-response)
    - [Gemini: 401 or "API key not valid"](#gemini-401-or-api-key-not-valid)
    - [H5AD or paper file not found](#h5ad-or-paper-file-not-found)
    - [Analysis produces generic or irrelevant results](#analysis-produces-generic-or-irrelevant-results)
    - [Code execution errors (agent keeps failing the same step)](#code-execution-errors-agent-keeps-failing-the-same-step)

---

## What CellVoyager Does

CellVoyager runs a two-phase loop for each analysis:

1. **Hypothesis generation** — The LLM reads your dataset metadata and biological summary, then proposes a specific analysis idea (e.g. "compare NK cell subsets between severe and mild COVID-19 patients using differential expression").
2. **Code execution** — The agent writes Python code using scanpy, seaborn, pandas, and related packages, runs it in a Jupyter kernel, checks the output, fixes errors, and iterates up to a configurable number of steps.

At the end of each analysis you get a Jupyter notebook (`.ipynb`) with all code, figures, and interpretations. You can run multiple independent analyses in one command, each exploring a different biological angle.

**Available packages:** scanpy, anndata, matplotlib, numpy, seaborn, pandas, scipy, harmonypy, bbknn

---

## Installation

Clone the repository and create the conda environment:

```bash
git clone https://github.com/zou-group/CellVoyager.git
cd CellVoyager
conda env create -f environment.yml
conda activate CellVoyager
```

---

## Setting Up Your Model

### Option A: Local Models via LM Studio

1. Download and install [LM Studio](https://lmstudio.ai).
2. Search for and download a model (llama, gemma, qwen, or any OpenAI-compatible model).
3. Load the model. Before starting the server, **increase the context window**:
   - In the model settings panel, find **Context Length** and set it to at least **32768** (32k). 70k works too if your hardware allows it.
   - CellVoyager sends long prompts that include dataset metadata and prior code — a small context window will cause the model to truncate or fail mid-analysis.
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

Available Gemini models:

| Model              | Notes                        |
| ------------------ | ---------------------------- |
| `gemini-2.5-flash` | Fast, good for most analyses |
| `gemini-2.5-pro`   | More capable, slower         |

---

## Preparing Your Inputs

### The Dataset File (.h5ad)

CellVoyager reads AnnData `.h5ad` files — the standard format for scRNA-seq data in Python. If your data is in R (Seurat or Monocle3), convert it first (see [Converting an RDS to h5ad](#converting-an-rds-to-h5ad)).

The agent automatically extracts:

- Cell metadata (`obs` columns: cell types, conditions, donors, etc.)
- Gene names (`var`)
- Embeddings (`obsm`: UMAP, PCA, etc.)
- Any pre-computed results stored in `uns`

No preprocessing required — just point to the file.

### Converting an RDS to h5ad

If your data lives in an `.RDS` file (a Monocle3 `cell_data_set` or a Bioconductor `SingleCellExperiment`), convert it with the helper utility in this repo:

- [`cellvoyager/rds_to_h5ad.R`](../cellvoyager/rds_to_h5ad.R) — reads the object and exports its matrix, cell/gene metadata, and embeddings
- [`cellvoyager/_assemble_h5ad.py`](../cellvoyager/_assemble_h5ad.py) — assembles those pieces into the `.h5ad`

The conversion runs in its own conda environment (kept separate from the main `CellVoyager` env). Create it once from the spec in this repo:

```bash
conda env create -f environment-rds2h5ad.yml
```

Then run the conversion:

```bash
conda activate rds2h5ad
export RDS2H5AD_PYTHON="$(which python)"   # the env's python (has anndata)
Rscript cellvoyager/rds_to_h5ad.R <input.RDS> <output.h5ad> [X_assay_name]
```

The result preserves the count matrix (`X`), cell metadata (`obs`), gene metadata (`var`), and reduced-dimension embeddings (`obsm`, e.g. `X_pca`, `X_umap`).

> **Why a dedicated env?** On some machines the system R segfaults loading the `monocle3` / `RcppAnnoy` native modules. The isolated env plus the stub-class trick in `rds_to_h5ad.R` lets `readRDS` reconstruct the object without ever loading those packages.
>
> **Seurat objects** are not handled by this utility — convert those with `SeuratDisk` or `sceasy` instead.

**Note on gene names:** if your object uses Ensembl IDs as row names, those become `var_names` (the gene index), with gene symbols available in a `var` column such as `gene_short_name`.

### The Dataset Summary File

The dataset summary is a plain `.txt` file that tells the agent what your data contains and what biological questions matter. **This is the most important input for getting useful analyses.** A vague or empty summary leads to generic analyses; a rich summary leads to targeted, biologically meaningful hypotheses.

**What to include:**

- What the dataset is (tissue, organism, experimental conditions)
- How many cells and what cell types are present
- What comparisons are meaningful (e.g. disease vs control, timepoints)
- What analyses have already been done (so the agent doesn't repeat them)
- Specific biological questions or pathways you want explored
- Any relevant background from the paper or prior knowledge

**Format:** Free text, organized into paragraphs or labeled sections. Length of 300–1000 words works well. See the [worked example](#worked-example-covid-19-dataset) for a real example.

---

## Running an Analysis

### Minimal Command

```bash
python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name MODEL_NAME \
  --h5ad-path path/to/data.h5ad \
  --paper-path path/to/summary.txt \
  --analysis-name my_analysis
```

Replace `MODEL_NAME` with your model (e.g. `gemini-2.5-flash` or `google/gemma-4-27b-it`).

### All Flags Reference

| Flag                 | Default                       | Description                                                                             |
| -------------------- | ----------------------------- | --------------------------------------------------------------------------------------- |
| `--execution-mode`   | `claude`                      | Must be `legacy` for this guide                                                         |
| `--model-name`       | `claude-sonnet-4-6`           | LLM for hypothesis generation and code writing                                          |
| `--h5ad-path`        | `example/covid19.h5ad`        | Path to your `.h5ad` dataset                                                            |
| `--paper-path`       | `example/covid19_summary.txt` | Path to your dataset summary `.txt` file                                                |
| `--analysis-name`    | `covid19`                     | Name for this run; used in output and log filenames                                     |
| `--num-analyses`     | `1`                           | Number of independent analyses to run sequentially                                      |
| `--max-iterations`   | `8`                           | Max code-generation steps per analysis                                                  |
| `--max-fix-attempts` | `3`                           | Max retries per step when code fails                                                    |
| `--interactive`      | off                           | Pause before each prompt for review/editing (see [Interactive Mode](#interactive-mode)) |
| `--log-prompts`      | off                           | Log full prompts sent to the LLM                                                        |
| `--log-responses`    | off                           | Save each LLM response as a separate `.txt` file                                        |
| `--no-self-critique` | off                           | Disable the agent's self-evaluation step                                                |
| `--no-vlm`           | off                           | Disable vision/image analysis                                                           |
| `--no-documentation` | off                           | Disable automatic code documentation                                                    |
| `--output-home`      | `.`                           | Base directory for the `outputs/` folder                                                |
| `--log-home`         | `.`                           | Base directory for the `logs/` folder                                                   |

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

| File                           | Contents                                                         |
| ------------------------------ | ---------------------------------------------------------------- |
| `<name>_log_<timestamp>.log`   | Human-readable log of prompts and outputs                        |
| `<name>_trace_<timestamp>.log` | Chronological trace: planner revisions, executor events, results |

### Additional logging flags

**`--log-prompts`** — saves each prompt as an individual `.txt` file in `outputs/<analysis_name>_<timestamp>/prompts/`. Useful when debugging why the agent produced unexpected code. Also enabled automatically by `--interactive`.

**`--log-responses`** — saves each LLM response as an individual `.txt` file in `outputs/<analysis_name>_<timestamp>/responses/`. Useful for automated pipelines where you want to inspect or replay model outputs. Also enabled automatically by `--interactive`.

Both flags can be used together:

```bash
python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name gemini-2.5-flash \
  --log-prompts \
  --log-responses \
  --h5ad-path path/to/data.h5ad \
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

**The notebook (`.ipynb`)** is the primary output. Open it in Jupyter Lab or Jupyter Notebook to:

- Read the agent's narrative and biological interpretations
- View all figures and tables
- Re-run or modify individual cells
- Extend the analysis manually

**The trace log** is useful for understanding what the agent decided at each step — which hypotheses it considered, what code it wrote, what errors it encountered, and how it recovered.

---

## Worked Example: COVID-19 Dataset

This example uses the COVID-19 peripheral blood dataset from [Wilk et al. 2020](https://www.nature.com/articles/s41591-020-0944-y).

### Step 1: Download the dataset

```bash
curl -o example/covid19.h5ad "https://hosted-matrices-prod.s3-us-west-2.amazonaws.com/Single_cell_atlas_of_peripheral_immune_response_to_SARS_CoV_2_infection-25/Single_cell_atlas_of_peripheral_immune_response_to_SARS_CoV_2_infection.h5ad"
```

The dataset summary is already included at `example/covid19_summary.txt`. It contains:

- Biological background on severe COVID-19 and ARDS
- Description of the 44,721 peripheral blood cells and 8 donors
- Summary of prior computational analyses (clustering, DE, RNA velocity)
- Specific hypotheses about monocyte–T cell communication and interferon signaling

This is a good template for writing your own summary.

### Step 2: Run with Gemini

```bash
export GEMINI_API_KEY=your_key_here

python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name gemini-2.5-flash \
  --h5ad-path example/covid19.h5ad \
  --paper-path example/covid19_summary.txt \
  --analysis-name covid19_test
```

### Step 3: Run with a local model via LM Studio

```bash
export OPENAI_BASE_URL=http://localhost:1234/v1
export OPENAI_API_KEY=local

python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name google/gemma-4-27b-it \
  --h5ad-path example/covid19.h5ad \
  --paper-path example/covid19_summary.txt \
  --analysis-name covid19_local
```

### Step 4: Run interactively with logging

```bash
export GEMINI_API_KEY=your_key_here

python run_cellvoyager.py \
  --execution-mode legacy \
  --model-name gemini-2.5-flash \
  --interactive \
  --log-prompts \
  --log-responses \
  --h5ad-path example/covid19.h5ad \
  --paper-path example/covid19_summary.txt \
  --analysis-name covid19_interactive
```

### What to expect

The agent will print its progress to the terminal. A typical run looks like:

```
🚀 Starting CellVoyager Analysis Agent (v2)
   H5AD file: example/covid19.h5ad
   ...
🔬 Running analyses...
[Hypothesis generation] Generating hypothesis for analysis 1...
[Execution] Step 1/8: Loading data and computing QC metrics...
[Execution] Step 2/8: UMAP visualization by cell type and condition...
...
✅ Analysis complete!
```

Open `outputs/covid19_test_<timestamp>/covid19_test_analysis_1.ipynb` in Jupyter to see the results.

---

## Troubleshooting

### "OpenAI-compatible configuration required"

```
❌ Error: OpenAI-compatible configuration required for --execution-mode legacy
```

**Fix:** Set `OPENAI_API_KEY` and optionally `OPENAI_BASE_URL`:

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

### H5AD or paper file not found

```
❌ Error: H5AD file not found: path/to/data.h5ad
```

**Fix:** Use the absolute path or verify the relative path from the `CellVoyager/` directory:

```bash
ls path/to/data.h5ad   # confirm the file exists
```

### Analysis produces generic or irrelevant results

The agent generates hypotheses it has seen before or ignores key aspects of your data.

**Fix:** Improve your dataset summary file:

- Add specific cell types, conditions, and comparisons present in your dataset
- List analyses you've already done so the agent doesn't repeat them
- Add explicit biological questions: _"I want to understand why CD16+ monocytes are depleted in ARDS patients"_
- Include relevant pathway names or gene sets you care about

### Code execution errors (agent keeps failing the same step)

The agent hits `max-fix-attempts` (default 3) and moves on or stops.

**Fix options:**

- Run with `--interactive` to review the failing code at each step and give corrective feedback
- Run with `--log-prompts` to inspect what context the agent had when it wrote the failing code
- Increase `--max-fix-attempts 5` to give the agent more retries
- If a specific package is missing, install it in the `CellVoyager` conda environment and restart
