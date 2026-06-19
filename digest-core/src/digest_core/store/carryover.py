"""Cross-day "open loops" from the store — the memory pillar feeding the digest.

Pure metadata query (no bodies, no LLM). A thread is an "open loop" when ALL hold:
* the owner was addressed (to/cc) in a message from an EARLIER day,
* the thread has since gone QUIET (no message for >= stale_days), and
* the latest message in the thread is NOT from the owner (the ball is in your court —
  if you already sent the last reply, it is waiting on them, not you).
The daily digest appends these as an "Open loops" section so the 30-day store actually
informs the digest, instead of being write-only.

Heuristic + privacy notes:
* "addressed to the owner" reuses the same alias-in-recipients match as the evidence
  splitter; it matches email recipients and any MM mention that carries the owner's
  email. (A pure MM-handle mention without the email won't match — acceptable for v1.)
* DM threads ('D'/'G') are excluded — their bodies are redacted at rest (guardrail #9)
  and they are consent-gated; only email + MM channel posts become open loops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Sequence

_DAY = 86400


@dataclass
class OpenLoop:
    thread_id: str
    subject: str
    author: str
    last_msg_id: str
    last_received_epoch: int
    age_days: int
    msg_count: int
    source: str


def _recipients(to_json: str, cc_json: str) -> List[str]:
    out: List[str] = []
    for raw in (to_json, cc_json):
        if not raw:
            continue
        try:
            vals = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(vals, list):
            out.extend(str(v).lower() for v in vals if v)
    return out


def find_open_loops(
    conn,
    *,
    user_aliases: Sequence[str],
    now: datetime,
    lookback_days: int = 7,
    stale_days: int = 2,
    max_items: int = 5,
) -> List[OpenLoop]:
    """Open loops: owner-addressed prior-day messages in threads now gone quiet.

    ``now`` is the reference instant (typically the digest date). Returns at most
    ``max_items``, most-stale first. Empty list when nothing qualifies.
    """
    aliases = {a.lower() for a in (user_aliases or []) if a}
    if not aliases:
        return []
    now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    now_epoch = int(now.timestamp())
    today_start = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp())
    lookback_start = today_start - lookback_days * _DAY
    stale_cutoff = now_epoch - stale_days * _DAY

    # Per-thread activity (over ALL messages): count, latest-message epoch, and the
    # AUTHOR of that latest message — so we can tell (a) if the thread has gone quiet
    # and (b) whether the ball is in your court (latest message is NOT from you).
    thread_info: dict = {}  # thread_id -> (count, latest_epoch, latest_author_lower)
    for tid, cnt, mx, latest_author in conn.execute(
        "SELECT t.thread_id, t.cnt, t.mx, m.author_email FROM "
        "(SELECT thread_id, COUNT(*) cnt, MAX(received_epoch) mx FROM messages "
        " WHERE thread_id IS NOT NULL GROUP BY thread_id) t "
        "JOIN messages m ON m.thread_id = t.thread_id AND m.received_epoch = t.mx"
    ).fetchall():
        if tid not in thread_info:  # ties at the max epoch → keep the first row
            thread_info[tid] = (int(cnt), int(mx), (latest_author or "").lower())

    # Candidate addressed messages from PRIOR days (not today), excluding DMs.
    candidates = conn.execute(
        "SELECT id, thread_id, subject, author_display, author_email, received_epoch, source, "
        "to_recipients, cc_recipients FROM messages "
        "WHERE received_epoch >= ? AND received_epoch < ? "
        "AND NOT (source = 'mm' AND mm_channel_type IN ('D','G')) "
        "AND thread_id IS NOT NULL "
        "ORDER BY received_epoch DESC",
        (lookback_start, today_start),
    ).fetchall()

    # Keep the latest owner-addressed message per thread; qualify if the thread is stale.
    rep_by_thread: dict = {}
    for row in candidates:
        mid, tid, subject, author_disp, author_email, recv, source, to_j, cc_j = row
        if tid in rep_by_thread:
            continue  # candidates are newest-first → first seen is the latest
        if not aliases.intersection(_recipients(to_j, cc_j)):
            continue
        rep_by_thread[tid] = (mid, subject, author_disp or author_email or "", source)

    loops: List[OpenLoop] = []
    for tid, (mid, subject, author, source) in rep_by_thread.items():
        count, latest, latest_author = thread_info.get(tid, (0, 0, ""))
        if not latest or latest > stale_cutoff:
            continue  # thread still active (or today) → not an open loop
        if latest_author in aliases:
            continue  # you sent the last message → ball is in their court, not yours
        loops.append(
            OpenLoop(
                thread_id=tid,
                subject=subject or "",
                author=author,
                last_msg_id=mid,
                last_received_epoch=latest,
                age_days=max(0, (now_epoch - latest) // _DAY),
                msg_count=count,
                source=source,
            )
        )

    loops.sort(key=lambda loop: loop.age_days, reverse=True)
    return loops[:max_items]
