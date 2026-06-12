"""Pipeline progress event seam (TERMINAL_DESIGN.md §4.4, roadmap T2).

``run.py`` emits events; sinks render them. structlog JSON logging is a
parallel channel and stays untouched — the live channel renders *state*,
never log lines (design P3/P4).

``NullSink`` is the default: ``cli run`` stays visually unchanged until the
plain and live renderers arrive behind ``--progress`` (T3/T4). Sinks must
never raise: a broken renderer must not break the pipeline, so the emit
helper in ``run.py`` swallows sink exceptions after logging them.

Event vocabulary (counts carry the funnel numbers — the "sense of the
machinery" from the design doc §4.2):

- ``on_stage_start(stage)`` — a pipeline stage began.
- ``on_stage_end(stage, counts, duration_ms)`` — it finished;
  ``counts`` examples: ``{"messages": 124}``, ``{"threads": 37}``,
  ``{"selected": 28, "of": 41}``, ``{"sections": 3, "items": 7}``.
- ``on_stage_failed(stage, error)`` — it degraded/failed (the run may
  continue per the degradation policy).
- ``on_llm_attempt(model, attempt, max_attempts)`` — an extractor call is
  about to start. Per-attempt token granularity arrives with the gateway
  hooks in T5; stage-level totals live in ``run_meta["llm_budget"]``.
- ``on_delivery(target, ok, detail)`` — delivery outcome.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ProgressSink:
    """No-op base sink. Renderers subclass and override what they need."""

    def on_stage_start(self, stage: str) -> None:  # pragma: no cover - no-op
        pass

    def on_stage_end(
        self, stage: str, counts: Dict[str, Any], duration_ms: int
    ) -> None:  # pragma: no cover - no-op
        pass

    def on_stage_failed(self, stage: str, error: str) -> None:  # pragma: no cover - no-op
        pass

    def on_llm_attempt(
        self, model: str, attempt: int, max_attempts: int
    ) -> None:  # pragma: no cover - no-op
        pass

    def on_delivery(
        self, target: str, ok: bool, detail: Optional[str] = None
    ) -> None:  # pragma: no cover - no-op
        pass

    def on_run_end(self, status: str) -> None:  # pragma: no cover - no-op
        """The pipeline finished (ok/partial/skipped/failed) — release the
        terminal: live renderers stop their region and restore the cursor
        here. Emitted from the same finally block as the OTel run span, so
        it fires on every exit path."""
        pass


#: Default sink — explicit name for call sites and signatures.
NullSink = ProgressSink
