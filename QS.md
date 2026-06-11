Quick Start

- Create/activate the env (unified Python orchestrator + R monocle3 backend, bridged via rpy2):
  - conda env create -f environment-monocle3.yml
  - conda activate CellVoyager-r

If you want local models

- Start your OpenAI-compatible server, then set:
  - export OPENAI_BASE_URL=http://localhost:11434/v1
  - export OPENAI_API_KEY=local

CLI: local planning + legacy execution

- Run:
  - python run_cellvoyager.py --execution-mode legacy --model-name llama3.1 --rds-path path/to/data.RDS --paper-path path/to/summary.txt --analysis-name my_run

GUI

- Start:
  - streamlit run gui/app.py
- Important: the GUI still uses Claude for execution, so it also needs:
  - export ANTHROPIC_API_KEY=...

If you want fully local

- Use the CLI with --execution-mode legacy.
- The current GUI is not fully local because execution still depends on Anthropic.

Example

The example iPSC Monocle3 cell_data_set ships in the repo and is the CLI default, so you can
omit --rds-path/--paper-path entirely. To pass them explicitly:

- export OPENAI_BASE_URL=http://localhost:11434/v1
- export OPENAI_API_KEY=local
- export GEMINI_API_KEY=ASK_FOR_THE_KEY
- python run_cellvoyager.py --execution-mode legacy --model-name llama3.1 --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" --analysis-name local_test

```bash
  export ANTHROPIC_BASE_URL=http://localhost:1234
  export ANTHROPIC_API_KEY=sk-no-key-required
  export GEMINI_API_KEY=ASK_FOR_THE_KEY
```

```bash
python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name google/gemma-4-26b-a4b \
 --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" \
 --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" \
 --analysis-name local_test

~/miniconda3/envs/CellVoyager-r/bin/python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name qwen/qwen3.5-35b-a3b\
 --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" \
 --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" \
 --analysis-name local_test
```

Run Gemini. requires GEMINI_API_KEY

```bash
python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name gemini-2.5-flash\
 --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" \
 --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" \
 --analysis-name local_test

python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name gemini-2.5-pro\
 --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" \
 --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" \
 --analysis-name local_test

```

log prompts and responses - this could be useful for automated pipelines. should work with any model

```bash
~/miniconda3/envs/CellVoyager-r/bin/python run_cellvoyager.py \
  --log-prompts \
  --log-responses \
  --execution-mode legacy \
  --model-name gemini-2.5-flash\
  --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" \
  --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" \
  --analysis-name 20260528_gemini_api
```

run in interactive mode. it will save the prompt for you to review. It will be a lot of prompts!

```bash
~/miniconda3/envs/CellVoyager-r/bin/python run_cellvoyager.py \
  --interactive \
  --log-prompts \
  --log-responses \
  --execution-mode legacy \
  --model-name gemini-2.5-flash\
  --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" \
  --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" \
  --analysis-name 20260528_gemini_api
```

(I'm not sure this is working yet)

```bash
~/miniconda3/envs/CellVoyager-r/bin/python run_cellvoyager.py \
 --execution-mode opencode \
 --model-name google/gemma-4-26b-a4b --execution-model google/gemma-4-26b-a4b \
 --rds-path "example/iPSC_dataset/HL052vHL043_PXGL_PXGGA_updated_processed_annotated.RDS" \
 --paper-path "example/iPSC_dataset/HL052 and HL034 PXGL and PXGGA 10X comparisons.txt" \
 --analysis-name local_test
```
