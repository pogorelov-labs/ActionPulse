"""Per-source incremental high-water marks.

BR principle (``docs/planning/BUSINESS_REQUIREMENTS.md`` §0): *"Идемпотентность и
инкременты: per-source high-water marks + дедуп."* A tiny ISO-8601 timestamp file
per source under the state dir records the latest message timestamp successfully
ingested. The next incremental run starts from that mark minus a small **overlap
window** — so a message that landed during the previous run's in-flight fetch is
re-read rather than skipped — and pipeline-level + store-level dedup absorb the
harmless re-reads.

This generalizes the EWS-only watermark (previously private to ``EWSIngest``) so
every source — EWS, Mattermost, and future sources — shares one mechanism,
independent of the optional encrypted message store (incremental load must work
with the store off).

Semantics chosen to preserve EWS's historical behavior while making it safer:

* ``effective_start`` returns ``watermark - overlap`` when a mark exists (catch up
  from the last seen message, with the re-read window), else the caller's
  ``window_start`` (a full-window fetch on first run / after a reset).
* ``advance`` persists the **max observed received-time**, NOT the window end — the
  old code stored the window end, which could skip a message that arrived between
  the last fetched item and "now". It is a **no-op on a quiet window**
  (``observed_max is None``) so the mark never ratchets past mail it never saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

#: Re-read window: on each incremental run, start ``overlap`` before the stored
#: mark so a message that arrived during the prior run's fetch is not skipped.
DEFAULT_OVERLAP = timedelta(minutes=10)


def _to_utc(dt: datetime) -> datetime:
    """Coerce to an aware UTC datetime (treat naive as already-UTC)."""
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class SourceWatermark:
    """A per-source high-water mark persisted as a single ISO-8601 line.

    ``filename`` overrides the default ``<state_dir>/<source>.watermark`` path —
    EWS uses it to keep reading/writing its historical ``ews.syncstate`` file so
    existing state survives the refactor.
    """

    state_dir: Path
    source: str
    overlap: timedelta = DEFAULT_OVERLAP
    filename: Optional[str] = None

    def path(self) -> Path:
        if self.filename:
            return Path(self.filename)
        return Path(self.state_dir) / f"{self.source}.watermark"

    def load(self) -> Optional[datetime]:
        """Return the stored mark as aware-UTC, or ``None`` if absent/unreadable.

        A missing or malformed file degrades to ``None`` (full fetch) rather than
        crashing — a corrupt watermark must never take down a run.
        """
        p = self.path()
        if not p.exists():
            return None
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning("watermark_load_failed", source=self.source, path=str(p), error=str(e))
            return None
        if not raw:
            return None
        try:
            return _to_utc(datetime.fromisoformat(raw))
        except ValueError as e:
            logger.warning(
                "watermark_invalid", source=self.source, path=str(p), value=raw, error=str(e)
            )
            return None

    def effective_start(self, window_start: datetime) -> datetime:
        """Lower bound for the next fetch.

        ``watermark - overlap`` when a mark exists (catch-up + re-read window),
        else ``window_start`` (no mark yet → the caller's full window).
        """
        wm = self.load()
        if wm is None:
            return window_start
        return wm - self.overlap

    def advance(self, observed_max: Optional[datetime]) -> None:
        """Persist the latest ingested timestamp (atomic write).

        No-op when ``observed_max is None`` (a quiet window) so the mark never
        moves past mail that was never observed. A write failure degrades to a
        warning — the run must not crash because state could not be saved.
        """
        if observed_max is None:
            return
        p = self.path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.parent / (p.name + ".tmp")
            tmp.write_text(_to_utc(observed_max).isoformat(), encoding="utf-8")
            tmp.replace(p)  # atomic rename on POSIX
        except OSError as e:
            logger.warning(
                "watermark_advance_failed", source=self.source, path=str(p), error=str(e)
            )
