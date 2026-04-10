# AGENTS.md

## Scope
- This file applies to the entire repository.

## Project State
- CellVoyager now supports two model families only:
  - `Anthropic` for Claude-specific execution paths.
  - `OpenAI-compatible` models for planning and legacy execution, including local servers via `OPENAI_BASE_URL` or `OPENAI_API_BASE`.
- `LiteLLM`, `instructor`, and Moonshot/Kimi support have been removed from the repo.
- Hypothesis generation in `cellvoyager/hypothesis.py` now uses direct SDK calls plus JSON prompting/parsing with `pydantic` validation.

## Important Constraints
- `--execution-mode claude` still requires `ANTHROPIC_API_KEY`.
- `--execution-mode legacy` requires an OpenAI-compatible configuration:
  - `OPENAI_API_KEY`, or
  - `OPENAI_BASE_URL` / `OPENAI_API_BASE` pointing at a compatible local server.
- `DeepResearch` is OpenAI-specific and still requires a real `OPENAI_API_KEY`.
- The GUI still requires `ANTHROPIC_API_KEY` because execution uses Claude even when hypothesis generation uses an OpenAI-compatible model.

## Key Files
- `cellvoyager/llm_utils.py`: shared provider detection and OpenAI-compatible client helpers.
- `cellvoyager/hypothesis.py`: planner/hypothesis generation; uses direct SDK calls plus JSON prompting/parsing.
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

## Validation
- After Python changes, run targeted syntax checks with `python -m py_compile` on touched files.
- When changing model routing or setup, verify README/help text stays consistent with the actual code paths.
