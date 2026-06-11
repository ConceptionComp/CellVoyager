"""
Idea execution module.
Extracted from agent.py - Phase 2: Idea Execution.
"""
import os
import re
import json
import time
import base64
import copy
import datetime
import nbformat as nbf
from nbformat.v4 import new_code_cell, new_output
from jupyter_client import KernelManager
from cellvoyager.llm_utils import create_json_chat_completion, parse_json_response_text
from cellvoyager.utils import get_documentation

AVAILABLE_PACKAGES = "monocle3, SingleCellExperiment, Matrix, ggplot2, patchwork"

# Curated house helpers (gene filtering + flexible plot_cells wrappers) sourced
# into the live R session so generated cells can call them directly. See
# cellvoyager/r_helpers/cv_helpers.R.
CV_HELPERS_PATH = os.path.join(os.path.dirname(__file__), "..", "r_helpers", "cv_helpers.R")

# Jupyter kernel that bridges to R via rpy2. The `cellvoyager-r` kernelspec is a Python
# ipykernel living in the CellVoyager-r conda env with R_HOME set so rpy2 finds the conda R
# (system arm64 R crashes on an arch mismatch). Override via env if registered elsewhere.
CELLVOYAGER_KERNEL_NAME = os.environ.get("CELLVOYAGER_KERNEL_NAME", "cellvoyager-r")

# Overall wall-clock budget (seconds) for a single generated cell to run to completion.
# monocle3 routines like fit_models fit a per-gene GLM and can run for minutes; the cap
# is generous but finite so a runaway/silent cell is interrupted and reported as a failure
# (the fix loop can then react) rather than silently treated as an empty success.
CELL_EXEC_TIMEOUT = int(os.environ.get("CELLVOYAGER_CELL_TIMEOUT", "900"))


def strip_code_markers(text):
    """Remove ```r, ```python and ``` fences from code blocks."""
    return re.sub(r"```r|```R|```python|```", "", text)


def ensure_r_cell_magic(code):
    """Guarantee a generated analysis cell runs as R via the rpy2 `%%R` magic.

    The backend is monocle3 (R), so every generated analysis cell is R that must
    run through the `%%R` cell magic. Models frequently emit a bare R block with
    no `%%R` line — the IPython kernel would then parse R as Python and fail on
    `$`, `<-`, etc. Prepend `%%R` unless the cell is already an R magic cell or
    the Python rpy2 setup cell (`%load_ext` / `import rpy2`). Idempotent, so it is
    safe to apply on every insertion and on each fix-loop retry.
    """
    stripped = code.lstrip()
    if not stripped:
        return code
    first_line = stripped.splitlines()[0]
    # Already an R magic cell (e.g. "%%R" or "%%R -w 800 -h 600").
    if first_line.startswith("%%R"):
        return code
    # The rpy2 setup cell is intentionally Python (loads the magic / uses ro.r()).
    if first_line.startswith("%load_ext") or "rpy2" in first_line:
        return code
    return "%%R\n" + code


