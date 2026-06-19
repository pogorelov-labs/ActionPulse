"""Harvest Mattermost reactions on delivered digest posts → ✓/✗ on evidence ids.

The EP-15 calibration signal: a ✅ on a delivered post is an implicit "this item was
real/useful", a ❌ is "this was wrong/noise". Folding those onto the post's
``evidence_ids`` (via the delivered-posts ledger) yields per-evidence ack/nack counts
that `eval-gold` / `eval-calibrate` can turn into a real ``recall_floor``.

The actual GET runs against the corp network (ADR-012); the logic here is offline-pure
and testable with a fake client exposing ``get_post_reactions(post_id) -> list[dict]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List

import structlog

from digest_core.feedback.delivered_ledger import DeliveredPost

logger = structlog.get_logger(__name__)

#: Mattermost ``emoji_name`` values (no surrounding colons). Configurable per call.
DEFAULT_ACK_EMOJIS: FrozenSet[str] = frozenset(
    {"white_check_mark", "heavy_check_mark", "+1", "thumbsup", "ok_hand", "100"}
)
DEFAULT_NACK_EMOJIS: FrozenSet[str] = frozenset(
    {"x", "-1", "thumbsdown", "no_entry", "no_entry_sign", "negative_squared_cross_mark"}
)


@dataclass
class ReactionRecord:
    post_id: str
    evidence_ids: List[str]
    emoji: str
    user_id: str
    signal: str  # 'ack' | 'nack' | 'other'


def classify(
    emoji: str,
    ack_emojis: FrozenSet[str] = DEFAULT_ACK_EMOJIS,
    nack_emojis: FrozenSet[str] = DEFAULT_NACK_EMOJIS,
) -> str:
    e = (emoji or "").strip().strip(":").lower()
    if e in ack_emojis:
        return "ack"
    if e in nack_emojis:
        return "nack"
    return "other"


def harvest_reactions(
    client: Any,
    entries: Iterable[DeliveredPost],
    *,
    ack_emojis: FrozenSet[str] = DEFAULT_ACK_EMOJIS,
    nack_emojis: FrozenSet[str] = DEFAULT_NACK_EMOJIS,
) -> List[ReactionRecord]:
    """For each delivered post, fetch + classify its reactions.

    ``client.get_post_reactions(post_id)`` returns the MM reactions list
    (dicts with ``emoji_name`` / ``user_id``). A per-post failure is logged and
    skipped — one bad post never aborts the harvest.
    """
    records: List[ReactionRecord] = []
    for entry in entries:
        try:
            reactions = client.get_post_reactions(entry.post_id) or []
        except Exception as exc:  # noqa: BLE001 - one bad post must not abort the sweep
            logger.warning("reaction_harvest_post_failed", post_id=entry.post_id, error=str(exc))
            continue
        for reaction in reactions:
            emoji = str(reaction.get("emoji_name", "")).strip()
            records.append(
                ReactionRecord(
                    post_id=entry.post_id,
                    evidence_ids=list(entry.evidence_ids),
                    emoji=emoji,
                    user_id=str(reaction.get("user_id", "")),
                    signal=classify(emoji, ack_emojis, nack_emojis),
                )
            )
    return records


def summarize(records: Iterable[ReactionRecord]) -> Dict[str, Any]:
    """Fold reactions onto evidence ids → per-evidence ack/nack counts + totals."""
    by_evidence: Dict[str, Dict[str, int]] = {}
    totals = {"ack": 0, "nack": 0, "other": 0}
    count = 0
    for rec in records:
        count += 1
        totals[rec.signal] = totals.get(rec.signal, 0) + 1
        if rec.signal == "other":
            continue
        for eid in rec.evidence_ids:
            slot = by_evidence.setdefault(eid, {"ack": 0, "nack": 0})
            slot[rec.signal] += 1
    return {"reactions": count, "totals": totals, "by_evidence": by_evidence}
