<div align="center">
<img src="gui/assets/logo.jpeg" alt="CellVoyager Logo" width="700">
</div>

# Demo
To try out the CellVoyager UI, check out the [CellVoyager demo](https://cellvoyager.org).

For a full walkthrough of CLI usage, model setup, inputs, outputs, and troubleshooting, see the [User Guide](docs/user-guide.md).

*Note: because of memory constraints, this uses a pre-loaded dataset + dataset summary (the example iPSC Monocle3 `cell_data_set`).*

# Installation

Clone the repository and create the conda environment:

```bash
git clone https://github.com/zou-group/CellVoyager.git
cd CellVoyager
conda env create -f environment-monocle3.yml
conda activate CellVoyager-r
```

CellVoyager runs single-cell analyses with **monocle3 in R**, bridged into Python via
`rpy2`. The `CellVoyager-r` environment bundles both the Python orchestrator and the R
monocle3 backend in a single conda env (see `environment-monocle3.yml`).

CellVoyager requires API keys depending on which models you use. Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-xxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxx
```

For local OpenAI-compatible models (for example via Ollama, vLLM, or LM Studio), set:

```
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=local
```

`OPENAI_BASE_URL` works for hypothesis generation and for `--execution-mode legacy` or `--execution-mode opencode`. Claude execution still requires `ANTHROPIC_API_KEY`.

# Usage

## GUI (Recommended)

```bash
streamlit run gui/app.py
```

This opens a browser-based interface where you can upload datasets, configure settings, and monitor analyses in real time. 

It also simulatenously builds a Jupyter notebook in your `outputs/` folder.

### GUI Home Screen

<img width="1881" height="906" alt="image" src="https://github.com/user-attachments/assets/bb78208d-fe36-4b5b-a8f1-4f9eff1fb34e" />


| Input | Description |
|---|---|
| Dataset | Drag and drop a `.RDS` Monocle3 `cell_data_set` (CDS) file from your computer |
| Dataset Summary | A summary for the dataset you are inputting (e.g. which diseases, tissues, etc. are in the dataset) |
| Past Analyses Tried | Any analyses that you've already conducted and want the agent to build on top of |
| Directions to Focus On | Guides the agent on which general topics you want to explor (e.g. IL-17 pathway genes) |
| Additional Biological Background | Any biological background you think would benefit the agent |
| Analysis Name | The name under which your analysis will be saved in the `outputs/` folder |
| Analyses | How many analyses you want the agent to conduct |
| Max steps per analysis | Maximum number of steps for each analysis (can always extend during the analysis if needed) |
| Interactive mode | Pauses at every N steps (specified below the checkbox) for user feedback; recommended to have on |
| Notify | Plays a notification sound when the agent is ready for user feedback |
| DeepResearch | Whether or not to call OpenAI's DeepResearch agent to get additional biological background prior to idea generation |
| Execution model | Select from the options which LLM to use for all code generation |
| Hypothesis generation model | Select the Anthropic or OpenAI-compatible LLM to use for hypothesis generation |

### GUI Interactive Screen

<img width="2982" height="1422" alt="image" src="https://github.com/user-attachments/assets/ece22db1-dcb9-4779-a5ac-51b0301afb51" />


| Input | Description |
|---|---|
| Feedback for the agent | Any feedback you want to give the agent for its next N steps |
| Continue Analysis | Continues the analysis with any provided feedback |
| Edit Analysis | Lets you edit, insert, and run code cells. Ideal for manually extending/exploring an agent's analysis |
| Finish Analysis | Instructs the agent to wrap up the analysis and summarize its findings |
| Chat with Agent | Live chatbox (one for each analysis) with the agent |


## Terminal

```bash
python run_cellvoyager.py --rds-path PATH_TO_RDS_DATASET \
                          --paper-path PATH_TO_PAPER_SUMMARY \
                          --analysis-name RUN_NAME
```

| Argument | Description |
|---|---|
| `--rds-path` | Path to the Monocle3/SCE `.RDS` file (`cell_data_set`) |
| `--paper-path` | Path to a `.txt` file containing a summary of the paper / biological context |
| `--analysis-name` | Name for the analysis output directory |
| `--execution-mode` | `claude` (default), `legacy`, or `opencode` |
| `--model-name` | LLM for hypothesis generation (default: `claude-sonnet-4-6`) |
| `--num-analyses` | Number of analyses to run (default: 1) |
| `--max-iterations` | Max iterations per analysis (default: 8) |
| `--interactive` | Pause after each step so you can edit the notebook in Jupyter |
| `--log-prompts` | Log full prompt/response bodies; all runs also write an analysis trace file under `logs/` |

Run `python run_cellvoyager.py --help` for the full list of options.

Each run writes two log files under `logs/` by default:

- `<analysis_name>_log_<timestamp>.log`: human-readable prompt/output log
- `<analysis_name>_trace_<timestamp>.log`: chronological analysis trace with planner revisions, executor/tool events, and result summaries

To use a local OpenAI-compatible model for planning and legacy execution:

```bash
export OPENAI_BASE_URL=http://localhost:11434/v1
export OPENAI_API_KEY=local
python run_cellvoyager.py --execution-mode legacy \
                          --model-name llama3.1 \
                          --rds-path PATH_TO_RDS_DATASET \
                          --paper-path PATH_TO_PAPER_SUMMARY \
                          --analysis-name RUN_NAME
```

To use a local OpenAI-compatible model for both planning and the live notebook executor:

```bash
export OPENAI_BASE_URL=http://localhost:1234/v1
export OPENAI_API_KEY=local
python run_cellvoyager.py --execution-mode opencode \
                          --model-name google/gemma-4-26b-a4b \
                          --execution-model google/gemma-4-26b-a4b \
                          --rds-path PATH_TO_RDS_DATASET \
                          --paper-path PATH_TO_PAPER_SUMMARY \
                          --analysis-name RUN_NAME
```

`opencode` is CLI-first and uses an OpenAI-compatible model to emit notebook actions directly. Resume mode still only supports `claude` execution.

The agent will work in a live Jupyter notebook and the user can interact with the agent via the terminal (if `--interactive` is enabled).

# Example

A ready-to-run example iPSC dataset ships with the repo as a Monocle3 `cell_data_set`:

- `example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS` — the CDS
- `example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt` — the dataset summary

These are the CLI defaults, so running `python run_cellvoyager.py` with no `--rds-path`/
`--paper-path` uses them directly. Otherwise either run the GUI and drag the `.RDS` into it,
or point `--rds-path` at your own Monocle3/SCE `.RDS` file.

# CellBench

To run base LLMs (gpt-4o, o3-mini) 3x on CellBench:

```
cd CellBench
python run_base_llm.py
python run_llm_judge.py
```

To run agent 3x on CellBench:

```
cd CellBench
python run_agent.py {gpt-4o|o3-mini}
```

Metrics should be printed to stdout and saved in the `responses` and `judged` dirs.
