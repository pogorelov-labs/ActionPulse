"""Idempotent upsert of messages into the store + summary stats.

Keyed by the URN id. ``content_hash`` distinguishes a changed message from an
unchanged re-read (the overlap-window re-reads from the per-source watermark land
here as no-ops). ``first_seen_at`` is write-once; ``last_seen_at`` advances on
every sighting; content columns + ``ingested_at`` advance only on a real change.

Chunk + embedding population lives in the search PR; this module persists the
messages and lets the FTS index stay in sync via the schema triggers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from digest_core.store.models import message_to_row
from digest_core.store.schema import CURRENT_SCHEMA_VERSION

_COLUMNS = (
    "id, source, canonical_url, thread_id, parent_id, received_at, received_epoch, "
    "author_display, author_email, author_role, subject, body_raw, body_normalized, "
    "content_hash, lang, importance, is_flagged, has_attachments, attachment_types, "
    "to_recipients, cc_recipients, risk_level, pipeline_version, schema_version, "
    "first_seen_at, last_seen_at, ingested_at"
)
_VALUES = (
    ":id, :source, :canonical_url, :thread_id, :parent_id, :received_at, :received_epoch, "
    ":author_display, :author_email, :author_role, :subject, :body_raw, :body_normalized, "
    ":content_hash, :lang, :importance, :is_flagged, :has_attachments, :attachment_types, "
    ":to_recipients, :cc_recipients, :risk_level, :pipeline_version, :schema_version, "
    ":now, :now, :now"
)
_INSERT_SQL = f"INSERT INTO messages ({_COLUMNS}) VALUES ({_VALUES})"

# Content-changed update: every content column + last_seen + ingested advance;
# first_seen is deliberately untouched (write-once).
_UPDATE_SQL = """
UPDATE messages SET
    source = :source, canonical_url = :canonical_url, thread_id = :thread_id,
    parent_id = :parent_id, received_at = :received_at, received_epoch = :received_epoch,
    author_display = :author_display, author_email = :author_email, author_role = :author_role,
    subject = :subject, body_raw = :body_raw, body_normalized = :body_normalized,
    content_hash = :content_hash, lang = :lang, importance = :importance,
    is_flagged = :is_flagged, has_attachments = :has_attachments,
    attachment_types = :attachment_types, to_recipients = :to_recipients,
    cc_recipients = :cc_recipients, risk_level = :risk_level,
    pipeline_version = :pipeline_version, last_seen_at = :now, ingested_at = :now
WHERE id = :id
"""

_TOUCH_SQL = "UPDATE messages SET last_seen_at = :now WHERE id = :id"


def _now_iso(now: Optional[datetime]) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat()


def upsert_messages(
    conn,
    messages: Iterable[Any],
    *,
    raw_by_id: Optional[Dict[str, str]] = None,
    pipeline_version: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Upsert messages idempotently. Returns ``{inserted, updated, unchanged, total}``."""
    raw = raw_by_id or {}
    now_iso = _now_iso(now)
    inserted = updated = unchanged = 0
    conn.execute("BEGIN")
    try:
        for msg in messages:
            row = message_to_row(
                msg,
                schema_version=CURRENT_SCHEMA_VERSION,
                pipeline_version=pipeline_version,
                raw_body=raw.get(getattr(msg, "msg_id", "")),
            )
            params = {**row, "now": now_iso}
            existing = conn.execute(
                "SELECT content_hash FROM messages WHERE id = :id", {"id": row["id"]}
            ).fetchone()
            if existing is None:
                conn.execute(_INSERT_SQL, params)
                inserted += 1
            elif existing[0] != row["content_hash"]:
                conn.execute(_UPDATE_SQL, params)
                updated += 1
            else:
                conn.execute(_TOUCH_SQL, {"id": row["id"], "now": now_iso})
                unchanged += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "total": inserted + updated + unchanged,
    }


def stats(conn) -> Dict[str, Any]:
    """Disk-free summary of the store contents."""
    total = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    by_source = {
        src: n
        for src, n in conn.execute(
            "SELECT source, count(*) FROM messages GROUP BY source ORDER BY source"
        ).fetchall()
    }
    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    embeddings = conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]
    oldest, newest = conn.execute(
        "SELECT min(received_at), max(received_at) FROM messages"
    ).fetchone()
    return {
        "messages": total,
        "by_source": by_source,
        "chunks": chunks,
        "embeddings": embeddings,
        "oldest": oldest,
        "newest": newest,
    }
