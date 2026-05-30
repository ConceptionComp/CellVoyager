"""
Hypothesis generation module.
Extracted from agent.py - Phase 1: Idea Generation.
"""
import json
import os
import re

import anthropic
from pydantic import BaseModel

from cellvoyager.llm_utils import create_gemini_client, create_openai_client, get_model_provider
from cellvoyager.utils import get_documentation


_MODEL_ALIASES = {
    "gpt-5.3": "gpt-5.3-chat-latest",
    "gpt-5.2": "gpt-5.2-chat-latest",
}


def _resolve_provider_and_model(model: str) -> tuple[str, str]:
    """Resolve the target provider and bare model name."""
    normalized_model = _MODEL_ALIASES.get(model, model)
    provider = get_model_provider(normalized_model)

    if normalized_model.startswith("anthropic/"):
        return "anthropic", normalized_model.split("/", 1)[1]
    if normalized_model.startswith("openai/"):
        return "openai", normalized_model.split("/", 1)[1]

    if provider in {"anthropic", "openai", "gemini"}:
        return provider, normalized_model
    return "openai", normalized_model


def _to_anthropic_kwargs(model_name: str, messages: list[dict], max_tokens: int = 4096) -> dict:
    system_parts = []
    anthropic_messages = []

    for message in messages:
        role = message["role"]
        content = message["content"]
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            anthropic_messages.append({"role": role, "content": content})

    kwargs = {
        "model": model_name,
        "messages": anthropic_messages,
        "max_tokens": max_tokens,
    }
    if system_parts:
        kwargs["system"] = "\n\n".join(system_parts)
    return kwargs


def _anthropic_text(response) -> str:
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts).strip()


class AnalysisPlan(BaseModel):
    hypothesis: str
    analysis_plan: list[str]
    first_step_code: str
    code_description: str = ""
    summary: str = ""


_ANALYSIS_PLAN_JSON_SCHEMA = json.dumps(AnalysisPlan.model_json_schema(), ensure_ascii=False, indent=2)
_ANALYSIS_PLAN_JSON_EXAMPLE = json.dumps(
    {
        "hypothesis": "Hypothesis text",
        "analysis_plan": ["Step 1", "Step 2"],
        "first_step_code": "print('hello')",
        "code_description": "Short code description",
        "summary": "Short summary",
    },
    ensure_ascii=False,
    indent=2,
)
_ANALYSIS_PLAN_JSON_INSTRUCTION = (
    "Return exactly one valid JSON object. "
    "Your final answer must start with '{' and end with '}'. "
    "Do not include markdown fences, prose, bullet points, explanations, or visible reasoning. "
    "Do not include keys other than hypothesis, analysis_plan, first_step_code, code_description, and summary. "
    "If you need to think, do it silently and output only the final JSON object. "
    "The JSON must conform to this schema:\n"
    f"{_ANALYSIS_PLAN_JSON_SCHEMA}\n\n"
    "Example output shape:\n"
    f"{_ANALYSIS_PLAN_JSON_EXAMPLE}"
)


def _with_json_instruction(messages: list[dict]) -> list[dict]:
    structured_messages = [dict(message) for message in messages]
    for message in structured_messages:
        if message.get("role") == "system":
            message["content"] = f"{message['content']}\n\n{_ANALYSIS_PLAN_JSON_INSTRUCTION}"
            return structured_messages
    return [{"role": "system", "content": _ANALYSIS_PLAN_JSON_INSTRUCTION}, *structured_messages]


def _extract_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    raise ValueError("No JSON object found in model response")


def _validate_analysis_plan(payload: dict | str) -> dict:
    if isinstance(payload, str):
        plan = AnalysisPlan.model_validate_json(payload)
    else:
        plan = AnalysisPlan.model_validate(payload)
    return plan.model_dump()


