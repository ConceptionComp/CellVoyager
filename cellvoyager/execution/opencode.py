"""
OpenAI-compatible notebook executor.

This executor provides a local, tool-using notebook loop for CellVoyager without
depending on the Claude agent SDK. It asks an OpenAI-compatible model to emit a
single JSON action per turn, executes the requested notebook operation, then
feeds the tool result back into the model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import nbformat as nbf
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

from cellvoyager.execution.claude import FileLogger, NotebookSession, strip_code_fences
from cellvoyager.llm_utils import create_openai_client


_ACTION_NAMES = {
    "read_notebook",
    "read_cell",
    "insert_cell",
    "overwrite_cell_source",
    "execute_cell",
    "insert_execute_code_cell",
    "restart_kernel",
    "finish",
}


def _extract_json_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    raise ValueError("No JSON object found in model response")


def _trim_json(payload: Any, limit: int = 8000) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


class OpenCodeJupyterExecutor:
    """
    OpenAI-compatible notebook executor for agent_v2.

    This is CLI-oriented and currently does not implement the Claude-style GUI
    pause / resume workflow.
    """

    def __init__(
        self,
        *,
        logger,
        output_dir,
        h5ad_path,
        adata_summary,
        paper_summary,
        coding_guidelines,
        analysis_name,
        model_name,
        client=None,
        max_iterations=8,
        max_turns=40,
        execution_model=None,
        interactive_mode=False,
        intervene_every=1,
        **kwargs,
    ):
        log_file = getattr(
            logger,
            "trace_file",
            getattr(logger, "log_file", str(Path(output_dir) / "opencode_execution.log")),
        )
        self.logger = FileLogger(log_file)
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.h5ad_path = str(Path(h5ad_path).resolve())
        self.adata_summary = adata_summary or ""
        self.paper_summary = paper_summary or ""
        self.coding_guidelines = coding_guidelines or ""
        self.analysis_name = analysis_name
        self.max_iterations = max_iterations
        self.max_turns = max_turns
        self.execution_model = execution_model or model_name
        self.interactive_mode = interactive_mode
        self.intervene_every = intervene_every
        self.client = client or create_openai_client()
        if not self.client:
            raise ValueError(
                "OpenAI-compatible configuration is required for execution_mode=opencode"
            )

    def _write_initial_notebook(self, analysis: dict[str, Any], analysis_idx: int) -> Path:
        nb = new_notebook()

        hypothesis = analysis.get("hypothesis", "No hypothesis provided")
        plan = analysis.get("analysis_plan", [])
        plan_text = "\n".join(f"- {step}" for step in plan) if plan else "- No plan provided"

        nb.cells.append(
            new_markdown_cell(
                f"# Analysis\n\n**Hypothesis**: {hypothesis}\n\n## Initial Plan\n{plan_text}"
            )
        )

        setup_code = f"""import scanpy as sc
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

print("Loading data...")
adata = sc.read_h5ad(r'''{self.h5ad_path}''')
print(f"Loaded: {{adata.n_obs}} cells x {{adata.n_vars}} genes")
"""
        nb.cells.append(new_code_cell(setup_code))

        notebook_path = self.output_dir / f"{self.analysis_name}_analysis_{analysis_idx + 1}.ipynb"
        with open(notebook_path, "w", encoding="utf-8") as f:
            nbf.write(nb, f)

        return notebook_path

    def _build_system_prompt(self, analysis: dict[str, Any], notebook_path: Path) -> str:
        hypothesis = analysis.get("hypothesis", "No hypothesis provided")
        plan = analysis.get("analysis_plan", [])
        first_step_code = strip_code_fences(analysis.get("first_step_code", ""))
        plan_text = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan)) if plan else "(none)"

        return f"""
You are a single-cell transcriptomics notebook execution agent.

You are working on notebook: {notebook_path}

Goal:
- Execute a useful analysis for the hypothesis below.
- Add notebook cells incrementally.
- Read notebook outputs before deciding what to do next.
- Stop after at most {self.max_iterations} successful analysis steps, or earlier if the analysis is complete.

Hypothesis:
{hypothesis}

Planned steps:
{plan_text}

Suggested first step code:
```python
{first_step_code}
```

Context:
- adata summary: {self.adata_summary[:3000]}
- user context: {self.paper_summary[:3000]}
- coding guidelines: {self.coding_guidelines[:3000]}

Available actions:
- read_notebook: {{"action":"read_notebook","args":{{}}}}
- read_cell: {{"action":"read_cell","args":{{"index": 0}}}}
- insert_cell: {{"action":"insert_cell","args":{{"index": null, "cell_type": "markdown"|"code", "source": "..."}}}}
- overwrite_cell_source: {{"action":"overwrite_cell_source","args":{{"index": 3, "source": "..."}}}}
- execute_cell: {{"action":"execute_cell","args":{{"index": 3}}}}
- insert_execute_code_cell: {{"action":"insert_execute_code_cell","args":{{"index": null, "source": "..."}}}}
- restart_kernel: {{"action":"restart_kernel","args":{{}}}}
- finish: {{"action":"finish","summary": "Short final notebook summary"}}

