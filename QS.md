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
- python run_cellvoyager.py --execution-mode legacy --model-name llama3.1 --h5ad-path example/covid19.h5ad --paper-path example/covid19_summary.txt --analysis-name local_test
