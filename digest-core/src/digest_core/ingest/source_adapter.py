"""Source adapter protocol + resilient multi-source ingest (PR12b).

``run_sources`` runs each adapter behind its own try/except so one source being
down never crashes the run (degrade-not-drop at the source boundary). EWS is
wrapped as the single live adapter; the seam is ready for more sources without
building them.
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

import structlog

from digest_core.ingest.envelope import Envelope, envelopes_from_messages
from digest_core.ingest.ews import EWSIngest, NormalizedMessage

logger = structlog.get_logger()


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
