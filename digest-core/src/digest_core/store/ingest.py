"""Idempotent upsert of messages into the store + summary stats.

Keyed by the URN id. ``content_hash`` distinguishes a changed message from an
unchanged re-read (the overlap-window re-reads from the per-source watermark land
here as no-ops). ``first_seen_at`` is write-once; ``last_seen_at`` advances on
every sighting; content columns + ``ingested_at`` advance only on a real change.

On a content change a message's chunks are recreated (cascading away its stale
embeddings); the FTS index stays in sync via the schema triggers. ``embed_backlog``
fills vectors for chunks that don't have one yet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from digest_core.store.chunking import chunk_id, chunk_text
from digest_core.store.models import is_dm, message_to_row
from digest_core.store.schema import CURRENT_SCHEMA_VERSION

_COLUMNS = (
    "id, source, canonical_url, thread_id, parent_id, mm_channel_type, received_at, "
    "received_epoch, author_display, author_email, author_role, subject, body_raw, "
    "body_normalized, content_hash, lang, importance, is_flagged, has_attachments, "
    "attachment_types, to_recipients, cc_recipients, risk_level, pipeline_version, "
    "schema_version, first_seen_at, last_seen_at, ingested_at"
)
_VALUES = (
    ":id, :source, :canonical_url, :thread_id, :parent_id, :mm_channel_type, :received_at, "
    ":received_epoch, :author_display, :author_email, :author_role, :subject, :body_raw, "
    ":body_normalized, :content_hash, :lang, :importance, :is_flagged, :has_attachments, "
    ":attachment_types, :to_recipients, :cc_recipients, :risk_level, :pipeline_version, "
    ":schema_version, :now, :now, :now"
)
_INSERT_SQL = f"INSERT INTO messages ({_COLUMNS}) VALUES ({_VALUES})"

# Content-changed update: every content column + last_seen + ingested advance;
# first_seen is deliberately untouched (write-once).
_UPDATE_SQL = """
UPDATE messages SET
    source = :source, canonical_url = :canonical_url, thread_id = :thread_id,
    parent_id = :parent_id, mm_channel_type = :mm_channel_type,
    received_at = :received_at, received_epoch = :received_epoch,
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


_INSERT_CHUNK_SQL = (
    "INSERT INTO chunks (chunk_id, message_id, chunk_index, text, token_count, "
    "char_start, char_end) VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def _now_iso(now: Optional[datetime]) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat()


def _replace_chunks(conn, urn: str, body_normalized: str) -> None:
    """Recreate a message's chunks (delete cascades its old embeddings).

    Called only when the message is new or its content changed, so an unchanged
    re-ingest never re-chunks or invalidates existing embeddings.
    """
    conn.execute("DELETE FROM chunks WHERE message_id = ?", (urn,))
    for idx, ch in enumerate(chunk_text(body_normalized or "")):
        conn.execute(
            _INSERT_CHUNK_SQL,
            (
                chunk_id(urn, idx, ch.text),
                urn,
                idx,
                ch.text,
                ch.token_count,
                ch.char_start,
                ch.char_end,
            ),
        )


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
            # DM bodies are redacted at rest (guardrail #9) → no real content to
            # chunk/embed/search; persist the row but skip chunk creation.
            dm = is_dm(row["source"], row.get("mm_channel_type"))
            existing = conn.execute(
                "SELECT content_hash FROM messages WHERE id = :id", {"id": row["id"]}
            ).fetchone()
            if existing is None:
                conn.execute(_INSERT_SQL, params)
                if not dm:
                    _replace_chunks(conn, row["id"], row["body_normalized"])
                inserted += 1
            elif existing[0] != row["content_hash"]:
                conn.execute(_UPDATE_SQL, params)
                if not dm:
                    _replace_chunks(conn, row["id"], row["body_normalized"])
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


_INSERT_EMB_SQL = (
    "INSERT INTO embeddings (chunk_id, model, dim, vector, embedded_at) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(chunk_id) DO UPDATE SET "
    "model=excluded.model, dim=excluded.dim, vector=excluded.vector, embedded_at=excluded.embedded_at"
)


def embed_backlog(
    conn,
    backend: Any,
    *,
    model: str = "bge-m3",
    dtype: str = "float32",
    batch_size: int = 128,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Embed chunks that have no vector yet, in batched ``backend.embed`` calls.

    ``backend`` is any object with ``embed(texts) -> list[list[float]]`` (the
    gateway ``EmbeddingsClient`` satisfies it as-is). Vectors are stored as
    little-endian ``float32``/``float16`` BLOBs. Returns ``{embedded, pending}``.
    """
    import numpy as np

    np_dtype = np.float16 if dtype == "float16" else np.float32
    now_iso = _now_iso(now)
    rows: List[Any] = conn.execute(
        "SELECT c.chunk_id, c.text FROM chunks c "
        "LEFT JOIN embeddings e ON e.chunk_id = c.chunk_id "
        "WHERE e.chunk_id IS NULL"
    ).fetchall()
    embedded = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = backend.embed([text for _cid, text in batch])
        conn.execute("BEGIN")
        try:
            for (cid, _text), vec in zip(batch, vectors):
                arr = np.asarray(vec, dtype=np_dtype)
                conn.execute(
                    _INSERT_EMB_SQL, (cid, model, int(arr.shape[0]), arr.tobytes(), now_iso)
                )
                embedded += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"embedded": embedded, "pending": 0}


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
