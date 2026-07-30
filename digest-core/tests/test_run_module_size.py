"""run.py must shrink, not grow (ACTPULSE-23).

The 2026-07 architecture review measured `run.py` at 2,107 lines / 64 module-level
functions and called it a god object. By 2026-07-30 it was **2,607 / 69** — and
+133 of that arrived the same day the review's findings were being *fixed*. Every
change is individually defensible and lands in the same file, which is exactly how
accretion works: nobody decides to make it worse.

This is a ratchet, not a limit. It fails when the file grows past the current
extraction point, so adding to `run.py` becomes a visible decision instead of a
silent one. Lower the numbers as phases 2–3 land; never raise them without saying
why in the commit.
"""

from __future__ import annotations

import ast
from pathlib import Path

RUN_PY = Path(__file__).resolve().parents[1] / "src" / "digest_core" / "run.py"

#: Phase 1 (pipeline/idempotency.py extracted) left run.py here. Ratchet down.
MAX_LINES = 2500
MAX_MODULE_FUNCTIONS = 62


def _tree() -> ast.Module:
    return ast.parse(RUN_PY.read_text(encoding="utf-8"))


def test_run_py_does_not_grow_past_the_current_extraction_point():
    actual = len(RUN_PY.read_text(encoding="utf-8").splitlines())
    assert actual <= MAX_LINES, (
        f"run.py is {actual} lines (ratchet {MAX_LINES}). It was 2,107 when the review "
        "called it a god object and 2,607 at its peak. If this change genuinely belongs "
        "in run.py, lower the ratchet in the same commit and say why — otherwise it "
        "belongs in digest_core/pipeline/."
    )


def test_module_level_function_count_does_not_grow():
    count = sum(
        1 for node in _tree().body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    assert count <= MAX_MODULE_FUNCTIONS, (
        f"run.py defines {count} module-level functions (ratchet {MAX_MODULE_FUNCTIONS}). "
        "A new helper here is a new reason the orchestrator is hard to read."
    )


def test_the_ratchet_is_honest_about_where_it_stands():
    """A ratchet set far above reality silently permits regrowth."""
    actual = len(RUN_PY.read_text(encoding="utf-8").splitlines())
    assert actual > MAX_LINES - 200, (
        f"run.py ({actual}) is well under the ratchet ({MAX_LINES}) — an extraction "
        "landed without tightening it. Lower MAX_LINES to lock the gain in."
    )


class TestExtractedModulesStayLeaves:
    """`pipeline/` may not import `run` — that is the whole point of the split.

    The prior attempt was deferred on a recorded 'real circular-import risk'. There
    was none: nothing inside digest_core imports run except cli. This test keeps it
    that way, so the reason can't quietly become true later.
    """

    def test_pipeline_modules_never_import_run(self):
        pipeline = RUN_PY.parent / "pipeline"
        offenders = []
        for path in pipeline.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "digest_core.run"
                ):
                    offenders.append(path.name)
                elif isinstance(node, ast.Import):
                    if any(a.name.startswith("digest_core.run") for a in node.names):
                        offenders.append(path.name)
        assert not offenders, f"pipeline modules importing run (cycle): {sorted(set(offenders))}"

    #: Modules that import `run` today. `cli` at import time; the rest inside
    #: functions, which is why a line-anchored grep misses them — it did, and this
    #: test caught the overstatement.
    KNOWN_IMPORTERS = {
        "cli.py",
        "daemon/tick.py",
        "eval/corpus.py",
        "eval/best_of_n_harness.py",
    }

    def test_no_new_importers_of_run_appear(self):
        """Every additional importer makes extraction harder — notice it early."""
        src = RUN_PY.parent
        importers = set()
        for path in src.rglob("*.py"):
            if path.name == "run.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "from digest_core.run import" in text or "from digest_core import run" in text:
                importers.add(path.relative_to(src).as_posix())
        new = importers - self.KNOWN_IMPORTERS
        assert not new, (
            f"new importers of run.py: {sorted(new)}. Prefer importing the extracted "
            "leaf module (digest_core/pipeline/) over widening run.py's blast radius."
        )
