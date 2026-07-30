"""Failure posture: what the pipeline does when a stage goes wrong.

Extracted from ``run.py`` (ACTPULSE-23, phase 2). These are the **pure** half of
the posture logic — policy decisions and digest builders that need only their
arguments. The half that needs a live ``RunContext`` (``_guard``,
``_enrich_guard``, ``_degrade_stage``, ``_persist_raw_digest``,
``_finish_degraded``) stays in ``run.py`` on purpose: it calls the stage functions,
so moving it here would import ``run`` and create exactly the cycle
``pipeline/`` exists to avoid.

The three postures, which are genuinely different and easy to confuse:

* **degrade** — a pre-LLM stage failed; emit a partial digest and keep going.
  Cheap to re-run, so losing the work costs little.
* **skip** — a post-LLM enrichment pass failed; keep the digest, drop the
  enrichment. Aborting here would throw away a paid LLM call; degrading would
  throw away the items.
* **crash** — ASSEMBLE failed. There is no digest to salvage, so failing loudly
  beats writing something wrong.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from digest_core.assemble.labels import (
    DEFAULT_LANGUAGE,
    STATUS,
    report_strings,
    section_sort_weight,
    section_title,
)
from digest_core.config import Config
from digest_core.llm.schemas import Digest


class _StageDegraded(Exception):
    """Internal signal carrying a degraded digest from a failed early stage."""

    def __init__(self, digest: Digest):
        super().__init__("stage degraded")
        self.digest = digest


def _is_operational_error(exc: Exception, *, replay: bool = False) -> bool:
    """Operational (degradable) vs config/precondition failure.

    Network errors always degrade. A missing/invalid file (OSError, e.g.
    FileNotFoundError) degrades only in replay mode (a missing snapshot); in live
    mode it is a configuration error (e.g. a bad ``verify_ca`` path) that must
    fail loud rather than silently produce an empty digest.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if replay and isinstance(exc, OSError):
        return True
    return False


#: Per-stage failure action. "empty"/"partial" degrade to a digest the user can
#: still read; "crash" is reserved for ASSEMBLE, where there is nothing to salvage.
#: Lives beside `degradation_policy`, its only reader — it was three screens away
#: in run.py, which is how it got left behind on the first extraction attempt.
_DEGRADE_ACTIONS = {
    "ingest": "empty",
    "normalize": "empty",
    "threads": "partial",
    "evidence": "partial",
    "select": "partial",
    "assemble": "crash",
}


def degradation_policy(stage: str, exc: Exception, config: Config, *, replay: bool = False) -> str:
    """Pure policy: how a failed `stage` degrades -> 'crash' | 'partial' | 'empty'."""
    if not config.degrade.enable:
        return "crash"
    action = _DEGRADE_ACTIONS.get(stage, "crash")
    # Ingest/normalize is the source boundary: a config/precondition error
    # (missing credentials, bad verify_ca path, bad endpoint) must fail fast
    # rather than silently produce an empty digest. Only operational failures
    # (EWS unreachable; a missing replay snapshot in replay mode) degrade.
    if action == "empty" and not _is_operational_error(exc, replay=replay):
        return "crash"
    return action


def _build_empty_digest(digest_date: str, trace_id: str, prompt_version: str) -> Digest:
    return Digest(
        schema_version="1.0",
        prompt_version=prompt_version,
        digest_date=digest_date,
        trace_id=trace_id,
        sections=[],
    )


def _build_partial_digest(
    digest_date: str,
    trace_id: str,
    error_message: str,
    title: str | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> Digest:
    strings = report_strings(language)
    if title is None:
        title = strings["banner_llm_unavailable"]
        if "timed out" in error_message.lower() or "timeout" in error_message.lower():
            title = strings["banner_llm_timeout"]
    return Digest(
        schema_version="1.0",
        prompt_version="none",
        digest_date=digest_date,
        trace_id=trace_id,
        sections=[
            {
                "title": section_title(STATUS, language),
                "items": [
                    {
                        "title": title,
                        "due": None,
                        "evidence_id": "system",
                        "confidence": 0.0,
                        "source_ref": {"type": "system", "error": error_message},
                    }
                ],
            }
        ],
    )


def _sort_sections(sections: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized_sections = []
    for section in sections:
        items = section.get("items", [])
        if not items:
            continue
        normalized_sections.append({"title": section.get("title", ""), "items": items})
    return sorted(
        normalized_sections,
        key=lambda section: (section_sort_weight(section["title"]), section["title"]),
    )
