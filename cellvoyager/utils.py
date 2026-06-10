import os
import re
import sys
import functools

# Target R packages whose functions we surface help for. Mirrors the
# AVAILABLE_PACKAGES set in agent.py (monocle3-via-rpy2 backend).
_DOC_PACKAGES = ("monocle3", "SingleCellExperiment", "Matrix", "ggplot2")

# Matches the head of an R function call: an identifier (optionally qualified
# with pkg:: / pkg:::) immediately followed by '('. Captures the bare name.
# R identifiers may contain dots (e.g. as.data.frame), hence '.' in the class.
_R_CALL_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9.]*\s*:::?\s*)?"   # optional pkg:: / pkg::: prefix
    r"([A-Za-z.][A-Za-z0-9._]*)\s*\("          # the function name + '('
)

# Terminal overstrike pair emitted by Rd2txt for bold/underline: a glyph (or
# underscore) followed by a backspace and the real character. Stripping the
# "<x>\x08" half leaves clean readable text.
_OVERSTRIKE_RE = re.compile(r".\x08")


def extract_r_call_names(source: str):
    """Return the sorted, deduplicated R function names called in `source`.

    Heuristic, not a full R parser: it finds identifiers immediately followed by
    '(' (optionally pkg-qualified). This works whether the code is raw R, lives
    inside a `%%R` cell, or is wrapped in rpy2 `ro.r("...")` — the function name
    precedes the paren in every case. Over-matching is harmless: names that have
    no R help (Python wrappers, base-R built-ins, the `function` keyword) are
    dropped downstream because they are not exported by the target packages.
    """
    return sorted(set(_R_CALL_RE.findall(source)))


def _ensure_r():
    """Point R_HOME at this conda env's R (if unset) and return robjects.

    get_documentation runs in the orchestrator process, so — exactly like
    AnalysisAgentV2._summarize_cds — rpy2 would otherwise grab the system arm64 R
    and crash on an x86_64/arm64 arch mismatch. See the monocle3 migration spec.
    """
    if "R_HOME" not in os.environ:
        candidate = os.path.join(sys.prefix, "lib", "R")
        if os.path.isdir(candidate):
            os.environ["R_HOME"] = candidate
    import rpy2.robjects as ro
    return ro


# R helper returning the plain-text help page for a function name (or "" if none).
# do.call(help, list(name)) is used rather than help(name): help() captures its
# first argument with substitute(), so a bare variable would be looked up as the
# literal symbol `name`. do.call embeds the value as a character constant, which
# help() resolves as the topic. Help paths look like <lib>/<package>/help/<topic>,
# so we keep only pages owned by an allowed package — this drops base-R generics
# (print, summary, ...) that a target package happens to also export. The chosen
# Rd is rendered to text via Rd2txt.
_R_HELP_FN = r'''
function(fn_name, allowed) {
  paths <- tryCatch(
    as.character(do.call(utils::help, list(fn_name, help_type = "text"))),
    error = function(e) character(0)
  )
  if (length(paths) == 0L) return("")
  hit <- ""
  for (p in paths) {
    if (basename(dirname(dirname(p))) %in% allowed) { hit <- p; break }
  }
  if (identical(hit, "")) return("")
  rd <- tryCatch(utils:::.getHelpFile(hit), error = function(e) NULL)
  if (is.null(rd)) return("")
  txt <- tryCatch(
    capture.output(tools::Rd2txt(rd, package = "")),
    error = function(e) character(0)
  )
  paste(txt, collapse = "\n")
}
'''


@functools.lru_cache(maxsize=1)
def _r_doc_tools():
    """Initialize R once and return (help_fn, allowed_vec, exported_names).

    Loads the target packages, collects the union of their exported function
    names (a cheap pre-filter so we only query help for plausibly-relevant calls,
    mirroring the old `sc.*` filter), and compiles the help-text R function plus
    the allowed-package vector it filters against. Cached so the slow library
    loads happen a single time per process.
    """
    ro = _ensure_r()
    exported = set()
    loaded = []
    for pkg in _DOC_PACKAGES:
        try:
            ro.r(f'suppressMessages(library({pkg}))')
            names = ro.r(f'getNamespaceExports("{pkg}")')
            exported.update(str(n) for n in names)
            loaded.append(pkg)
        except Exception:
            # A package that fails to load just contributes no docs.
            pass
    help_fn = ro.r(_R_HELP_FN)
    allowed_vec = ro.StrVector(loaded)
    return help_fn, allowed_vec, exported


def get_documentation(code: str, max_characters: int = 10000) -> str:
    """Return R help text for the monocle3/SCE/Matrix/ggplot2 functions in `code`.

    R analog of the old scanpy `inspect.getdoc` helper: extracts R call names,
    keeps those exported by the target packages, and pulls each function's help
    page via rpy2, concatenated and capped at `max_characters`. Returns a short
    marker string on hard failure rather than raising, so the critique/fix loop
    degrades gracefully when R or the docs are unavailable.
    """
    try:
        help_fn, allowed_vec, exported = _r_doc_tools()
    except Exception as e:
        return f"<documentation unavailable (R init failed): {e}>"

    try:
        call_names = extract_r_call_names(code)
    except Exception as e:
        return f"<documentation extraction failed: {e}>"

    docs = []
    for name in call_names:
        if name not in exported:
            continue
        try:
            # Rd2txt renders bold/underline as terminal overstrikes
            # (`char\x08char`, `_\x08char`); collapse each to its final glyph.
            text = _OVERSTRIKE_RE.sub("", str(help_fn(name, allowed_vec)[0])).strip()
        except Exception as e:
            text = f"<could not resolve: {e}>"
        if text:
            docs.append(f"{name}:\n{text}")

    return "\n\n".join(docs)[:max_characters]
