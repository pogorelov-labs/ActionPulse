"""Direct retrieval over the store — the read primitives the digest run never needed
but an API / MCP surface does: get a message, reconstruct a thread, list by sender /
date / recency, summarize threads, count by sender / day.

All functions here are OFFLINE (pure SQLite over the existing indexes
``idx_messages_{thread,epoch,source}``) and return focused dataclasses — NOT the
search-shaped ``SearchHit`` (which carries score/snippet/chunk_id and lacks body /
recipients / flags). A DM's ``body`` comes back as the at-rest redaction marker, never
colleague text (guardrail #9) — the row's metadata is kept, the body never was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from digest_core.store._rows import decode_json_list
from digest_core.store.search import _since_epoch  # shared YYYY-MM-DD -> epoch parse

_DAY = 86400

# Column order is fixed and shared by every record query + ``_row_to_record``.
_RECORD_COLS = (
    "id, source, thread_id, received_at, author_display, author_email, subject, "
    "body_normalized, importance, is_flagged, has_attachments, attachment_types, "
    "to_recipients, cc_recipients, mm_channel_type"
)


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    source: str
    thread_id: Optional[str]
    received_at: str
    author_display: str
    author_email: str
    subject: str
    body: str  # body_normalized; '' for redacted DM ('D'/'G')
    importance: str
    is_flagged: bool
    has_attachments: bool
    attachment_types: List[str]
    to_recipients: List[str]
    cc_recipients: List[str]
    mm_channel_type: Optional[str]


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    subject: str
    message_count: int
    last_received_at: str
    last_author: str
    source: str


@dataclass(frozen=True)
class SenderCount:
    email: str
    name: str
    count: int


@dataclass(frozen=True)
class DayCount:
    day: str  # 'YYYY-MM-DD' (UTC)
    count: int


def _json_list(raw) -> List[str]:
    return decode_json_list(raw)


def _row_to_record(row) -> MessageRecord:
    (
        mid,
        source,
        thread_id,
        received_at,
        author_display,
        author_email,
        subject,
        body,
        importance,
        is_flagged,
        has_attachments,
        attachment_types,
        to_recipients,
        cc_recipients,
        mm_channel_type,
    ) = row
    return MessageRecord(
        message_id=mid,
        source=source or "",
        thread_id=thread_id,
        received_at=received_at or "",
        author_display=author_display or "",
        author_email=author_email or "",
        subject=subject or "",
        body=body or "",
        importance=importance or "Normal",
        is_flagged=bool(is_flagged),
        has_attachments=bool(has_attachments),
        attachment_types=_json_list(attachment_types),
        to_recipients=_json_list(to_recipients),
        cc_recipients=_json_list(cc_recipients),
        mm_channel_type=mm_channel_type,
    )


def get_message(conn, message_id: str) -> Optional[MessageRecord]:
    """One message by URN id, or None."""
    row = conn.execute(
        f"SELECT {_RECORD_COLS} FROM messages WHERE id = ?", (message_id,)
    ).fetchone()
    return _row_to_record(row) if row else None


def get_thread(conn, thread_id: str, *, limit: int = 200) -> List[MessageRecord]:
    """All messages in a thread, oldest-first (chronological reconstruction)."""
    rows = conn.execute(
        f"SELECT {_RECORD_COLS} FROM messages WHERE thread_id = ? "
        "ORDER BY received_epoch ASC, id ASC LIMIT ?",
        (thread_id, limit),
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_recent(conn, *, limit: int = 50, source: Optional[str] = None) -> List[MessageRecord]:
    """Most recent messages first, optionally filtered by source."""
    if source:
        rows = conn.execute(
            f"SELECT {_RECORD_COLS} FROM messages WHERE source = ? "
            "ORDER BY received_epoch DESC LIMIT ?",
            (source, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_RECORD_COLS} FROM messages ORDER BY received_epoch DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_by_sender(conn, email: str, *, limit: int = 50) -> List[MessageRecord]:
    """Messages from a sender (case-insensitive author_email match), newest-first."""
    rows = conn.execute(
        f"SELECT {_RECORD_COLS} FROM messages WHERE lower(author_email) = lower(?) "
        "ORDER BY received_epoch DESC LIMIT ?",
        (email, limit),
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_by_date_range(
    conn, start: str, end: str, *, source: Optional[str] = None, limit: int = 200
) -> List[MessageRecord]:
    """Messages in [start, end] inclusive (whole UTC days, YYYY-MM-DD), oldest-first."""
    start_epoch = _since_epoch(start)
    end_epoch = _since_epoch(end)
    if start_epoch is None or end_epoch is None:
        return []
    end_epoch += _DAY  # make the end day inclusive
    clauses = ["received_epoch >= ?", "received_epoch < ?"]
    params: List = [start_epoch, end_epoch]
    if source:
        clauses.append("source = ?")
        params.append(source)
    params.append(limit)
    rows = conn.execute(
        f"SELECT {_RECORD_COLS} FROM messages WHERE {' AND '.join(clauses)} "
        "ORDER BY received_epoch ASC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_threads(conn, *, limit: int = 50, source: Optional[str] = None) -> List[ThreadSummary]:
    """Most-recently-active threads first, with count + latest subject/author."""
    where = "WHERE thread_id IS NOT NULL"
    params: List = []
    if source:
        where += " AND source = ?"
        params.append(source)
    grouped = conn.execute(
        f"SELECT thread_id, COUNT(*), MAX(received_epoch) FROM messages {where} "
        "GROUP BY thread_id ORDER BY MAX(received_epoch) DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    out: List[ThreadSummary] = []
    for thread_id, count, latest in grouped:
        row = conn.execute(
            "SELECT subject, author_display, author_email, source, received_at FROM messages "
            "WHERE thread_id = ? AND received_epoch = ? ORDER BY id LIMIT 1",
            (thread_id, latest),
        ).fetchone()
        subject, author_display, author_email, src, received_at = row or ("", "", "", "", "")
        out.append(
            ThreadSummary(
                thread_id=thread_id,
                subject=subject or "",
                message_count=int(count),
                last_received_at=received_at or "",
                last_author=author_display or author_email or "",
                source=src or "",
            )
        )
    return out


def count_by_sender(conn, *, limit: int = 20, since: Optional[str] = None) -> List[SenderCount]:
    """Top senders by message count (optionally since a YYYY-MM-DD date)."""
    clause = ""
    params: List = []
    since_epoch = _since_epoch(since)
    if since_epoch is not None:
        clause = "WHERE received_epoch >= ?"
        params.append(since_epoch)
    rows = conn.execute(
        "SELECT author_email, MAX(author_display), COUNT(*) c FROM messages "
        f"{clause} GROUP BY lower(author_email) ORDER BY c DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [
        SenderCount(email=email or "", name=name or "", count=int(count))
        for email, name, count in rows
    ]


def count_by_day(
    conn, *, days: int = 30, source: Optional[str] = None, now: Optional[datetime] = None
) -> List[DayCount]:
    """Message counts per UTC day over the last ``days`` days, oldest-first."""
    now = now or datetime.now(timezone.utc)
    cutoff = int(now.timestamp()) - days * _DAY
    clauses = ["received_epoch >= ?"]
    params: List = [cutoff]
    if source:
        clauses.append("source = ?")
        params.append(source)
    rows = conn.execute(
        "SELECT date(received_at) d, COUNT(*) FROM messages "
        f"WHERE {' AND '.join(clauses)} GROUP BY d ORDER BY d ASC",
        params,
    ).fetchall()
    return [DayCount(day=day, count=int(count)) for day, count in rows if day]
