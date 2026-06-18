"""Housekeeping over the data home (roadmap U6).

Pure, UI-free helpers behind the menu's Maintenance screen and the
`actionpulse clean` command: disk-usage read-out, log/digest cleanup, and the
file-logging toggle. Cleaning only ever touches **regenerable** files
(``var/logs``, ``var/out``, the legacy log dirs) — never state (the EWS
watermark and dedup ledger change fetch behavior), never secrets or config.

Telemetry honesty: there is no phone-home telemetry to turn off. Logs and the
Prometheus/healthz ports are local; OTel tracing is off by default. The
maintenance screen states this instead of pretending to disable something.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from digest_core import paths

#: Pre-U5 log locations — still listed and cleanable so an upgrade actually
#: frees the space the owner asked about.
LEGACY_LOG_DIRS = (Path.home() / ".digest-logs", Path("/tmp/digest-logs"))

#: Default retention for "clean old digests" (menu + `clean --digests`).
#: Kept as a stable constant for existing callers; the *bare* ``clean_digests()``
#: default now reads ``retention.keep_days`` from config (see ``_USE_CONFIG``).
DEFAULT_KEEP_DAYS = 14

#: Sentinel for ``clean_digests``: when no explicit ``older_than_days`` is given,
#: source the window from ``config.retention.keep_days`` instead of a hardcode.
_USE_CONFIG = object()

#: Artifact globs the retention sweep is allowed to delete in the out/ dir.
#: Verbatim subjects/senders/quotes (PDn) live in the digest pair; the trace
#: meta is payload-free but still run-identifying — all three are time-pruned.
RETENTION_GLOBS = ("digest-*.json", "digest-*.md", "trace-*.meta.json")

#: Path to the user config the logging toggle rewrites (wizard-generated,
#: comment-free yaml — safe to round-trip). Module-level so tests monkeypatch.
CONFIG_USER = paths.PROJECT_ROOT / "configs" / "config.yaml"


@dataclass
class UsageEntry:
    key: str
    label: str
    path: Path
    files: int
    size_bytes: int


def _dir_usage(path: Path) -> Tuple[int, int]:
    files = 0
    size = 0
    if not path.exists():
        return 0, 0
    for child in path.rglob("*"):
        if child.is_file():
            files += 1
            try:
                size += child.stat().st_size
            except OSError:
                continue
    return files, size


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover - unreachable


def collect_usage() -> List[UsageEntry]:
    """Disk usage of everything maintenance can see (legacy dirs only when present)."""
    entries = [
        UsageEntry("digests", "Digests", paths.out_dir(create=False), *(0, 0)),
        UsageEntry("logs", "Logs", paths.logs_dir(create=False), *(0, 0)),
        UsageEntry("state", "State (never auto-cleaned)", paths.state_dir(create=False), *(0, 0)),
        UsageEntry(
            "store",
            "Message store (encrypted; never auto-cleaned)",
            paths.store_dir(create=False),
            *(0, 0),
        ),
    ]
    for legacy in LEGACY_LOG_DIRS:
        if legacy.exists():
            entries.append(UsageEntry("legacy_logs", "Legacy logs", legacy, 0, 0))
    return [UsageEntry(e.key, e.label, e.path, *_dir_usage(e.path)) for e in entries]


def _remove_files(files: List[Path]) -> Tuple[int, int]:
    removed = 0
    freed = 0
    for path in files:
        try:
            size = path.stat().st_size
            path.unlink()
            removed += 1
            freed += size
        except OSError:
            continue
    return removed, freed


def clean_logs() -> Tuple[int, int]:
    """Delete run logs from the data home and the legacy locations."""
    targets: List[Path] = []
    for root in (paths.logs_dir(create=False), *LEGACY_LOG_DIRS):
        if root.exists():
            targets.extend(p for p in root.rglob("*") if p.is_file())
    return _remove_files(targets)


def _digest_date(path: Path) -> Optional[datetime]:
    """The YYYY-MM-DD encoded in digest artifact names, if any."""
    stem = path.name
    if stem.startswith("digest-"):
        try:
            return datetime.strptime(stem[7:17], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def clean_digests(older_than_days: Optional[int] = _USE_CONFIG) -> Tuple[int, int]:
    """Delete digest artifacts (json/md/idem sidecars/trace meta).

    ``older_than_days=N`` keeps the last N days (digest files by the date in
    their name, trace meta by mtime); ``None`` removes everything in var/out.
    With no argument the window is sourced from ``config.retention.keep_days``
    (was a hardcoded 14) so there is one documented retention number; existing
    callers that pass an explicit value (incl. ``DEFAULT_KEEP_DAYS``) are
    unchanged.
    """
    if older_than_days is _USE_CONFIG:
        older_than_days = _config_keep_days()
    out = paths.out_dir(create=False)
    if not out.exists():
        return 0, 0
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=older_than_days)
        if older_than_days is not None
        else None
    )
    targets: List[Path] = []
    for path in out.iterdir():
        if not path.is_file():
            continue
        if cutoff is None:
            targets.append(path)
            continue
        stamp = _digest_date(path)
        if stamp is None:
            try:
                stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
        if stamp < cutoff:
            targets.append(path)
    return _remove_files(targets)


def _config_keep_days() -> int:
    """``retention.keep_days`` from config, falling back to DEFAULT_KEEP_DAYS."""
    from digest_core.config import Config

    try:
        return int(Config().retention.keep_days)
    except Exception:  # noqa: BLE001 - a broken config must not break maintenance
        return DEFAULT_KEEP_DAYS


def prune_artifacts(config) -> dict:
    """Time-prune on-disk digest artifacts by mtime (retention enforcement).

    Deletes files in the resolved out/ dir (``paths.out_dir``) matching the
    three :data:`RETENTION_GLOBS` (``digest-*.json``, ``digest-*.md``,
    ``trace-*.meta.json``) whose mtime is older than
    ``now - config.retention.keep_days days``. Returns per-glob deletion counts
    plus a total: ``{"digest_json": N, "digest_md": N, "trace_meta": N,
    "total": N, "keep_days": K}``.

    Safety rails:
      * Only the three globs above, only inside the resolved out/ dir — never
        arbitrary paths, never a recursive walk outside out/ (``iterdir`` only,
        glob anchored to ``out``).
      * ``.state/`` operational files (``ews.syncstate``, ``last_run.json``,
        the dedup ledger) live in a different directory and are never globbed.
      * ``keep_days < 1`` is a no-op: nothing is deleted. With mtime-based
        cutoff the current run's just-written files (mtime ~ now) are protected
        inherently; the floor additionally guards against a misconfigured 0 that
        would wipe the freshly written run.
    """
    keep_days = int(config.retention.keep_days)
    counts = {"digest_json": 0, "digest_md": 0, "trace_meta": 0, "total": 0, "keep_days": keep_days}
    if keep_days < 1:
        return counts  # safety rail: never prune the current run

    out = paths.out_dir(create=False)
    if not out.exists():
        return counts

    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    glob_keys = {
        "digest-*.json": "digest_json",
        "digest-*.md": "digest_md",
        "trace-*.meta.json": "trace_meta",
    }
    for pattern in RETENTION_GLOBS:
        for path in out.glob(pattern):
            if not path.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            counts[glob_keys[pattern]] += 1
            counts["total"] += 1
    return counts


def file_logging_enabled() -> bool:
    """Current value of observability.log_to_file (defaults make this True)."""
    from digest_core.config import Config

    try:
        return bool(Config().observability.log_to_file)
    except Exception:  # noqa: BLE001 - a broken config must not break the menu
        return True


def set_file_logging(enabled: bool) -> bool:
    """Persist observability.log_to_file into the user config; returns the new state."""
    data = {}
    if CONFIG_USER.exists():
        data = yaml.safe_load(CONFIG_USER.read_text(encoding="utf-8")) or {}
    observability = data.get("observability") or {}
    observability["log_to_file"] = bool(enabled)
    data["observability"] = observability
    CONFIG_USER.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_USER.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return bool(enabled)


def toggle_file_logging() -> bool:
    """Flip the file-logging switch; returns the new state."""
    return set_file_logging(not file_logging_enabled())
