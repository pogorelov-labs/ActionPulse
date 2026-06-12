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
- ``on_stage_progress(stage, done, total, unit, detail)`` — intra-stage data
  progress from a bounded producer loop (EWS paging, normalize, evidence
  split). Producers emit numbers; renderers own wording and throttling
  (design §3: Live pulls state at 10 fps, PlainSink prints a >=10 s
  reassurance line). ``total`` is None for unbounded loops — never render a
  percentage for an estimated total.
- ``on_stage_retry(stage, attempt, max_attempts, reason)`` — a transient
  failure scheduled a retry (EWS reconnect, LLM 429/5xx). This is the event
  that makes a silent backoff legible: the live footer warms immediately,
  the plain log prints one warn line per retry (rare by construction).
- ``on_stage_end(stage, counts, duration_ms)`` — it finished;
  ``counts`` examples: ``{"messages": 124}``, ``{"threads": 37}``,
  ``{"selected": 28, "of": 41}``, ``{"sections": 3, "items": 7}``.
  Optional ``retries``/``errors`` keys (present only when nonzero) render as
  a warn suffix on the permanent line and land in run_meta["stage_health"].
- ``on_stage_failed(stage, error)`` — it degraded/failed (the run may
  continue per the degradation policy).
- ``on_llm_attempt(model, attempt, max_attempts)`` — an extractor call is
  about to start (the quality retry emits attempt 2/2). Per-attempt token
  granularity arrives with the fleet gateway hooks; stage-level totals live
  in ``run_meta["llm_budget"]``.
- ``on_lane_update(lane, state)`` — fleet lane telemetry (design §4.3): one
  lane per MODEL, emitted by the gateway/fleet clients around real network
  calls only (replay stays silent — lanes are never theater). ``state``:
  ``{"model", "stage", "in_flight" (0|1 — intra-model serial), "calls",
  "rpm_used", "rpm_cap", "penalty_remaining_s"}``. Renderers cap visible
  lanes at 4 and clear them on stage transitions.
- ``on_delivery(target, ok, detail)`` — delivery outcome.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger()


class ProgressSink:
    """No-op base sink. Renderers subclass and override what they need."""

    def on_stage_start(self, stage: str) -> None:  # pragma: no cover - no-op
        pass

    def on_stage_progress(
        self,
        stage: str,
        done: int,
        total: Optional[int] = None,
        unit: str = "",
        detail: str = "",
    ) -> None:  # pragma: no cover - no-op
        pass

    def on_stage_retry(
        self, stage: str, attempt: int, max_attempts: int, reason: str
    ) -> None:  # pragma: no cover - no-op
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

    def on_lane_update(self, lane: str, state: Dict[str, Any]) -> None:  # pragma: no cover - no-op
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


def emit(sink: ProgressSink, method: str, *args: Any, **kwargs: Any) -> None:
    """Fire a sink event from any producer (run.py, ingest, gateway).

    The sink contract: a broken renderer must never break the pipeline —
    every emission site goes through this swallow-and-log helper.
    """
    try:
        getattr(sink, method)(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - sink errors are non-fatal by contract
        logger.warning("Progress sink failed", method=method, error=str(exc))
