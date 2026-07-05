"""The daemon status file: last/next run, per-source counts, corp reachability.

A single small JSON at ``<data home>/var/state/daemon.json``, written atomically by each
tick and read by the CLI, the launcher menu, and the MCP ``daemon_status`` tool. Kept
schema-loose (``load`` returns whatever is there) so an older/newer file never crashes a
reader; ``summarize`` annotates it for display (staleness + installed state). No secrets
and no message bodies ever land here — counts and timestamps only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from digest_core import paths
from digest_core.mcp.jsonfile import atomic_write_json, read_json_or_empty


def status_path() -> Path:
    """The daemon status JSON path (``<data home>/var/state/daemon.json``)."""
    return paths.state_dir() / "daemon.json"


def load() -> Optional[Dict[str, Any]]:
    """The last-written status dict, or ``None`` when absent/unparseable."""
    doc, malformed = read_json_or_empty(status_path())
    if malformed or not doc:
        return None
    return doc


def save(record: Dict[str, Any]) -> None:
    """Persist ``record`` atomically (temp + rename)."""
    atomic_write_json(status_path(), record)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def staleness_seconds(record: Dict[str, Any], *, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since the last recorded tick, or ``None`` if it never ran."""
    last = _parse(record.get("last_run"))
    if last is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - last).total_seconds())


def summarize(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Load the status and annotate it for display.

    Adds ``installed`` (is the LaunchAgent present), ``staleness_seconds`` /
    ``staleness_days``, and ``is_stale`` (no successful tick for > 2 intervals, or > 2h
    when the interval is unknown). Never raises — a missing file yields a sensible
    "never run / not installed" shape.
    """
    from digest_core.daemon import launchd  # lazy: keep import-time deps minimal

    record = load() or {}
    stale = staleness_seconds(record, now=now)
    interval_min = record.get("interval_minutes")
    out: Dict[str, Any] = dict(record)
    out["installed"] = launchd.is_installed()
    out["staleness_seconds"] = stale
    out["staleness_days"] = round(stale / 86400, 2) if stale is not None else None
    if stale is None:
        out["is_stale"] = None
    elif interval_min:
        out["is_stale"] = stale > interval_min * 60 * 2
    else:
        out["is_stale"] = stale > 2 * 3600
    return out
