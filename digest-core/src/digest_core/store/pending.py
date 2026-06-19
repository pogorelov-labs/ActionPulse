"""Pending requests from the store — the content-aware sibling of carryover.

Where [[carryover]] is a pure metadata heuristic (a thread you were in went quiet),
this reads the stored BODY to find messages from an earlier day that actually asked
YOU something — a question, an approval, or a generic request — and that you have not
replied to since. The daily digest surfaces them as an "Awaiting your reply" section.

A message qualifies when ALL hold:
* it is from an EARLIER day (cross-day; today's asks are already in today's digest),
* you are in to/cc and it is NOT your own message,
* its subject/body matches a request/question/approval cue (bilingual EN+RU), and
* you have not sent any message in the thread SINCE it (you owe the reply).

Privacy: bodies are read only to classify the ask — the digest item carries metadata
only (subject, author, age, kind), never body text. DM threads ('D'/'G') are excluded
(redacted at rest, guardrail #9). Detection is deterministic and offline (no LLM call,
no gateway); stored embeddings are a documented future enhancement, not used here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from digest_core.store._rows import decode_json_list

_DAY = 86400

# Bilingual (EN + RU) speech-act cues, lowercased; RU entries are stems so a single
# token matches its inflections (e.g. "соглас" → согласовать/согласование/согласуйте).
# Order of classification: approval (most blocking) > question > generic request.
_APPROVAL_CUES = (
    "approve",
    "approval",
    "sign off",
    "sign-off",
    "signoff",
    "go-ahead",
    "go ahead",
    "ok to proceed",
    "соглас",  # согласовать / согласование / согласуйте
    "подтверд",  # подтвердите / подтверждение
    "утверд",  # утвердить / утверждение / на утверждение
    "акцепт",
    "ваше согласие",
)
_QUESTION_CUES = (
    "could you",
    "can you",
    "would you",
    "do you",
    "your thoughts",
    "let me know",
    "any update",
    "wdyt",
    "не могли бы вы",
    "можете ли вы",
    "подскажите",
    "ваше мнение",
    "что думаете",
    "как вы считаете",
    "уточните",
)
_REQUEST_CUES = (
    "please",
    "kindly",
    "need your",
    "awaiting your",
    "by eod",
    "by end of day",
    "follow up",
    "прошу",
    "пожалуйста",
    "просьба",
    "нужно ваше",
    "нужна ваша",
    "ждём от вас",
    "ждем вашего",
    "напоминаю",
)

_KIND_RANK = {"approval": 0, "question": 1, "request": 2}


@dataclass
class PendingRequest:
    thread_id: str
    subject: str
    author: str
    asked_msg_id: str
    asked_epoch: int
    age_days: int
    kind: str  # "approval" | "question" | "request"
    source: str


def classify_ask(subject: str, body: str) -> Optional[str]:
    """Speech act of a message directed at the owner, or ``None`` if it isn't an ask.

    Approval beats question beats request; a literal '?' in the body counts as a
    question. Substring match on lowercased ``subject`` + ``body``.
    """
    text = f"{subject}\n{body}".lower()
    if any(cue in text for cue in _APPROVAL_CUES):
        return "approval"
    if "?" in body or any(cue in text for cue in _QUESTION_CUES):
        return "question"
    if any(cue in text for cue in _REQUEST_CUES):
        return "request"
    return None


def _recipients(to_json: str, cc_json: str) -> List[str]:
    out: List[str] = []
    for raw in (to_json, cc_json):
        out.extend(decode_json_list(raw, lowercase=True, drop_empty=True))
    return out


def find_pending_requests(
    conn,
    *,
    user_aliases: Sequence[str],
    now: datetime,
    lookback_days: int = 7,
    max_items: int = 5,
) -> List[PendingRequest]:
    """Prior-day messages that asked the owner something and are still unanswered.

    ``now`` is the reference instant (typically the digest date). Returns at most
    ``max_items``, approvals first then most-overdue. Empty list when nothing qualifies.
    """
    aliases = {a.lower() for a in (user_aliases or []) if a}
    if not aliases:
        return []
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_epoch = int(now.timestamp())
    # Start of the reference day in *now's own* timezone (see find_open_loops): the digest
    # window is a user-local calendar day, so forcing UTC here would mis-place "today" by
    # the offset and double-surface an early-local-day ask that is "yesterday" in UTC.
    today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    lookback_start = today_start - lookback_days * _DAY

    # Your latest message epoch per thread → an ask is "unanswered" only if you have
    # not posted in its thread since (you still owe the reply).
    alias_list = sorted(aliases)
    placeholders = ",".join("?" * len(alias_list))
    your_last = {
        row[0]: int(row[1])
        for row in conn.execute(
            "SELECT thread_id, MAX(received_epoch) FROM messages "
            f"WHERE thread_id IS NOT NULL AND lower(author_email) IN ({placeholders}) "
            "GROUP BY thread_id",
            alias_list,
        ).fetchall()
    }

    candidates = conn.execute(
        "SELECT id, thread_id, subject, author_display, author_email, received_epoch, source, "
        "body_normalized, to_recipients, cc_recipients FROM messages "
        "WHERE received_epoch >= ? AND received_epoch < ? "
        "AND NOT (source = 'mm' AND mm_channel_type IN ('D','G')) "
        "AND thread_id IS NOT NULL "
        "ORDER BY received_epoch DESC",
        (lookback_start, today_start),
    ).fetchall()

    by_thread: dict = {}
    for row in candidates:
        mid, tid, subject, author_disp, author_email, recv, source, body, to_j, cc_j = row
        if tid in by_thread:
            continue  # newest qualifying ask per thread (candidates are newest-first)
        if (author_email or "").lower() in aliases:
            continue  # your own message is not an ask directed at you
        if not aliases.intersection(_recipients(to_j, cc_j)):
            continue  # not addressed to you
        if int(recv) <= your_last.get(tid, 0):
            continue  # you posted in this thread at/after the ask → answered
        kind = classify_ask(subject or "", body or "")
        if not kind:
            continue
        by_thread[tid] = PendingRequest(
            thread_id=tid,
            subject=subject or "",
            author=author_disp or author_email or "",
            asked_msg_id=mid,
            asked_epoch=int(recv),
            age_days=max(0, (now_epoch - int(recv)) // _DAY),
            kind=kind,
            source=source,
        )

    results = sorted(by_thread.values(), key=lambda p: (_KIND_RANK.get(p.kind, 3), -p.age_days))
    return results[:max_items]
