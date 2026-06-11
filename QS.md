Quick Start

- Create/activate the env:
  - conda env create -f environment.yml
  - conda activate CellVoyager

If you want local models

- Start your OpenAI-compatible server, then set:
  - export OPENAI_BASE_URL=http://localhost:11434/v1
  - export OPENAI_API_KEY=local

CLI: local planning + legacy execution

- Run:
  - python run_cellvoyager.py --execution-mode legacy --model-name llama3.1 --h5ad-path path/to/data.h5ad --paper-path path/to/summary.txt --analysis-name my_run

GUI

- Start:
  - streamlit run gui/app.py
- Important: the GUI still uses Claude for execution, so it also needs:
  - export ANTHROPIC_API_KEY=...

If you want fully local

- Use the CLI with --execution-mode legacy.
- The current GUI is not fully local because execution still depends on Anthropic.

Example

- export OPENAI_BASE_URL=http://localhost:11434/v1
- export OPENAI_API_KEY=local
- export GEMINI_API_KEY=ASK_FOR_THE_KEY
- python run_cellvoyager.py --execution-mode legacy --model-name llama3.1 --h5ad-path example/covid19.h5ad --paper-path example/covid19_summary.txt --analysis-name local_test

```bash
  export ANTHROPIC_BASE_URL=http://localhost:1234
  export ANTHROPIC_API_KEY=sk-no-key-required
  export GEMINI_API_KEY=ASK_FOR_THE_KEY
```

```bash
python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name google/gemma-4-26b-a4b \
 --h5ad-path example/HFTA_v2_germ_XX.h5ad \
 --paper-path example/covid19_summary.txt \
 --analysis-name local_test

~/miniconda3/envs/CellVoyager/bin/python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name qwen/qwen3.5-35b-a3b\
 --h5ad-path example/HFTA_v2_germ_XX.h5ad \
 --paper-path example/covid19_summary.txt \
 --analysis-name local_test
```

Run Gemini. requires GEMINI_API_KEY

```bash
python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name gemini-2.5-flash\
 --h5ad-path example/HFTA_v2_germ_XX.h5ad \
 --paper-path example/covid19_summary.txt \
 --analysis-name local_test

python run_cellvoyager.py \
  --execution-mode legacy \
 --model-name gemini-2.5-pro\
 --h5ad-path example/HFTA_v2_germ_XX.h5ad \
 --paper-path example/covid19_summary.txt \
 --analysis-name local_test

```

log prompts and responses - this could be useful for automated pipelines. should work with any model

```bash
~/miniconda3/envs/CellVoyager/bin/python run_cellvoyager.py \
  --log-prompts \
  --log-responses \
  --execution-mode legacy \
  --model-name gemini-2.5-flash\
  --h5ad-path example/HFTA_v2_germ_XX.h5ad \
  --paper-path example/covid19_summary.txt \
  --analysis-name 20260528_gemini_api
```

run in interactive mode. it will save the prompt for you to review. It will be a lot of prompts!

```bash
~/miniconda3/envs/CellVoyager/bin/python run_cellvoyager.py \
  --interactive \
  --log-prompts \
  --log-responses \
  --execution-mode legacy \
  --model-name gemini-2.5-flash\
  --h5ad-path example/HFTA_v2_germ_XX.h5ad \
  --paper-path example/covid19_summary.txt \
  --analysis-name 20260528_gemini_api
```

(I'm not sure this is working yet)

```bash
~/miniconda3/envs/CellVoyager/bin/python run_cellvoyager.py \
 --execution-mode opencode \
 --model-name google/gemma-4-26b-a4b --execution-model google/gemma-4-26b-a4b \
 --h5ad-path example/HFTA_v2_germ_XX.h5ad \
 --paper-path example/covid19_summary.txt \
 --analysis-name local_test
```