class IdeaExecutor:
    """
    Executes analysis ideas (hypotheses) as Jupyter notebooks.
    Called after the hypothesis generation phase.
    """

    def __init__(
        self,
        hypothesis_generator,
        client,
        model_name,
        prompt_dir,
        coding_guidelines,
        coding_system_prompt,
        adata_summary,
        paper_summary,
        logger,
        rds_path,
        output_dir,
        analysis_name,
        max_iterations=6,
        max_fix_attempts=3,
        use_self_critique=True,
        use_VLM=True,
        use_documentation=True,
        log_prompts=False,
        log_responses=False,
        interactive=False,
    ):
        self.hypothesis_generator = hypothesis_generator
        self.client = client
        self.model_name = model_name
        self.prompt_dir = prompt_dir
        self.coding_guidelines = coding_guidelines
        self.coding_system_prompt = coding_system_prompt
        self.adata_summary = adata_summary
        self.paper_summary = paper_summary
        self.logger = logger
        self.rds_path = rds_path
        self.output_dir = output_dir
        self.analysis_name = analysis_name
        self.max_iterations = max_iterations
        self.max_fix_attempts = max_fix_attempts
        self.use_self_critique = use_self_critique
        self.use_VLM = use_VLM
        self.use_documentation = use_documentation
        self.log_prompts = log_prompts or interactive
        self.log_responses = log_responses or interactive
        self.interactive = interactive

        # Code memory for context
        self.code_memory = []
        self.code_memory_size = 5
        self.kernel_manager = None
        self.kernel_client = None

    def update_code_memory(self, notebook_cells):
        """Update the code memory with the latest code cells from the notebook"""
        code_cells = []
        for cell in notebook_cells:
            if cell.get("cell_type") == "code":
                code_cells.append(cell["source"] if isinstance(cell, dict) else cell.source)

        self.code_memory = code_cells[-self.code_memory_size :] if len(code_cells) > 0 else []

    def generate_jupyter_summary(self, notebook_cells):
        """Generate a comprehensive summary of notebook cells including source code and outputs (including errors)"""
        if notebook_cells is None:
            return ""

        jupyter_summary = ""
        for cell in notebook_cells:
            cell_type = cell.get("cell_type") if isinstance(cell, dict) else getattr(cell, "cell_type", None)
            source = cell.get("source", "") if isinstance(cell, dict) else getattr(cell, "source", "")
            if cell_type in ("code", "markdown", "error"):
                jupyter_summary += f"{source}\n"

        return jupyter_summary

    def generate_next_step_analysis(self, analysis, attempted_analyses, notebook_cells, num_steps_left, seeded, analysis_idx=0, step=0):
        hypothesis = analysis["hypothesis"]
        analysis_plan = analysis["analysis_plan"]
        first_step_code = analysis["first_step_code"]

        # Update code memory with latest notebook cells
        self.update_code_memory(notebook_cells)

        # Generate comprehensive jupyter summary including outputs and errors
        jupyter_summary = self.generate_jupyter_summary(notebook_cells)

        if seeded:
            prompt = open(os.path.join(self.prompt_dir, "next_step_seeded.txt")).read()
            prompt = prompt.format(
                hypothesis=hypothesis,
                analysis_plan=analysis_plan,
                num_steps_left=num_steps_left,
                CODING_GUIDELINES=self.coding_guidelines,
                jupyter_notebook=jupyter_summary,
                adata_summary=self.adata_summary,
                past_analyses=attempted_analyses,
                paper_txt=self.paper_summary,
            )
        else:
            prompt = open(os.path.join(self.prompt_dir, "next_step.txt")).read()
            prompt = prompt.format(
                hypothesis=hypothesis,
                analysis_plan=analysis_plan,
                CODING_GUIDELINES=self.coding_guidelines,
                jupyter_notebook=jupyter_summary,
                adata_summary=self.adata_summary,
                past_analyses=attempted_analyses,
                paper_txt=self.paper_summary,
                num_steps_left=num_steps_left,
            )

        call_type = "next_step_seeded" if seeded else "next_step"
        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, step, call_type)
            if self.interactive:
                prompt = self._interactive_pause(path)

        # Retry logic for generating valid analysis plan
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                response = create_json_chat_completion(
                    self.client,
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self.coding_system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                )
                result = response.choices[0].message.content

                if result is None:
                    print(f"⚠️ API returned None response in generate_next_step (attempt {attempt + 1})")
                    if attempt == max_retries:
                        raise ValueError("Model API returned None response for next step after all retries")
                    continue

                if self.log_responses:
                    resp_path = self._save_response(result, analysis_idx, step, call_type)
                    if self.interactive:
                        print(f"[interactive] Response saved: {resp_path}")

                try:
                    analysis = parse_json_response_text(result)
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON decode error in generate_next_step (attempt {attempt + 1}): {e}")
                    if attempt == max_retries:
                        raise
                    continue

                if "analysis_plan" not in analysis:
                    if attempt == max_retries:
                        raise ValueError("Generated analysis missing 'analysis_plan' key after all retries")
                    continue

                if not isinstance(analysis["analysis_plan"], list):
                    if attempt == max_retries:
                        raise ValueError("Generated analysis 'analysis_plan' is not a list after all retries")
                    continue

                if len(analysis["analysis_plan"]) == 0:
                    if attempt == max_retries:
                        raise ValueError("Generated analysis has empty 'analysis_plan' after all retries")
                    continue

                if len(analysis["analysis_plan"]) > num_steps_left:
                    if attempt == max_retries:
                        print(f"   Truncating analysis plan to {num_steps_left} steps")
                        analysis["analysis_plan"] = analysis["analysis_plan"][:num_steps_left]

                print(f"✅ Valid analysis plan generated (attempt {attempt + 1}): {len(analysis['analysis_plan'])} steps")
                break

            except Exception as e:
                if attempt == max_retries:
                    print(f"❌ All retry attempts failed for generate_next_step_analysis")
                    raise
                print(f"⚠️ Attempt {attempt + 1} failed: {e}. Retrying...")
                continue

        if seeded:
            analysis["hypothesis"] = hypothesis

        return analysis

    def fix_code(self, code, error, other_code="", documentation="", analysis_idx=0, step=0, attempt=1):
        """Attempts to fix code that produced an error"""
        max_error_chars = 2000
        max_other_code_chars = 3000
        max_past_context_chars = 4000
        max_documentation_chars = 3000

        truncated_error = error[-max_error_chars:] if len(error) > max_error_chars else error
        if len(error) > max_error_chars:
            truncated_error = "...(error truncated)...\n" + truncated_error

        truncated_other_code = other_code[-max_other_code_chars:] if len(other_code) > max_other_code_chars else other_code
        if len(other_code) > max_other_code_chars:
            truncated_other_code = "...(context truncated)...\n" + truncated_other_code

        past_code_context = ""
        if self.code_memory:
            past_cells = self.code_memory[-5:]
            past_code_context = "\n\n".join(
                [f"# Previous code cell {i+1}:\n{cell}" for i, cell in enumerate(past_cells)]
            )
            if len(past_code_context) > max_past_context_chars:
                past_code_context = past_code_context[-max_past_context_chars:]
                past_code_context = "...(context truncated)...\n" + past_code_context

        truncated_documentation = (
            documentation[-max_documentation_chars:] if len(documentation) > max_documentation_chars else documentation
        )
        if len(documentation) > max_documentation_chars:
            truncated_documentation = "...(documentation truncated)...\n" + truncated_documentation

        prompt = f"""Fix this R (monocle3) code that produced an error:

        Code:
        ```r
        {code}
        ```

        Error:
        {truncated_error}

        Provide only the fixed code with no explanation. Keep it R run through monocle3 inside a `%%R` cell, reusing the in-memory `cds` cell_data_set; do NOT rewrite it as Python/scanpy.
        You can only use the following R packages: {AVAILABLE_PACKAGES}

        Here is previous code/context (if any):
        {truncated_other_code}

        Here are the past code cells for additional context (last 5 cells):
        {past_code_context}"""

        if self.use_documentation and truncated_documentation:
            prompt += f"""

        Finally, here is documentation about some of the functions being called, ensure that the code is using the proper parameters/functions:
        {truncated_documentation}"""

        estimated_tokens = len(prompt) // 4
        if estimated_tokens > 50000:
            print(f"⚠️ Warning: Large fix_code prompt detected ({estimated_tokens} estimated tokens)")

        call_type = f"fix_code_attempt{attempt}"
        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, step, call_type)
            if self.interactive:
                prompt = self._interactive_pause(path)

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": (
                    "You are a coding assistant helping to fix single-cell transcriptomics code written in R with monocle3 (run via rpy2 `%%R` cell magic), operating on the in-memory cell_data_set `cds`. "
                    "Key monocle3 API rules: "
                    "(1) Most workflow steps have ordering dependencies — reduce_dimension(cds) requires preprocess_cds(cds); cluster_cells(cds) requires reduce_dimension(cds); learn_graph(cds) requires cluster_cells(cds); order_cells(cds) requires learn_graph(cds) and a root (root_pr_nodes/root_cells). plot_cells(cds, color_cells_by='pseudotime') needs order_cells first; color_cells_by='cluster' needs cluster_cells first. "
                    "(2) These functions RETURN an updated cds — reassign it (cds <- reduce_dimension(cds)); the object is not mutated in place. graph_test/top_markers/fit_models return data.frames, not modified cds objects. "
                    "(3) Access data with R accessors on cds: colData(cds), rowData(cds)/fData(cds), reducedDims(cds), exprs(cds)/assay(cds,'counts'); genes are addressed by rownames(cds) (symbol often in rowData(cds)$gene_short_name). Subset with R brackets cds[gene_rows, cell_cols]; remember R is 1-indexed. A ggplot only renders when the plot object is printed. "
                    "(4) Differential expression: prefer top_markers(cds, group_cells_by=<colData column>, cores=1) for marker/DE work — it is robust and needs no model. Use fit_models() ONLY for an explicit condition contrast, and make it defensive: subset genes with filter_cds_genes(cds[, cells], num_cells_expressed_cutoff=10); ensure the grouping variable has >=2 levels present (colData(cds_sub)$grp <- droplevels(factor(...)); stopifnot(nlevels(...) >= 2)); after coefficient_table(), inspect unique(ct$term) and colnames(ct) and NEVER guess them; filter to status=='OK' and the actual non-intercept term; select columns via intersect(c('gene_short_name','term','estimate','p_value','q_value'), colnames(ct)). The example CDS is already preprocessed/aligned/reduced/annotated — do NOT re-run preprocess_cds/align_cds/reduce_dimension/cluster_cells. "
                    "(5) Keep the fix as R/monocle3 — do NOT convert it to Python/scanpy. Provide only fixed code with no explanation."
                )},
                {"role": "user", "content": prompt},
            ],
        )
        fixed_code = response.choices[0].message.content

        if self.log_responses:
            resp_path = self._save_response(fixed_code, analysis_idx, step, call_type)
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")

        return fixed_code

    def generate_code_description(self, code, context="", analysis_idx=0, step=0):
        """Generate a description for a code cell based on its content"""
        prompt = f"""Generate 1-2 sentences describing the goal of the code, what it is doing, and why.

        Code:
        ```r
        {code}
        ```
        """

        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, step, "code_description")
            if self.interactive:
                prompt = self._interactive_pause(path)

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": "You are a single-cell bioinformatics expert providing concise code descriptions.",
                },
                {"role": "user", "content": prompt},
            ],
        )

        result = response.choices[0].message.content.strip()
        if self.log_responses:
            resp_path = self._save_response(result, analysis_idx, step, "code_description")
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")
        return result

    def interpret_results(self, notebook, past_analyses, hypothesis, analysis_plan, code, analysis_idx=0, step=0):
        last_cell = notebook.cells[-1]
        no_interpretation = "No results found"

        if last_cell.get("cell_type") != "code":
            print("Last cell is not a code cell")
            return no_interpretation

        text_output = ""
        if "outputs" in last_cell:
            for output in last_cell["outputs"]:
                if output.get("output_type") == "stream":
                    text_output += output.get("text", "")
                elif output.get("output_type") == "execute_result":
                    text_output += str(output.get("data", {}).get("text/plain", ""))

        if self.use_VLM:
            image_outputs = []
            if "outputs" in last_cell:
                for i, output in enumerate(last_cell["outputs"]):
                    if output.get("output_type") == "display_data":
                        image_data = output.get("data", {}).get("image/png")
                        if image_data:
                            image_outputs.append({"data": image_data, "format": "image/png"})

            if not text_output and not image_outputs:
                return no_interpretation
        else:
            if not text_output:
                return no_interpretation

        prompt = open(os.path.join(self.prompt_dir, "interp_results.txt")).read()
        prompt = prompt.format(
            text_output=text_output,
            paper_txt=self.paper_summary,
            CODING_GUIDELINES=self.coding_guidelines,
            past_analyses=past_analyses,
            hypothesis=hypothesis,
            analysis_plan=analysis_plan,
            code=code,
        )

        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, step, "interpret_results")
            if self.interactive:
                prompt = self._interactive_pause(path)

        if self.use_VLM:
            user_content = [{"type": "text", "text": prompt}]
            try:
                for img in image_outputs:
                    try:
                        image_data = img["data"]
                        if isinstance(image_data, str) and "," in image_data:
                            image_data = image_data.split(",")[1]
                        user_content.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_data}"},
                            }
                        )
                    except Exception as e:
                        print(f"Warning: Error processing image: {str(e)}")
                        continue

                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a single-cell transcriptomics expert providing feedback on R (monocle3) code and analysis plan.",
                        },
                        {"role": "user", "content": user_content},
                    ],
                )
                feedback = response.choices[0].message.content
            finally:
                image_outputs.clear()
                user_content.clear()
                import gc
                gc.collect()
        else:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a single-cell bioinformatics expert providing feedback on R (monocle3) code and analysis plan.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            feedback = response.choices[0].message.content

        if self.log_responses:
            resp_path = self._save_response(feedback, analysis_idx, step, "interpret_results")
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")

        return feedback

    def _collect_displayed_images(self, notebook, max_images=12):
        """Collect base64 PNGs that were actually displayed in the notebook.

        Used by generate_conclusion so the VLM only ever sees figures the run
        truly produced (never a computed-but-unprinted plot). Returns the most
        recent `max_images` so the payload stays bounded on long notebooks.
        """
        images = []
        for cell in notebook.cells:
            if getattr(cell, "cell_type", None) != "code":
                continue
            for output in getattr(cell, "outputs", []) or []:
                if output.get("output_type") == "display_data":
                    image_data = output.get("data", {}).get("image/png")
                    if image_data:
                        images.append({"data": image_data, "format": "image/png"})
        return images[-max_images:]

    def generate_conclusion(self, notebook, hypothesis, analysis_plan, analysis_idx=0):
        """Backward-looking synthesis of the whole analysis (the run's conclusion).

        Distinct from interpret_results (which is forward-looking per-step
        feedback): this restates the hypothesis, gives an explicit verdict, cites
        results actually present, and lists limitations/failed steps. Modeled on
        interpret_results for client/logging/save plumbing; passes displayed
        figures when use_VLM so it references only figures that truly rendered.
        Called once after the iteration loop, so it is guaranteed to run.
        """
        jupyter_summary = self.generate_jupyter_summary(notebook.cells)

        prompt = open(os.path.join(self.prompt_dir, "conclusion.txt")).read()
        prompt = prompt.format(
            hypothesis=hypothesis,
            analysis_plan=analysis_plan,
            jupyter_notebook=jupyter_summary,
        )

        if self.log_prompts:
            path = self._save_prompt(prompt, analysis_idx, 0, "conclusion")
            if self.interactive:
                prompt = self._interactive_pause(path)

        system_content = (
            "You are a single-cell transcriptomics expert writing the final, "
            "backward-looking conclusion of a completed R (monocle3) analysis."
        )

        if self.use_VLM:
            image_outputs = self._collect_displayed_images(notebook)
            user_content = [{"type": "text", "text": prompt}]
            try:
                for img in image_outputs:
                    try:
                        image_data = img["data"]
                        if isinstance(image_data, str) and "," in image_data:
                            image_data = image_data.split(",")[1]
                        user_content.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image_data}"},
                            }
                        )
                    except Exception as e:
                        print(f"Warning: Error processing image: {str(e)}")
                        continue

                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": user_content},
                    ],
                )
                conclusion = response.choices[0].message.content
            finally:
                image_outputs.clear()
                user_content.clear()
                import gc
                gc.collect()
        else:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
            )
            conclusion = response.choices[0].message.content

        if self.log_responses:
            resp_path = self._save_response(conclusion, analysis_idx, 0, "conclusion")
            if self.interactive:
                print(f"[interactive] Response saved: {resp_path}")

        return conclusion

    def start_persistent_kernel(self):
        """Start a persistent kernel for efficient cell execution"""
        try:
            self.kernel_manager = KernelManager(kernel_name=CELLVOYAGER_KERNEL_NAME)
            self.kernel_manager.start_kernel()
            self.kernel_client = self.kernel_manager.client()
            self.kernel_client.start_channels()
            self.kernel_client.wait_for_ready()
            print("✅ Persistent kernel started")
            return True
        except Exception as e:
            print(f"⚠️ Failed to start persistent kernel: {str(e)}")
            return False

    def stop_persistent_kernel(self):
        """Stop the persistent kernel with proper error handling"""
        try:
            if self.kernel_client:
                try:
                    self.kernel_client.stop_channels()
                except Exception as e:
                    print(f"⚠️ Warning: Error stopping kernel channels: {e}")

            if self.kernel_manager:
                try:
                    self.kernel_manager.shutdown_kernel(now=True)
                except Exception as e:
                    print(f"⚠️ Warning: Error shutting down kernel: {e}")

            print("✅ Persistent kernel stopped")
        except Exception as e:
            print(f"⚠️ Warning: Error during kernel cleanup: {e}")
        finally:
            self.kernel_client = None
            self.kernel_manager = None

    def run_last_cell(self, nb):
        """Executes the most recently added code cell and updates its outputs."""
        if not nb.cells:
            raise ValueError("No cells in notebook to run.")

        last_code_cell = None
        for cell in reversed(nb.cells):
            if cell.cell_type == "code":
                last_code_cell = cell
                break

        if not last_code_cell:
            raise ValueError("No code cells found in notebook.")

        code = last_code_cell.source
        msg_id = self.kernel_client.execute(code)
        outputs = []

        # Track real completion (our cell's "idle" status) vs. an overall-timeout break.
        # A per-message timeout must NOT be treated as completion: a slow cell (e.g. a long
        # fit_models) can run silently for a while, and breaking early would record a false
        # success with empty outputs while the kernel is still busy.
        deadline = time.monotonic() + CELL_EXEC_TIMEOUT
        completed = False

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = self.kernel_client.get_iopub_msg(timeout=min(remaining, 10))
            except Exception:
                # No message yet; keep waiting until the overall deadline.
                continue

            msg_type = msg["msg_type"]
            content = msg["content"]

            if msg["parent_header"].get("msg_id") != msg_id:
                continue

            if msg_type == "status" and content.get("execution_state") == "idle":
                completed = True
                break

            if msg_type == "stream":
                outputs.append(
                    new_output(output_type="stream", name=content["name"], text=content["text"])
                )
            elif msg_type == "execute_result":
                outputs.append(
                    new_output(
                        output_type="execute_result",
                        data=content["data"],
                        execution_count=content["execution_count"],
                    )
                )
            elif msg_type == "display_data":
                outputs.append(
                    new_output(
                        output_type="display_data",
                        data=content["data"],
                        metadata=content.get("metadata", {}),
                    )
                )
            elif msg_type == "error":
                outputs.append(
                    new_output(
                        output_type="error",
                        ename=content["ename"],
                        evalue=content["evalue"],
                        traceback=content["traceback"],
                    )
                )

        # Overall timeout: the cell never reached idle. Interrupt the still-running kernel
        # so the next cell starts clean, and surface it as a real failure (not empty success).
        if not completed:
            try:
                self.kernel_manager.interrupt_kernel()
            except Exception:
                pass
            timeout_msg = (
                f"TimeoutError: cell exceeded {CELL_EXEC_TIMEOUT}s and was interrupted "
                f"(consider a faster approach, e.g. subsetting genes/cells before fit_models)"
            )
            outputs.append(
                new_output(
                    output_type="error",
                    ename="TimeoutError",
                    evalue=timeout_msg,
                    traceback=[timeout_msg],
                )
            )
            code_cell_index = nb.cells.index(last_code_cell)
            nb.cells[code_cell_index].outputs = outputs
            return False, timeout_msg, nb

        code_cell_index = nb.cells.index(last_code_cell)
        nb.cells[code_cell_index].outputs = outputs

        for output in outputs:
            if output.output_type == "error":
                error_msg = f"{output.ename}: {output.evalue}"
                return False, error_msg, nb

        return True, None, nb

    def create_initial_notebook(self, hypothesis):
        notebook = nbf.v4.new_notebook()
        notebook.cells.append(nbf.v4.new_markdown_cell(f"# Analysis\n\n**Hypothesis**: {hypothesis}"))

        cv_helpers_path = os.path.abspath(CV_HELPERS_PATH)

        setup_code = f"""# Bridge to R via rpy2; the `%%R` cell magic shares one embedded R process,
# so `cds` defined here is visible to every later %%R cell.
%load_ext rpy2.ipython
import warnings
import rpy2.robjects as ro

warnings.filterwarnings('ignore')

# Load data (Monocle3 cell_data_set). dim(cds) is [genes, cells].
print("Loading data...")
ro.r('library(monocle3)')

# Source curated house helpers (detectQC, filter_cds_genes/cells, get_avg_expr,
# downsample_cds_by*, get_colors, feature_plot_flexible, plot_genes_flexible).
# A load failure must not abort the run.
try:
    ro.r('''tryCatch(source("{cv_helpers_path}"),
                     error = function(e) message("cv_helpers.R failed to load: ", conditionMessage(e)))''')
    print("Loaded cv_helpers.R")
except Exception as _e:
    print(f"⚠️ Could not source cv_helpers.R: {{_e}}")

ro.r('cds <- readRDS("{self.rds_path}")')
_dims = ro.r('dim(cds)')
print(f"Data loaded: {{int(_dims[1])}} cells and {{int(_dims[0])}} genes")
"""
        notebook.cells.append(nbf.v4.new_code_cell(setup_code))

        return notebook

    def cleanup_notebook_outputs(self, notebook):
        """Clean notebook outputs to ensure they are proper nbformat objects"""
        for cell in notebook.cells:
            if cell.cell_type == "code" and hasattr(cell, "outputs"):
                cleaned_outputs = []
                for output in cell.outputs:
                    if isinstance(output, dict):
                        if output.get("output_type") == "stream":
                            cleaned_outputs.append(
                                nbf.v4.new_output(
                                    "stream",
                                    name=output.get("name", "stdout"),
                                    text=output.get("text", ""),
                                )
                            )
                        elif output.get("output_type") == "execute_result":
                            cleaned_outputs.append(
                                nbf.v4.new_output(
                                    "execute_result",
                                    data=output.get("data", {}),
                                    execution_count=output.get("execution_count", None),
                                )
                            )
                        elif output.get("output_type") == "display_data":
                            cleaned_outputs.append(
                                nbf.v4.new_output("display_data", data=output.get("data", {}))
                            )
                        elif output.get("output_type") == "error":
                            cleaned_outputs.append(
                                nbf.v4.new_output(
                                    "error",
                                    ename=output.get("ename", ""),
                                    evalue=output.get("evalue", ""),
                                    traceback=output.get("traceback", []),
                                )
                            )
                    else:
                        cleaned_outputs.append(output)
                cell.outputs = cleaned_outputs

        return notebook

    def _snapshot_dir(self):
        snapshot_dir = os.path.join(self.output_dir, "snapshots")
        os.makedirs(snapshot_dir, exist_ok=True)
        return snapshot_dir

    def _prompt_dir(self):
        d = os.path.join(self.output_dir, "prompts")
        os.makedirs(d, exist_ok=True)
        return d

    def _response_dir(self):
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

    def save_notebook_snapshot(self, notebook, analysis_idx, step_idx, label):
        """Save a full notebook snapshot for in-progress inspection."""
        snapshot_dir = self._snapshot_dir()
        snapshot_path = os.path.join(
            snapshot_dir,
            f"{self.analysis_name}_analysis_{analysis_idx+1}_step_{step_idx:02d}_{label}.ipynb",
        )
        notebook_copy = copy.deepcopy(notebook)
        with open(snapshot_path, "w", encoding="utf-8") as f:
            nbf.write(self.cleanup_notebook_outputs(notebook_copy), f)
        print(f"💾 Snapshot saved: {snapshot_path}")
        return snapshot_path

    def execute_idea(self, analysis, past_analyses, analysis_idx, seeded=False):
        """
        Phase 2: Idea Execution

        Args:
            analysis: Analysis dict from generate_idea phase
            past_analyses: String of past analysis summaries
            analysis_idx: Analysis index for logging
            seeded: Boolean indicating if the analysis is seeded

        Returns:
            updated past_analyses string
        """

        def namer(analysis_idx, step_idx):
            return f"{analysis_idx+1}_{step_idx}"

        def short_step_text(text, limit=90):
            cleaned = " ".join(str(text or "").split())
            if len(cleaned) <= limit:
                return cleaned
            return cleaned[: limit - 3].rstrip() + "..."

        hypotheses_analysis = []
        self.code_memory = []

        print(f"\n🚀 Executing Analysis {analysis_idx+1}")

        if not self.start_persistent_kernel():
            print(f"⚠️ Failed to start kernel for analysis {analysis_idx+1}. Skipping...")
            return past_analyses

        hypothesis = analysis["hypothesis"]
        analysis_plan = analysis["analysis_plan"]
        current_code = analysis["first_step_code"]

        plan_markdown = "# Analysis Plan\n\n**Hypothesis**: " + hypothesis + "\n\n## Steps:\n"
        for step in analysis_plan:
            plan_markdown += f"- {step}\n"

        notebook = self.create_initial_notebook(hypothesis)
        _, _, notebook = self.run_last_cell(notebook)

        notebook.cells.append(nbf.v4.new_markdown_cell(plan_markdown))

        if analysis_plan:
            notebook.cells.append(nbf.v4.new_markdown_cell(f"## {analysis['code_description']}"))

        current_code = ensure_r_cell_magic(strip_code_markers(current_code))
        notebook.cells.append(new_code_cell(current_code))
        self.save_notebook_snapshot(notebook, analysis_idx, 0, "setup")

        for iteration in range(self.max_iterations):
            step_idx = iteration + 1
            step_name = namer(analysis_idx, step_idx)
            step_title = (
                analysis_plan[0] if analysis_plan else f"Step {step_idx}"
            )
            print(
                f"▶️ Running step {step_idx}/{self.max_iterations}: {short_step_text(step_title)}"
            )
            success, error_msg, notebook = self.run_last_cell(notebook)
            self.save_notebook_snapshot(notebook, analysis_idx, step_idx, "after_run")

            if success:
                self.logger.log_response(
                    f"STEP {step_idx} RAN SUCCESSFULLY - Analysis {analysis_idx+1}",
                    f"step_execution_success_{step_name}",
                )
                print("🧪 Interpreting step results...")
                results_interpretation = self.interpret_results(
                    notebook, past_analyses, hypothesis, analysis_plan, current_code,
                    analysis_idx=analysis_idx + 1, step=step_idx,
                )
                self.logger.log_response(results_interpretation, f"results_interpretation_{step_name}")
                interpretation_cell = nbf.v4.new_markdown_cell(
                    f"### Agent Interpretation\n\n{results_interpretation}"
                )
                notebook.cells.append(interpretation_cell)
                self.save_notebook_snapshot(notebook, analysis_idx, step_idx, "after_interpretation")

            else:
                print(f"⚠️ Code errored with: {error_msg}")
                self.logger.log_response(
                    f"STEP {step_idx} FAILED - Analysis {analysis_idx+1}\n\nCode:\n```r\n{current_code}\n\n Error:\n{error_msg}```",
                    f"step_execution_failed_{step_name}",
                )
                fix_attempt, fix_successful = 0, False
                results_interpretation = ""
                while fix_attempt < self.max_fix_attempts and not fix_successful:
                    fix_attempt += 1
                    print(f"  🔧 Fix attempt {fix_attempt}/{self.max_fix_attempts}")

                    documentation = ""
                    if self.use_documentation:
                        try:
                            documentation = get_documentation(current_code)
                        except Exception as e:
                            print(f"⚠️ Documentation extraction failed: {e}")
                            documentation = ""

                    current_code = self.fix_code(
                        current_code, error_msg, documentation=documentation,
                        analysis_idx=analysis_idx + 1, step=step_idx, attempt=fix_attempt,
                    )
                    current_code = ensure_r_cell_magic(strip_code_markers(current_code))
                    notebook.cells[-1] = nbf.v4.new_code_cell(current_code)

                    success, error_msg, notebook = self.run_last_cell(notebook)
                    self.save_notebook_snapshot(
                        notebook, analysis_idx, step_idx, f"after_fix_attempt_{fix_attempt}"
                    )

                    if success:
                        fix_successful = True
                        print(f"  ✅ Fix successful on attempt {fix_attempt}")
                        self.logger.log_response(
                            f"FIX SUCCESSFUL on attempt {fix_attempt}/{self.max_fix_attempts} - Analysis {analysis_idx+1}, Step {step_idx}",
                            f"fix_attempt_success_{step_name}_{fix_attempt}",
                        )
                        updated_description = self.generate_code_description(
                            current_code, analysis_idx=analysis_idx + 1, step=step_idx,
                        )
                        for i in range(len(notebook.cells) - 1, -1, -1):
                            cell = notebook.cells[i]
                            if (
                                cell.cell_type == "markdown"
                                and str(cell.source).startswith("##")
                                and "Agent Interpretation" not in str(cell.source)
                            ):
                                cell.source = f"## {updated_description}"
                                break
                        print("🧪 Interpreting step results...")
                        results_interpretation = self.interpret_results(
                            notebook, past_analyses, hypothesis, analysis_plan, current_code,
                            analysis_idx=analysis_idx + 1, step=step_idx,
                        )
                        self.logger.log_response(
                            results_interpretation, f"results_interpretation_{step_name}"
                        )
                        interpretation_cell = nbf.v4.new_markdown_cell(
                            f"### Agent Interpretation\n\n{results_interpretation}"
                        )
                        notebook.cells.append(interpretation_cell)
                        self.save_notebook_snapshot(
                            notebook, analysis_idx, step_idx, "after_interpretation"
                        )
                        break
                    else:
                        print(f"  ❌ Fix attempt {fix_attempt} failed")
                        self.logger.log_response(
                            f"FIX ATTEMPT FAILED {fix_attempt}/{self.max_fix_attempts} - Analysis {analysis_idx+1}, Step {step_idx}: {error_msg}\n\nCode:\n```r\n{current_code}\n```",
                            f"fix_attempt_failed_{step_name}_{fix_attempt}",
                        )
                        if fix_attempt == self.max_fix_attempts:
                            print(
                                f"  ⚠️ Failed to fix after {self.max_fix_attempts} attempts. Moving to next iteration."
                            )
                            self.logger.log_response(
                                f"ALL FIX ATTEMPTS EXHAUSTED - Analysis {analysis_idx+1}, Step {step_idx}. Failed after {self.max_fix_attempts} attempts.",
                                f"fix_attempt_exhausted_{step_name}",
                            )
                            results_interpretation = (
                                "Current analysis step failed to run. Try an alternative approach"
                            )
                            interpretation_cell = nbf.v4.new_markdown_cell(
                                f"### Agent Interpretation\n\n{results_interpretation}"
                            )
                            notebook.cells.append(interpretation_cell)
                            self.save_notebook_snapshot(
                                notebook, analysis_idx, step_idx, "after_interpretation"
                            )
                if not results_interpretation:
                    results_interpretation = self.interpret_results(
                        notebook, past_analyses, hypothesis, analysis_plan, current_code,
                        analysis_idx=analysis_idx + 1, step=step_idx,
                    )
                    interpretation_cell = nbf.v4.new_markdown_cell(
                        f"### Agent Interpretation\n\n{results_interpretation}"
                    )
                    notebook.cells.append(interpretation_cell)
                    self.save_notebook_snapshot(notebook, analysis_idx, step_idx, "after_interpretation")

            hypotheses_analysis.append(hypothesis)

            if iteration < self.max_iterations - 1:
                num_steps_left = self.max_iterations - iteration - 1

                analysis = {
                    "hypothesis": hypothesis,
                    "analysis_plan": analysis_plan,
                    "first_step_code": current_code,
                }
                print("🧭 Generating next step plan...")
                next_step_analysis = self.generate_next_step_analysis(
                    analysis, past_analyses, notebook.cells, num_steps_left, seeded,
                    analysis_idx=analysis_idx + 1, step=step_idx,
                )

                first_step_description = (
                    next_step_analysis["analysis_plan"][0]
                    if next_step_analysis["analysis_plan"]
                    else "No additional analysis steps generated"
                )
                self.logger.log_response(
                    f"NEXT STEP PLAN - Analysis {analysis_idx+1}, Step {iteration + 2}: {first_step_description}\n\nCode:\n```r\n{next_step_analysis['first_step_code']}\n```",
                    f"initial_analysis_{step_name}",
                )

                if self.use_self_critique:
                    print("🔍 Reviewing next step plan...")
                    modified_analysis = self.hypothesis_generator.get_feedback(
                        next_step_analysis, past_analyses, notebook.cells, num_steps_left,
                        analysis_idx=analysis_idx + 1,
                        step=step_idx,
                    )
                    self.logger.log_response(
                        f"APPLIED SELF-CRITIQUE - Analysis {analysis_idx+1}, Step {iteration + 2}",
                        f"self_critique_{step_name}",
                    )
                    hypothesis = modified_analysis["hypothesis"]
                    analysis_plan = modified_analysis["analysis_plan"]
                    current_code = modified_analysis["first_step_code"]
                    self.logger.log_response(
                        f"Revised Hypothesis: {hypothesis}\n\nRevised Analysis Plan:\n"
                        + "\n".join([f"{i+1}. {step}" for i, step in enumerate(analysis_plan)])
                        + f"\n\nRevised Code:\n{current_code}",
                        f"revised_analysis_{step_name}",
                    )
                else:
                    print("🚫 Skipping feedback on next step (no self-critique)")
                    self.logger.log_response(
                        f"SKIPPING INITIAL SELF-CRITIQUE - Analysis {analysis_idx+1}",
                        f"no_self_critique_{step_name}",
                    )
                    modified_analysis = next_step_analysis

                next_title = (
                    modified_analysis["analysis_plan"][0]
                    if modified_analysis["analysis_plan"]
                    else "No additional analysis steps generated"
                )
                print(f"➡️ Next step ready: {short_step_text(next_title)}")

                steps_text = "\n".join(
                    [f"Step {i+1}: {item}" for i, item in enumerate(modified_analysis["analysis_plan"])]
                )
                next_step_cell = nbf.v4.new_markdown_cell(f"## Next Steps\n{steps_text}")
                notebook.cells.append(next_step_cell)
                code_description = modified_analysis["code_description"]
                notebook.cells.append(nbf.v4.new_markdown_cell(f"## {code_description}"))
                modified_code = ensure_r_cell_magic(strip_code_markers(modified_analysis["first_step_code"]))
                notebook.cells.append(new_code_cell(modified_code))
                current_code = modified_code
                self.save_notebook_snapshot(notebook, analysis_idx, step_idx, "after_planning")

            self.update_code_memory(notebook.cells)

        # Reserved synthesis step — runs outside the iteration loop so it is
        # guaranteed to execute and the notebook ends on a holistic wrap-up
        # rather than the last step's forward-looking interpretation.
        print("📝 Generating conclusion...")
        try:
            conclusion = self.generate_conclusion(
                notebook, hypothesis, analysis_plan, analysis_idx=analysis_idx + 1,
            )
            self.logger.log_response(conclusion, f"conclusion_{analysis_idx+1}")
            notebook.cells.append(
                nbf.v4.new_markdown_cell(f"## Conclusion\n\n{conclusion}")
            )
            self.save_notebook_snapshot(notebook, analysis_idx, self.max_iterations + 1, "conclusion")
        except Exception as e:
            print(f"⚠️ Failed to generate conclusion: {e}")
            self.logger.log_response(
                f"CONCLUSION GENERATION FAILED - Analysis {analysis_idx+1}: {e}",
                f"conclusion_failed_{analysis_idx+1}",
            )

        notebook_path = os.path.join(
            self.output_dir, f"{self.analysis_name}_analysis_{analysis_idx+1}.ipynb"
        )
        with open(notebook_path, "w", encoding="utf-8") as f:
            clean_notebook = self.cleanup_notebook_outputs(notebook)
            nbf.write(clean_notebook, f)
            print(f"💾 Saved notebook to: {notebook_path}")

        self.logger.log_response(
            f"ANALYSIS {analysis_idx+1} COMPLETED - Notebook saved to: {notebook_path}",
            "analysis_complete",
        )

        self.stop_persistent_kernel()

        del notebook
        import gc
        gc.collect()

        print(f"✅ Completed Analysis {analysis_idx+1}")

        if hypotheses_analysis:
            analysis_summary = f"Analysis {analysis_idx+1}: {hypothesis}\n"
            return past_analyses + analysis_summary
        else:
            return past_analyses
