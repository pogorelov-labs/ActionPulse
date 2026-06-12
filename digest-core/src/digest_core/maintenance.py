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
DEFAULT_KEEP_DAYS = 14

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


def clean_digests(older_than_days: Optional[int] = DEFAULT_KEEP_DAYS) -> Tuple[int, int]:
    """Delete digest artifacts (json/md/idem sidecars/trace meta).

    ``older_than_days=N`` keeps the last N days (digest files by the date in
    their name, trace meta by mtime); ``None`` removes everything in var/out.
    """
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
