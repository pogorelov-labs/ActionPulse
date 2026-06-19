"""Safe JSON read-modify-write for third-party CLI config files.

Defensive by design: never overwrite a file we couldn't parse, back up byte-exact
before any write, write atomically (temp + rename, the ``ingest/watermark.py`` idiom),
and preserve every key we don't own.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def read_json_or_empty(path: Path) -> Tuple[Dict[str, Any], bool]:
    """Return ``(doc, malformed)``. Missing/empty → ``({}, False)``; unparseable or
    non-object JSON → ``({}, True)`` so the caller refuses to clobber it."""
    if not path.exists():
        return {}, False
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}, False
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return {}, True
    if not isinstance(doc, dict):
        return {}, True
    return doc, False


def backup(path: Path, *, now: Optional[datetime] = None) -> Optional[Path]:
    """Copy an existing file to ``<name>.<UTC-stamp>.bak`` (byte-exact). No-op if absent."""
    if not path.exists():
        return None
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    bak = path.with_name(f"{path.name}.{stamp}.bak")
    bak.write_bytes(path.read_bytes())
    return bak


def atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    """Serialize ``doc`` to ``path`` atomically (temp file + rename), creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
