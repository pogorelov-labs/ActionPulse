"""The delivered-posts ledger — the post_id ↔ evidence_id map EP-15 needs.

When a digest is delivered in ``auth_mode=api``, each part becomes a Mattermost post
with a captured ``post_id``. This ledger records, per delivered post, the digest's
evidence ids so a later reaction harvest can fold ✓/✗ onto ``evidence_id``.

Payload-free by construction: only ``post_id`` / ``channel_id`` / ``digest_date`` /
``evidence_ids`` (content-hash ids) / ``delivered_at`` are stored — never any message
body or subject. Lives next to the other state (``<state>/delivered-posts.jsonl``).

**Granularity caveat (scaffold):** delivery splits the rendered digest by message
length, NOT by item, so a post cannot today be mapped to a *specific* item — each
post entry carries the **whole digest's** evidence ids. Precise per-item mapping needs
per-section threaded delivery (ROADMAP §4 Phase 2 / MM_DESIGN §3); until then a
reaction credits every evidence id in the digest, which the calibration step must
account for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import structlog

logger = structlog.get_logger(__name__)

LEDGER_NAME = "delivered-posts.jsonl"


@dataclass
class DeliveredPost:
    post_id: str
    channel_id: str
    digest_date: str
    evidence_ids: List[str]
    delivered_at: str = ""
    extra: dict = field(default_factory=dict)


def ledger_path(state_dir: Path) -> Path:
    return Path(state_dir) / LEDGER_NAME


def record_delivery(
    state_dir: Path,
    *,
    post_ids: List[str],
    evidence_ids: List[str],
    channel_id: str = "",
    digest_date: str = "",
    now: Optional[datetime] = None,
) -> int:
    """Append one ledger entry per delivered ``post_id``. Returns entries written.

    Non-fatal: a write failure logs a warning and returns 0 — recording feedback
    metadata must never break a delivered run.
    """
    if not post_ids:
        return 0
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    ev = [e for e in (evidence_ids or []) if e and e != "system"]
    path = ledger_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for post_id in post_ids:
                if not post_id:
                    continue
                handle.write(
                    json.dumps(
                        {
                            "post_id": post_id,
                            "channel_id": channel_id,
                            "digest_date": digest_date,
                            "evidence_ids": ev,
                            "delivered_at": when,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        return len(post_ids)
    except OSError as exc:
        logger.warning("delivered_ledger_write_failed", path=str(path), error=str(exc))
        return 0


def read_ledger(state_dir: Path, *, digest_date: Optional[str] = None) -> List[DeliveredPost]:
    """Read the ledger (optionally filtered to one ``digest_date``). Bad lines skipped."""
    path = ledger_path(state_dir)
    if not path.exists():
        return []
    out: List[DeliveredPost] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if digest_date and row.get("digest_date") != digest_date:
            continue
        out.append(
            DeliveredPost(
                post_id=str(row.get("post_id", "")),
                channel_id=str(row.get("channel_id", "")),
                digest_date=str(row.get("digest_date", "")),
                evidence_ids=list(row.get("evidence_ids", []) or []),
                delivered_at=str(row.get("delivered_at", "")),
            )
        )
    return out
