# AGENTS.md

## Scope
- This file applies to the entire repository.

## Project State
- CellVoyager's analysis backend is **monocle3 in R**, bridged into the Python Jupyter
  kernel via `rpy2` (the LLM writes R through `%%R` / `rpy2.robjects`); there is no Python
  monocle3. Input data is a native Monocle3/SCE `.RDS` (`cell_data_set`, canonical handle
  `cds`), passed via `--rds-path`. The conda env is `CellVoyager-r`, created from
  `environment-monocle3.yml` (a single env bundling the Python orchestrator and the R
  monocle3 backend — do **not** use system R, it segfaults loading monocle3 native
  modules). Migration details: `docs/specs/monocle3-migration.md`.
- CellVoyager now supports two model families only:
  - `Anthropic` for Claude-specific execution paths.
  - `OpenAI-compatible` models for planning and legacy execution, including local servers via `OPENAI_BASE_URL` or `OPENAI_API_BASE`.
- `LiteLLM`, `instructor`, and Moonshot/Kimi support have been removed from the repo.
- Hypothesis generation in `cellvoyager/hypothesis.py` now uses direct SDK calls plus JSON prompting/parsing with `pydantic` validation.
- Planner prompts were tightened to require a single JSON object with no extra commentary, and the OpenAI-compatible planner path now attempts `response_format={"type":"json_object"}` with fallback for servers that do not support it.
- Legacy execution now prints high-level progress milestones to stdout and writes incremental notebook snapshots under `output_dir/snapshots/` during a run.
- The GUI execution path still launches `run_cellvoyager.py --execution-mode claude`.
- GUI model dropdowns now include `google/gemma-4-26b-a4b` and `qwen/qwen3.5-35b-a3b`.
- `cellvoyager/llm_utils.py` treats `google/`, `qwen/`, `meta-llama/`, `deepseek/`, and `mistralai/` model IDs as `OpenAI-compatible`.
- `environment-monocle3.yml` (env `CellVoyager-r`) is the canonical environment for the
  monocle3 backend; it includes the `markdown` package required by `gui/common.py`.

## Important Constraints
- `--execution-mode claude` still requires `ANTHROPIC_API_KEY`.
- `--execution-mode legacy` requires an OpenAI-compatible configuration:
  - `OPENAI_API_KEY`, or
  - `OPENAI_BASE_URL` / `OPENAI_API_BASE` pointing at a compatible local server.
- `DeepResearch` is OpenAI-specific and still requires a real `OPENAI_API_KEY`.
- The GUI still requires `ANTHROPIC_API_KEY` because execution uses Claude even when hypothesis generation uses an OpenAI-compatible model.
- The raw `.RDS` file stays local in GUI runs, but dataset summaries, context text, notebook content, and output previews can still be sent to the configured model backend.
- GUI helper features in `gui/common.py` and `cellvoyager/execution/claude.py` still prefer Anthropic first for pause summaries and chat if `ANTHROPIC_API_KEY` is set, even when the main run is otherwise local.

## Key Files
- `cellvoyager/llm_utils.py`: shared provider detection and OpenAI-compatible client helpers.
- `cellvoyager/hypothesis.py`: planner/hypothesis generation; uses direct SDK calls plus JSON prompting/parsing.
- `cellvoyager/execution/legacy.py`: legacy notebook executor; now emits concise CLI progress and saves in-progress notebook snapshots.
- `cellvoyager/execution/claude.py`: Claude Code / Agent SDK executor plus local notebook MCP tools.
- `run_cellvoyager.py`: main CLI entry point.
- `gui/app.py`: main GUI and provider validation logic.
- `gui/common.py`: GUI helper API calls; prefers Anthropic first, then OpenAI-compatible.
- `README.md`: user-facing setup and run instructions.

## Working Guidelines
- Keep provider logic centralized in `cellvoyager/llm_utils.py`.
- Prefer `OpenAI-compatible` wording for generic OpenAI-style endpoints.
- Only use `OpenAI` wording for truly OpenAI-specific features such as DeepResearch.
- Do not reintroduce provider-specific abstractions or extra provider support unless explicitly requested.
- Keep changes minimal and consistent with the current simplified provider model.
- When adding new local-model presets to the GUI, also update provider detection in `cellvoyager/llm_utils.py` so validation and labels stay correct.
- Prompt templates in `cellvoyager/prompts/*.txt` are formatted with Python `.format(...)`; escape literal braces as `{{` and `}}`.
- For planner/output-shape changes, keep prompt instructions and parser expectations aligned so retries and repair prompts match the actual schema.

## Validation
- After Python changes, run targeted syntax checks with `python -m py_compile` on touched files.
- When changing model routing or setup, verify README/help text stays consistent with the actual code paths.