class HypothesisGenerator:
    """
    Generates and refines analysis hypotheses/ideas.
    Called during the idea generation phase before execution.
    """

    def __init__(
        self,
        model_name,
        prompt_dir,
        coding_guidelines,
        coding_system_prompt,
        adata_summary,
        paper_summary,
        logger,
        use_self_critique=True,
        use_documentation=True,
        max_iterations=6,
        deepresearch_background="",
        log_prompts=False,
        log_responses=False,
        interactive=False,
        output_dir=None,
        client=None,  # kept for backward compat, unused
    ):
        self.provider, self.model_name = _resolve_provider_and_model(model_name)
        self.prompt_dir = prompt_dir
        self.coding_guidelines = coding_guidelines
        self.coding_system_prompt = coding_system_prompt
        self.adata_summary = adata_summary
        self.paper_summary = paper_summary
        self.logger = logger
        self.use_self_critique = use_self_critique
        self.use_documentation = use_documentation
        self.max_iterations = max_iterations
        self.deepresearch_background = deepresearch_background
        self.log_prompts = log_prompts or interactive
        self.log_responses = log_responses or interactive
        self.interactive = interactive
        self.output_dir = output_dir

        if self.provider == "anthropic":
            anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
            if not anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY is required for Anthropic hypothesis models")
            self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        elif self.provider == "gemini":
            self.client = create_gemini_client()
            if not self.client:
                raise ValueError(
                    "GEMINI_API_KEY is required for Gemini hypothesis models"
                )
        else:
            self.client = create_openai_client()
            if not self.client:
                raise ValueError(
                    "OpenAI-compatible hypothesis models require OPENAI_API_KEY, "
                    "or OPENAI_BASE_URL / OPENAI_API_BASE for a local server."
                )

    def _complete_structured(self, messages: list, _analysis_idx=None, _step=None, _call_type=None) -> dict:
        """Call the configured SDK client and parse a validated AnalysisPlan JSON object."""
        structured_messages = _with_json_instruction(list(messages))
        if self.provider == "anthropic":
            response_text = self._complete(structured_messages)
        else:
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=structured_messages,
                    response_format={"type": "json_object"},
                )
                response_text = response.choices[0].message.content or ""
            except Exception:
                response_text = self._complete(structured_messages)
        try:
            return _validate_analysis_plan(_extract_json_text(response_text))
        except Exception:
            repair_prompt = (
                "Rewrite the following response as exactly one valid JSON object. "
                "Your answer must start with '{' and end with '}'. "
                "Do not include markdown fences, explanations, or visible reasoning. "
                "Preserve the original meaning.\n\n"
                f"Schema:\n{_ANALYSIS_PLAN_JSON_SCHEMA}\n\n"
                "Example output shape:\n"
                f"{_ANALYSIS_PLAN_JSON_EXAMPLE}\n\n"
                f"Response to repair:\n{response_text}"
            )
            if self.log_prompts and _analysis_idx is not None:
                repair_call_type = f"{_call_type}_repair" if _call_type else "repair"
                path = self._save_prompt(repair_prompt, _analysis_idx, _step or 0, repair_call_type)
                if self.interactive:
                    repair_prompt = self._interactive_pause(path)
            repaired_text = self._complete([
                {"role": "system", "content": "You repair model outputs into valid JSON."},
                {"role": "user", "content": repair_prompt},
            ])
            if self.log_responses and _analysis_idx is not None:
                repair_call_type = f"{_call_type}_repair" if _call_type else "repair"
                resp_path = self._save_response(repaired_text, _analysis_idx, _step or 0, repair_call_type)
                if self.interactive:
                    print(f"[interactive] Response saved: {resp_path}")
            return _validate_analysis_plan(_extract_json_text(repaired_text))

    def _complete(self, messages: list) -> str:
        """Call the configured SDK client for plain-text responses."""
        if self.provider == "anthropic":
            response = self.client.messages.create(**_to_anthropic_kwargs(self.model_name, list(messages)))
            return _anthropic_text(response)

        response = self.client.chat.completions.create(model=self.model_name, messages=list(messages))
        return response.choices[0].message.content

    def _analysis_trace_payload(self, analysis: dict, **extra) -> dict:
        plan = analysis.get("analysis_plan", []) or []
        first_step_code = analysis.get("first_step_code", "") or ""
        payload = {
            "hypothesis": analysis.get("hypothesis", ""),
            "analysis_plan": plan,
            "num_plan_steps": len(plan),
            "first_step_code_preview": first_step_code[:1200],
            "first_step_code_length": len(first_step_code),
        }
        payload.update(extra)
        return payload

    def _prompt_dir(self):
        assert self.output_dir, "output_dir must be set to save prompt files"
        d = os.path.join(self.output_dir, "prompts")
        os.makedirs(d, exist_ok=True)
        return d

    def _response_dir(self):
        assert self.output_dir, "output_dir must be set to save response files"
        d = os.path.join(self.output_dir, "responses")
        os.makedirs(d, exist_ok=True)
        return d

    def _save_prompt(self, prompt, analysis_idx, step, call_type):
        path = os.path.join(self._prompt_dir(), f"analysis_{analysis_idx}_step{step}_{call_type}.txt")
        with open(path, "w") as f:
            f.write(prompt)
        return path

    def _save_response(self, response, analysis_idx, step, call_type):
        path = os.path.join(self._response_dir(), f"analysis_{analysis_idx}_step{step}_{call_type}.txt")
        with open(path, "w") as f:
            f.write(response or "")
        return path

    def _interactive_pause(self, prompt_path):
        print(f"[interactive] Prompt: {prompt_path}")
        try:
            input("Press Enter to send (or Ctrl+C to abort)...")
        except KeyboardInterrupt:
            print("\n[interactive] Aborted.")
            raise
        print("[interactive] Sending prompt, waiting for response...")
        with open(prompt_path) as f:
            return f.read()

    def generate_jupyter_summary(self, notebook_cells):
        """Generate a comprehensive summary of notebook cells including source code and outputs (including errors)"""
        if notebook_cells is None:
            return ""

        jupyter_summary = ""
        for cell in notebook_cells:
            if cell["cell_type"] == "code" or cell["cell_type"] == "markdown" or cell["cell_type"] == "error":
                jupyter_summary += f"{cell['source']}\n"

        return jupyter_summary

    def generate_initial_analysis(self, attempted_analyses, analysis_idx=1):
        print("📝 Requesting initial analysis plan from model...")
        prompt = open(os.path.join(self.prompt_dir, "first_draft.txt")).read()
        prompt = prompt.format(
            CODING_GUIDELINES=self.coding_guidelines,
            adata_summary=self.adata_summary,
            past_analyses=attempted_analyses,
            paper_txt=self.paper_summary,
            deepresearch_background=self.deepresearch_background,
            max_iterations=self.max_iterations,
        )

        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, 0, "first_draft")
            if self.interactive:
                prompt = self._interactive_pause(path)

        self.logger.log_trace(
            "planner_initial_analysis_start",
            f"provider={self.provider} model={self.model_name} attempted_analyses_chars={len(attempted_analyses or '')}",
        )

        analysis = self._complete_structured([
            {"role": "system", "content": self.coding_system_prompt},
            {"role": "user", "content": prompt},
        ], _analysis_idx=analysis_idx, _step=0, _call_type="first_draft")

        if self.log_responses:
            resp_path = self._save_response(json.dumps(analysis, indent=2), analysis_idx, 0, "first_draft")
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")

        self.logger.log_trace_json("planner_initial_analysis_result", self._analysis_trace_payload(analysis))
        return analysis

    def critique_step(self, analysis, past_analyses, notebook_cells, num_steps_left, analysis_idx=1, step=0):
        print("🔍 Reviewing generated plan...")
        hypothesis = analysis["hypothesis"]
        analysis_plan = analysis["analysis_plan"]
        first_step_code = analysis["first_step_code"]

        # Generate comprehensive jupyter summary including outputs and errors
        jupyter_summary = self.generate_jupyter_summary(notebook_cells)

        if self.use_documentation:
            prompt = open(os.path.join(self.prompt_dir, "critic.txt")).read()
            # Get relevant documentation on the single-cell packages being used in the first step code
            try:
                documentation = get_documentation(first_step_code)
            except Exception as e:
                print(f"⚠️ Documentation extraction failed: {e}")
                documentation = ""
            prompt = prompt.format(
                hypothesis=hypothesis,
                analysis_plan=analysis_plan,
                first_step_code=first_step_code,
                CODING_GUIDELINES=self.coding_guidelines,
                adata_summary=self.adata_summary,
                past_analyses=past_analyses,
                paper_txt=self.paper_summary,
                jupyter_notebook=jupyter_summary,
                documentation=documentation,
                num_steps_left=num_steps_left,
            )
        else:
            prompt = open(os.path.join(self.prompt_dir, "ablations", "critic_NO_DOCUMENTATION.txt")).read()
            prompt = prompt.format(
                hypothesis=hypothesis,
                analysis_plan=analysis_plan,
                first_step_code=first_step_code,
                CODING_GUIDELINES=self.coding_guidelines,
                adata_summary=self.adata_summary,
                past_analyses=past_analyses,
                paper_txt=self.paper_summary,
                jupyter_notebook=jupyter_summary,
                num_steps_left=num_steps_left,
            )

        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, step, "critic")
            if self.interactive:
                prompt = self._interactive_pause(path)

        feedback = self._complete([
            {
                "role": "system",
                "content": "You are a single-cell bioinformatics expert providing feedback on code and analysis plan.",
            },
            {"role": "user", "content": prompt},
        ])

        if self.log_responses:
            resp_path = self._save_response(feedback, analysis_idx, step, "critic")
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")

        self.logger.log_trace(
            "planner_critique_feedback",
            f"num_steps_left={num_steps_left} chars={len(feedback or '')} preview={(feedback or '')[:1200]}",
        )
        return feedback

    def incorporate_critique(self, analysis, feedback, notebook_cells, num_steps_left, analysis_idx=1, step=0):
        print("🛠 Revising plan from critique...")
        hypothesis = analysis["hypothesis"]
        analysis_plan = analysis["analysis_plan"]
        first_step_code = analysis["first_step_code"]

        # Generate comprehensive jupyter summary including outputs and errors
        jupyter_summary = self.generate_jupyter_summary(notebook_cells)

        prompt = open(os.path.join(self.prompt_dir, "incorporate_critque.txt")).read()
        prompt = prompt.format(
            hypothesis=hypothesis,
            analysis_plan=analysis_plan,
            first_step_code=first_step_code,
            CODING_GUIDELINES=self.coding_guidelines,
            adata_summary=self.adata_summary,
            feedback=feedback,
            jupyter_notebook=jupyter_summary,
            num_steps_left=num_steps_left,
        )

        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, step, "incorporate_critique")
            if self.interactive:
                prompt = self._interactive_pause(path)

        self.logger.log_trace(
            "planner_incorporate_critique_start",
            f"num_steps_left={num_steps_left} feedback_chars={len(feedback or '')}",
        )

        revised_analysis = self._complete_structured([
            {"role": "system", "content": self.coding_system_prompt},
            {"role": "user", "content": prompt},
        ], _analysis_idx=analysis_idx, _step=step, _call_type="incorporate_critique")

        if self.log_responses:
            resp_path = self._save_response(json.dumps(revised_analysis, indent=2), analysis_idx, step, "incorporate_critique")
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")

        self.logger.log_trace_json(
            "planner_incorporate_critique_result",
            self._analysis_trace_payload(revised_analysis),
        )
        return revised_analysis

    def get_feedback(self, analysis, past_analyses, notebook_cells, num_steps_left, iterations=1, analysis_idx=1, step=0):
        current_analysis = analysis
        for i in range(iterations):
            if iterations > 1:
                print(f"🔄 Self-critique pass {i + 1}/{iterations}...")
            self.logger.log_trace(
                "planner_feedback_iteration_start",
                f"iteration={i + 1} num_steps_left={num_steps_left}",
            )
            # Use step * 100 + (i+1) so different execution steps and rounds each get a unique file.
            file_step = step * 100 + (i + 1)
            feedback = self.critique_step(current_analysis, past_analyses, notebook_cells, num_steps_left, analysis_idx=analysis_idx, step=file_step)
            current_analysis = self.incorporate_critique(
                current_analysis, feedback, notebook_cells, num_steps_left, analysis_idx=analysis_idx, step=file_step
            )
            self.logger.log_trace_json(
                "planner_feedback_iteration_result",
                self._analysis_trace_payload(current_analysis, iteration=i + 1),
            )

        return current_analysis

    def generate_idea(self, past_analyses, analysis_idx=None, seeded_hypothesis=None):
        """
        Phase 1: Idea Generation

        Args:
            past_analyses: String of past analysis summaries
            analysis_idx: Analysis index for logging (optional)
            seeded_hypothesis: Simple hypothesis string to guide AI generation (optional)

        Returns:
            dict: Analysis containing hypothesis, analysis_plan, first_step_code, etc.
        """
        if seeded_hypothesis is not None:
            print(f"🌱 Using seeded hypothesis: {seeded_hypothesis}")
            print("📝 Requesting seeded analysis plan from model...")
            return self.generate_analysis_from_hypothesis(seeded_hypothesis, past_analyses, analysis_idx)

        print("🧠 Generating new analysis idea...")

        idx = (analysis_idx + 1) if analysis_idx is not None else 1
        # Create the initial analysis plan
        analysis = self.generate_initial_analysis(past_analyses, analysis_idx=idx)
        self.logger.log_trace_json(
            "planner_generate_idea_initial",
            self._analysis_trace_payload(analysis, analysis_idx=analysis_idx, seeded=False),
        )

        if analysis_idx is not None:
            step_name = f"{analysis_idx+1}_1"
            hypothesis = analysis["hypothesis"]
            analysis_plan = analysis["analysis_plan"]
            initial_code = analysis["first_step_code"]

            # Log only the output of the analysis
            self.logger.log_response(
                f"Hypothesis: {hypothesis}\n\nAnalysis Plan:\n"
                + "\n".join([f"{i+1}. {step}" for i, step in enumerate(analysis_plan)])
                + f"\n\nInitial Code:\n{initial_code}",
                f"initial_analysis_{step_name}",
            )

        # Get feedback for the initial analysis plan and modify it accordingly
        if self.use_self_critique:
            modified_analysis = self.get_feedback(analysis, past_analyses, None, self.max_iterations, analysis_idx=idx)
            self.logger.log_trace_json(
                "planner_generate_idea_final",
                self._analysis_trace_payload(modified_analysis, analysis_idx=analysis_idx, seeded=False),
            )

            if analysis_idx is not None:
                self.logger.log_response(
                    f"APPLIED INITIAL SELF-CRITIQUE - Analysis {analysis_idx+1}",
                    f"self_critique_{step_name}",
                )

                hypothesis = modified_analysis["hypothesis"]
                analysis_plan = modified_analysis["analysis_plan"]
                current_code = modified_analysis["first_step_code"]

                # Log revised analysis plan
                self.logger.log_response(
                    f"Revised Hypothesis: {hypothesis}\n\nRevised Analysis Plan:\n"
                    + "\n".join([f"{i+1}. {step}" for i, step in enumerate(analysis_plan)])
                    + f"\n\nRevised Code:\n{current_code}",
                    f"revised_analysis_{step_name}",
                )

            return modified_analysis
        else:
            self.logger.log_trace_json(
                "planner_generate_idea_final",
                self._analysis_trace_payload(analysis, analysis_idx=analysis_idx, seeded=False),
            )
            if analysis_idx is not None:
                print("🚫 Skipping feedback on next step (no self-critique)")
                self.logger.log_response(
                    f"SKIPPING INITIAL SELF-CRITIQUE - Analysis {analysis_idx+1}",
                    f"no_self_critique_{step_name}",
                )

            return analysis

    def generate_analysis_from_hypothesis(self, hypothesis, past_analyses, analysis_idx=None):
        """
        Generate an analysis plan from a simple hypothesis string using AI

        Args:
            hypothesis: Simple hypothesis string
            past_analyses: String of past analysis summaries
            analysis_idx: Analysis index for logging (optional)

        Returns:
            dict: Analysis containing hypothesis, analysis_plan, first_step_code, etc.
        """
        # Create a modified prompt that incorporates the seeded hypothesis
        prompt = open(os.path.join(self.prompt_dir, "ablations", "analysis_from_hypothesis.txt")).read()
        prompt = prompt.format(
            hypothesis=hypothesis,
            coding_guidelines=self.coding_guidelines,
            adata_summary=self.adata_summary,
            paper_summary=self.paper_summary,
        )

        idx = analysis_idx if analysis_idx is not None else 1
        if self.log_prompts:
            path = self._save_prompt(prompt, idx, 0, "analysis_from_hypothesis")
            if self.interactive:
                prompt = self._interactive_pause(path)

        self.logger.log_trace(
            "planner_seeded_hypothesis_start",
            f"analysis_idx={analysis_idx} hypothesis={(hypothesis or '')[:300]}",
        )

        print("📝 Requesting seeded analysis plan from model...")
        analysis = self._complete_structured([
            {"role": "system", "content": self.coding_system_prompt},
            {"role": "user", "content": prompt},
        ], _analysis_idx=idx, _step=0, _call_type="analysis_from_hypothesis")

        if self.log_responses:
            resp_path = self._save_response(json.dumps(analysis, indent=2), idx, 0, "analysis_from_hypothesis")
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")

        analysis = self.get_feedback(analysis, past_analyses, None, self.max_iterations, analysis_idx=idx)

        # Ensure the hypothesis matches what was provided
        analysis["hypothesis"] = hypothesis
        self.logger.log_trace_json(
            "planner_seeded_hypothesis_final",
            self._analysis_trace_payload(analysis, analysis_idx=analysis_idx, seeded=True),
        )

        # Log the seeded hypothesis analysis
        if analysis_idx is not None:
            step_name = f"{analysis_idx+1}_1"
            analysis_plan = analysis["analysis_plan"]
            initial_code = analysis["first_step_code"]

            # Log the seeded hypothesis analysis
            self.logger.log_response(
                f"Seeded Hypothesis: {hypothesis}\n\nGenerated Analysis Plan:\n"
                + "\n".join([f"{i+1}. {step}" for i, step in enumerate(analysis_plan)])
                + f"\n\nInitial Code:\n{initial_code}",
                f"seeded_hypothesis_{step_name}",
            )

        return analysis