Rules:
- Return exactly one JSON object and nothing else.
- Prefer appending cells with index=null.
- Do not delete cells.
- Use markdown summary / interpretation cells around each major code step.
- Use the suggested first step code early unless notebook state clearly suggests a better first move.
- After executing code, inspect outputs with read_cell or read_notebook before the next change.
- If a code cell fails, fix that cell with overwrite_cell_source and rerun it.
- Finish once the notebook contains a coherent short analysis, not just a single raw code cell.
""".strip()

    def _complete_action(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        kwargs = {
            "model": self.execution_model,
            "messages": messages,
        }
        try:
            response = self.client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            response = self.client.chat.completions.create(**kwargs)

        text = response.choices[0].message.content or ""
        try:
            payload = json.loads(_extract_json_text(text))
        except Exception as e:
            repair_messages = [
                {
                    "role": "system",
                    "content": (
                        "Rewrite the user-provided content into one valid JSON object only. "
                        f"Allowed actions: {sorted(_ACTION_NAMES)}."
                    ),
                },
                {"role": "user", "content": text},
            ]
            repair = self.client.chat.completions.create(model=self.execution_model, messages=repair_messages)
            payload = json.loads(_extract_json_text(repair.choices[0].message.content or ""))

        action = payload.get("action")
        if action not in _ACTION_NAMES:
            raise ValueError(
                f"Unsupported action: {action!r}\n"
                f"Model response: {text[:500]}\n"
                f"Parsed payload: {payload}\n"
                f"Expected one of: {sorted(_ACTION_NAMES)}"
            )
        payload.setdefault("args", {})
        return payload

    def _execute_action(self, session: NotebookSession, action: dict[str, Any]) -> dict[str, Any]:
        name = action["action"]
        args = action.get("args", {}) or {}

        if name == "read_notebook":
            return session.read_notebook()
        if name == "read_cell":
            return session.read_cell(int(args["index"]))
        if name == "insert_cell":
            return session.insert_cell(args.get("index"), args["cell_type"], args["source"])
        if name == "overwrite_cell_source":
            return session.overwrite_cell_source(int(args["index"]), args["source"])
        if name == "execute_cell":
            return session.execute_cell(int(args["index"]))
        if name == "insert_execute_code_cell":
            return session.insert_execute_code_cell(args.get("index"), args["source"])
        if name == "restart_kernel":
            session.restart_kernel()
            return {"ok": True, "message": "Kernel restarted"}
        if name == "finish":
            summary = action.get("summary", "Analysis complete.")
            session.insert_cell(None, "markdown", f"## Final summary\n\n{summary}")
            return {"ok": True, "finished": True, "summary": summary}
        raise ValueError(f"Unknown action: {name}")

    def _seed_notebook(self, session: NotebookSession, analysis: dict[str, Any]) -> None:
        session.execute_cell(1)
        first_step_code = strip_code_fences(analysis.get("first_step_code", ""))
        plan = analysis.get("analysis_plan", [])
        first_step_title = plan[0] if plan else "Initial analysis step"
        if first_step_code:
            session.insert_cell(
                None,
                "markdown",
                f"## Step 1 summary - {first_step_title}\n\nInitial seeded step from the hypothesis generator.",
            )
            session.insert_execute_code_cell(None, first_step_code)

    def execute_idea(
        self,
        analysis: dict[str, Any],
        past_analyses: str = "",
        analysis_idx: int = 0,
        seeded: bool = False,
    ) -> str:
        notebook_path = self._write_initial_notebook(analysis, analysis_idx)
        prompt = self._build_system_prompt(analysis, notebook_path)

        self.logger.log("analysis_start", f"analysis_idx={analysis_idx} notebook={notebook_path}")
        self.logger.log("prompt", prompt)

        session = NotebookSession(str(notebook_path))
        messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]

        try:
            self._seed_notebook(session, analysis)
            initial_state = session.read_notebook()
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Current notebook state after setup and the seeded first step:\n"
                        f"{_trim_json(initial_state)}\n\n"
                        "Choose the next action as JSON only."
                    ),
                }
            )

            for turn in range(self.max_turns):
                action = self._complete_action(messages)
                self.logger.log_json("action", {"turn": turn + 1, **action})

                try:
                    result = self._execute_action(session, action)
                except Exception as exc:
                    result = {"ok": False, "error": str(exc), "action": action["action"]}

                self.logger.log_json("action_result", {"turn": turn + 1, **result})

                messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool result for `{action['action']}`:\n{_trim_json(result)}\n\n"
                            "Choose the next action as JSON only."
                        ),
                    }
                )

                if result.get("finished"):
                    break

            hypothesis = analysis.get("hypothesis", "")
            plan = analysis.get("analysis_plan", [])
            plan_str = "\n".join(f"  - {step}" for step in plan) if plan else ""
            summary = f"Analysis {analysis_idx + 1}:\n  Hypothesis: {hypothesis}\n  Plan:\n{plan_str}\n"
            return f"{past_analyses}{summary}\n"
        finally:
            session.shutdown()
