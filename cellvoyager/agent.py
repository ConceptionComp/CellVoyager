"""
CellVoyager agent: Modular architecture with separate hypothesis generation and execution.
Uses HypothesisGenerator (cellvoyager.hypothesis) and either IdeaExecutor
(cellvoyager.execution.legacy) or ClaudeJupyterExecutor (cellvoyager.execution.claude).
"""
import os
import sys
import datetime

from cellvoyager.hypothesis import HypothesisGenerator
from cellvoyager.llm_utils import create_gemini_client, create_openai_client, get_model_provider
from cellvoyager.execution.legacy import IdeaExecutor
from cellvoyager.logger import Logger
from cellvoyager.deepresearch import DeepResearcher

AVAILABLE_PACKAGES = "monocle3, SingleCellExperiment, Matrix, ggplot2, patchwork"


class AnalysisAgentV2:
    def __init__(
        self,
        rds_path,
        paper_summary_path,
        model_name,
        analysis_name,
        openai_api_key=None,
        num_analyses=5,
        max_iterations=6,
        prompt_dir=None,
        output_home=".",
        output_dir=None,
        log_home=".",
        use_self_critique=True,
        use_VLM=True,
        use_documentation=True,
        log_prompts=False,
        log_responses=False,
        interactive=False,
        max_fix_attempts=3,
        use_deepresearch_background=True,
        execution_mode="legacy",
        anthropic_api_key=None,
        **execution_kwargs,
    ):
        """
        Args:
            execution_mode: "legacy" (default) uses IdeaExecutor;
                "claude" uses ClaudeJupyterExecutor from execution.py (live Jupyter + Claude Agent SDK).
            anthropic_api_key: Required when execution_mode="claude". Can also set ANTHROPIC_API_KEY env.
            **execution_kwargs: Passed to ClaudeJupyterExecutor when execution_mode="claude",
                e.g. jupyter_port=8888, auto_start_jupyter=True, stop_jupyter_on_complete=False.
        """
        self.rds_path = rds_path
        self.paper_summary = open(paper_summary_path).read()
        self.openai_api_key = openai_api_key
        self.model_name = model_name
        self.analysis_name = analysis_name
        self.max_iterations = max_iterations
        self.num_analyses = num_analyses
        self.prompt_dir = prompt_dir or os.path.join(os.path.dirname(__file__), "prompts")
        # interactive here means "file-based prompt editing" — only meaningful in legacy mode.
        # Claude/OpenCode modes pass interactive via execution_kwargs as interactive_mode.
        _file_interactive = interactive and execution_mode == "legacy"
        self.log_prompts = log_prompts or _file_interactive
        self.log_responses = log_responses or _file_interactive
        self.interactive = _file_interactive
        self.max_fix_attempts = max_fix_attempts
        self.use_deepresearch_background = use_deepresearch_background

        if output_dir is not None:
            self.output_dir = os.path.abspath(output_dir)
        else:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.output_dir = os.path.join(output_home, "outputs", f"{analysis_name}_{timestamp}")

        if get_model_provider(model_name) == "gemini":
            self.client = create_gemini_client()
        else:
            self.client = create_openai_client(api_key=openai_api_key)

        self.use_self_critique = use_self_critique
        self.use_VLM = use_VLM
        self.use_documentation = use_documentation

        os.makedirs(self.output_dir, exist_ok=True)

        # Coding guidelines (same as agent.py)
        self._analyses_overview = open(os.path.join(self.prompt_dir, "DeepResearch_Analyses.txt")).read()
        if self.use_VLM:
            coding_guidelines_template = open(
                os.path.join(self.prompt_dir, "coding_guidelines.txt")
            ).read()
        else:
            coding_guidelines_template = open(
                os.path.join(self.prompt_dir, "ablations", "coding_guidelines_NO_VLM_ABLATION.txt")
            ).read()

        self.coding_system_prompt = open(
            os.path.join(self.prompt_dir, "coding_system_prompt.txt")
        ).read().format(max_iterations=self.max_iterations)

        self.coding_guidelines = coding_guidelines_template.format(
            name=self.analysis_name,
            adata_path=self.rds_path,
            available_packages=AVAILABLE_PACKAGES,
            analyses_overview=self._analyses_overview,
        )

        self.logger = Logger(self.analysis_name, log_dir=os.path.join(log_home, "logs"))

        # Load the Monocle3 cell_data_set (.RDS) and build a text summary for planning.
        # This runs in the orchestrator process via rpy2 (not the Jupyter kernel), so
        # _summarize_cds points R_HOME at this env's R before importing rpy2.
        if self.rds_path == "":
            self.adata_summary = ""
        else:
            print("Loading CDS (.RDS) for summarization via rpy2...")
            self.adata_summary = self._summarize_cds(self.rds_path)
            print(f"✅ Loaded summary from {self.rds_path}")

        # DeepResearch for idea generation
        self.deepresearch_background = ""
        if self.use_deepresearch_background:
            print("Running DeepResearch...")
            try:
                deepresearch = DeepResearcher(self.openai_api_key)
                dr_summary = deepresearch.research_from_paper_summary(
                    self.paper_summary, self.adata_summary, AVAILABLE_PACKAGES
                )
                self.deepresearch_background = dr_summary.strip()
                print("✅ DeepResearch completed")
                print("DEEPRESEARCH BACKGROUND: ", self.deepresearch_background[:100])
            except Exception as e:
                print(f"Warning: DeepResearch failed or was skipped: {e}")

        # (1) Hypothesis generation module
        self.hypothesis_generator = HypothesisGenerator(
            model_name=self.model_name,
            prompt_dir=self.prompt_dir,
            coding_guidelines=self.coding_guidelines,
            coding_system_prompt=self.coding_system_prompt,
            adata_summary=self.adata_summary,
            paper_summary=self.paper_summary,
            logger=self.logger,
            use_self_critique=self.use_self_critique,
            use_documentation=self.use_documentation,
            max_iterations=self.max_iterations,
            deepresearch_background=self.deepresearch_background,
            log_prompts=self.log_prompts,
            log_responses=self.log_responses,
            interactive=self.interactive,
            output_dir=self.output_dir,
        )

        # (2) Idea execution module
        shared_executor_kwargs = dict(
            hypothesis_generator=self.hypothesis_generator,
            client=self.client,
            model_name=self.model_name,
            prompt_dir=self.prompt_dir,
            coding_guidelines=self.coding_guidelines,
            coding_system_prompt=self.coding_system_prompt,
            adata_summary=self.adata_summary,
            paper_summary=self.paper_summary,
            logger=self.logger,
            rds_path=self.rds_path,
            output_dir=self.output_dir,
            analysis_name=self.analysis_name,
            max_iterations=self.max_iterations,
            max_fix_attempts=self.max_fix_attempts,
            use_self_critique=self.use_self_critique,
            use_VLM=self.use_VLM,
            use_documentation=self.use_documentation,
            log_prompts=self.log_prompts,
            log_responses=self.log_responses,
            interactive=self.interactive,
        )

        if execution_mode == "claude":
            from cellvoyager.execution.claude import ClaudeJupyterExecutor
            self.executor = ClaudeJupyterExecutor(
                **shared_executor_kwargs,
                anthropic_api_key=anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"),
                **execution_kwargs,
            )
        elif execution_mode == "opencode":
            from cellvoyager.execution.opencode import OpenCodeJupyterExecutor
            self.executor = OpenCodeJupyterExecutor(
                **shared_executor_kwargs,
                **execution_kwargs,
            )
        else:
            self.executor = IdeaExecutor(**shared_executor_kwargs)

    def _ensure_r(self):
        """Initialize rpy2 against this env's R and return the robjects module.

        The summarizer runs in the orchestrator process (not the Jupyter kernel),
        so R_HOME may be unset here. If so, point it at <sys.prefix>/lib/R — the R
        shipped in the CellVoyager-r conda env. Without this, rpy2 grabs the system
        arm64 R and crashes on an x86_64/arm64 arch mismatch (see the migration spec).
        """
        if "R_HOME" not in os.environ:
            candidate = os.path.join(sys.prefix, "lib", "R")
            if os.path.isdir(candidate):
                os.environ["R_HOME"] = candidate
        import rpy2.robjects as ro
        return ro

    # R routine that summarizes a cell_data_set as a text block. Mirrors the old
    # AnnData summarizer's shape (per-column unique values, capped at length_cutoff)
    # but reports CDS equivalents: colData≈.obs, rowData≈.var, reducedDims≈.obsm,
    # assays≈.X/layers.
    _R_CDS_SUMMARY = r'''
    function(rds_path, length_cutoff) {
      suppressMessages(library(monocle3))
      cds <- readRDS(rds_path)
      parts <- character(0)
      add <- function(s) parts[[length(parts) + 1L]] <<- s

      n_genes <- nrow(cds); n_cells <- ncol(cds)
      add(sprintf("cds (cell_data_set): %d cells × %d genes\n", n_cells, n_genes))

      summarize_df <- function(df) {
        if (is.null(df) || ncol(df) == 0L) return("  (empty)")
        lines <- character(0)
        for (col in colnames(df)) {
          vals <- df[[col]]
          vals <- vals[!is.na(vals)]
          u <- unique(vals)
          if (length(u) == 0L) {
            s <- "(all NA/empty)"
          } else if (length(u) > length_cutoff) {
            s <- paste0(paste(as.character(head(u, length_cutoff)), collapse = ", "),
                        sprintf(" ... and %d more", length(u) - length_cutoff))
          } else {
            s <- paste(as.character(u), collapse = ", ")
          }
          lines <- c(lines, sprintf("  %s: %s", col, s))
        }
        paste(lines, collapse = "\n")
      }

      add("--- colData(cds)  [cell metadata; analog of adata.obs] ---")
      add(summarize_df(as.data.frame(colData(cds))))

      rd <- as.data.frame(rowData(cds))
      if (!is.null(rd) && ncol(rd) > 0L) {
        add("\n--- rowData(cds)  [gene metadata; analog of adata.var] ---")
        add(summarize_df(rd))
      }

      rdims <- reducedDimNames(cds)
      if (length(rdims) > 0L) {
        add("\n--- reducedDims(cds)  [analog of adata.obsm] ---")
        for (nm in rdims) {
          d <- dim(reducedDims(cds)[[nm]])
          add(sprintf("  %s: shape (%d, %d)", nm, d[1], d[2]))
        }
      }

      an <- assayNames(cds)
      if (length(an) > 0L) {
        add("\n--- assays(cds)  [count matrices; analog of adata.X / layers] ---")
        for (nm in an) {
          d <- dim(assay(cds, nm))
          add(sprintf("  %s: shape (%d, %d)  [genes × cells]", nm, d[1], d[2]))
        }
      }

      paste(parts, collapse = "\n")
    }
    '''

    def _summarize_cds(self, rds_path, length_cutoff=25):
        """Summarize a Monocle3 cell_data_set (.RDS) via rpy2 as a text block.

        Returns the same shape of text summary the old AnnData path produced, so
        downstream planning prompts are unaffected. On any failure, returns a short
        error string rather than raising, matching the old summarizer's behavior.
        """
        try:
            ro = self._ensure_r()
            summarize = ro.r(self._R_CDS_SUMMARY)
            result = summarize(rds_path, length_cutoff)
            return str(result[0])
        except Exception as e:
            return f"Could not summarize CDS at {rds_path}: {e}"

    def run(self, seeded_hypotheses=None):
        """
        Main run method that orchestrates both idea generation and execution phases.

        Args:
            seeded_hypotheses: Optional list of hypothesis strings for AI to develop into full analyses.
        """
        past_analyses = ""
        self.logger.log_trace_json(
            "agent_run_start",
            {
                "analysis_name": self.analysis_name,
                "num_analyses": self.num_analyses,
                "max_iterations": self.max_iterations,
            },
        )

        for analysis_idx in range(self.num_analyses):
            seeded_hypothesis, seeded = None, False

            if seeded_hypotheses and analysis_idx < len(seeded_hypotheses):
                seeded_hypothesis = seeded_hypotheses[analysis_idx]
                seeded = True

            try:
                self.logger.log_trace(
                    "agent_analysis_start",
                    f"analysis_idx={analysis_idx} seeded={seeded}",
                )
                # Phase 1: Idea Generation (hypothesis.py)
                analysis = self.hypothesis_generator.generate_idea(
                    past_analyses, analysis_idx, seeded_hypothesis
                )
                print(f"🚀 Generated Initial Analysis Plan for Analysis {analysis_idx+1}")

                # Phase 2: Idea Execution
                past_analyses = self.executor.execute_idea(
                    analysis, past_analyses, analysis_idx, seeded=seeded
                )
                self.logger.log_trace(
                    "agent_analysis_complete",
                    f"analysis_idx={analysis_idx} past_analyses_chars={len(past_analyses or '')}",
                )
                print(f"✅ Completed Analysis {analysis_idx+1}")

                # In interactive mode, pause between analyses so the user can review
                # the completed notebook and optionally provide feedback before continuing.
                if analysis_idx + 1 < self.num_analyses and hasattr(self.executor, "inter_analysis_pause"):
                    nb_path = os.path.join(
                        self.output_dir, f"{self.analysis_name}_analysis_{analysis_idx + 1}.ipynb"
                    )
                    _user_stopped = False
                    while True:
                        feedback = self.executor.inter_analysis_pause(nb_path, analysis_idx)
                        if feedback in ("__STOP__", "__FINISH__"):
                            print(f"⏹ User stopped after Analysis {analysis_idx + 1}.")
                            _user_stopped = True
                            break
                        if feedback.startswith("__CONTINUE_CURRENT__"):
                            user_note = feedback[len("__CONTINUE_CURRENT__"):].lstrip(":").strip()
                            print(f"📝 Extending Analysis {analysis_idx + 1} further...")
                            self.executor.resume_from_notebook(
                                nb_path, analysis_idx,
                                user_feedback=user_note or None,
                                extend=True,
                            )
                            continue
                        if feedback:
                            past_analyses += f"User feedback before Analysis {analysis_idx + 2}: {feedback}\n\n"
                        break
                    if _user_stopped:
                        break

            except ValueError as e:
                if "OpenAI API refused" in str(e) or "Model API returned None" in str(e):
                    print(f"🚫 API refusal/error for Analysis {analysis_idx+1}. Skipping to next analysis.")
                    print(f"   Error: {str(e)}")
                    self.logger.log_trace(
                        "agent_analysis_skipped",
                        f"analysis_idx={analysis_idx} error={str(e)}",
                    )
                    past_analyses += f"Analysis {analysis_idx+1}: Skipped due to API refusal/error.\n\n"
                    continue
                else:
                    raise

        # Clean up resources (IdeaExecutor owns the kernel; ClaudeJupyterExecutor manages Jupyter)
        if hasattr(self.executor, "stop_persistent_kernel"):
            self.executor.stop_persistent_kernel()
        self.logger.log_trace("agent_run_complete", f"analysis_name={self.analysis_name}")
        import gc
        gc.collect()

    def run_resume(self, notebook_path: str, analysis_idx: int = 0):
        """
        Resume a completed analysis: re-run the notebook to restore kernel state,
        then enter interactive mode for the user to run, edit, and give feedback.
        Only supported with ClaudeJupyterExecutor.
        """
        if hasattr(self.executor, "resume_from_notebook"):
            self.executor.resume_from_notebook(notebook_path, analysis_idx)
        else:
            raise ValueError("Resume requires execution_mode=claude (ClaudeJupyterExecutor)")
