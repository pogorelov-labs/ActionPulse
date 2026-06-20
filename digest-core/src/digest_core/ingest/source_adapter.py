"""Source adapter protocol + resilient multi-source ingest (PR12b).

``run_sources`` runs each adapter behind its own try/except so one source being
down never crashes the run (degrade-not-drop at the source boundary). EWS is
wrapped as the single live adapter; the seam is ready for more sources without
building them.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, runtime_checkable

import structlog

from digest_core.ingest.envelope import Envelope, envelopes_from_messages
from digest_core.ingest.ews import EWSIngest, NormalizedMessage

logger = structlog.get_logger()

#: User-facing source names and their aliases. The single source of truth for the
#: name→adapter dispatch that run.py and the InboxAPI used to each re-spell inline.
EWS_SOURCE_NAMES = frozenset({"ews", "email"})
MM_SOURCE_NAMES = frozenset({"mm", "mattermost"})
CALENDAR_SOURCE_NAMES = frozenset({"calendar", "cal"})


def canonical_source(name: str) -> Optional[str]:
    """Map a source name/alias to its canonical key (``"ews"`` | ``"mm"`` | ``"calendar"``)."""
    key = (name or "").strip().lower()
    if key in EWS_SOURCE_NAMES:
        return "ews"
    if key in MM_SOURCE_NAMES:
        return "mm"
    if key in CALENDAR_SOURCE_NAMES:
        return "calendar"
    return None


@runtime_checkable
class SourceAdapter(Protocol):
    name: str

    def fetch(self, digest_date: str) -> List[NormalizedMessage]: ...


class EWSSourceAdapter:
    """Adapts EWSIngest to the SourceAdapter protocol."""

    name = "ews"

    def __init__(self, ingest: EWSIngest):
        self._ingest = ingest

    def fetch(self, digest_date: str) -> List[NormalizedMessage]:
        return self._ingest.fetch_messages(digest_date, self._ingest.time_config)


class CalendarSourceAdapter:
    """Adapts EWSIngest's calendar fetch to the SourceAdapter protocol (read-only events).

    Shares the EWS connection (same ``EWSIngest``/account as email) — calendar is just the
    EWS calendar folder. Tags messages ``source='calendar'``."""

    name = "calendar"

    def __init__(self, ingest: EWSIngest):
        self._ingest = ingest

    def fetch(self, digest_date: str) -> List[NormalizedMessage]:
        return self._ingest.fetch_events(digest_date, self._ingest.time_config)


def build_adapter(source: str, config, *, incremental: bool = False) -> SourceAdapter:
    """Build ONE source adapter from a Config (the InboxAPI's read-shaped path).

    EWS constructs its own ``EWSIngest``; Mattermost builds a non-stateful read adapter;
    calendar reuses an ``EWSIngest`` (same account, read-only). Raises ``ValueError`` for an
    unknown source. run.py keeps its own builder — it owns the strict/lenient split, the live
    ProgressSink, and the per-source watermark."""
    canonical = canonical_source(source)
    if canonical == "ews":
        return EWSSourceAdapter(EWSIngest(config.ews, config.time))
    if canonical == "calendar":
        return CalendarSourceAdapter(EWSIngest(config.ews, config.time))
    if canonical == "mm":
        from digest_core.ingest.mattermost import MattermostSourceAdapter

        return MattermostSourceAdapter(config.mm_source, config.time, incremental=incremental)
    raise ValueError(f"unknown source {source!r} (ews | calendar | mm)")


def run_sources(
    adapters: List[SourceAdapter], digest_date: str, *, strict: bool = False
) -> List[Envelope]:
    """Fetch from every adapter and return source-tagged envelopes.

    Default (``strict=False``) is degrade-not-drop at the source boundary: a
    failing source is logged and skipped so one source being down never crashes
    a multi-source run.

    ``strict=True`` re-raises the original exception instead of swallowing it.
    This is what the single live EWS source uses today, so the run's
    degradation policy (config-error -> crash, operational-error -> degrade)
    still sees the real exception exactly as it did before the seam was wired.
    """
    envelopes: List[Envelope] = []
    for adapter in adapters:
        name = getattr(adapter, "name", adapter.__class__.__name__)
        try:
            messages = adapter.fetch(digest_date)
        except Exception as exc:
            if strict:
                raise
            logger.error("Source adapter failed; skipping", source=name, error=str(exc))
            continue
        envelopes.extend(envelopes_from_messages(name, messages))
    return envelopes
