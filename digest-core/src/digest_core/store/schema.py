"""DDL for the encrypted message store (single source of truth).

Five tables converging toward the BR "Единая схема данных v3.0":

* ``messages``    — one row per fetched message, keyed by a stable URN id; carries
                    both ``body_raw`` and ``body_normalized`` plus all current
                    ``NormalizedMessage`` fields and nullable v3.0 seams
                    (canonical_url, parent_id, author_role, lang, risk_level).
* ``messages_fts``— FTS5 external-content index over subject + normalized body,
                    kept in sync by triggers (Cyrillic + Latin tokenizer).
* ``chunks``      — message split into embeddable units with char offsets
                    (populated in the search PR; table exists so TTL cascades).
* ``embeddings``  — one dense vector BLOB per chunk (populated in the search PR).
* ``meta``        — key/value, incl. ``schema_version`` for forward migrations.

``messages`` keeps its implicit rowid (NOT ``WITHOUT ROWID``) because FTS5
external-content joins on ``rowid``.
"""

from __future__ import annotations

CURRENT_SCHEMA_VERSION = 1

_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,          -- urn:email:<msgId> | urn:mm:<postId>
    source           TEXT NOT NULL,             -- 'email' | 'mm'
    canonical_url    TEXT,                       -- v3.0 seam
    thread_id        TEXT,                       -- = NormalizedMessage.conversation_id
    parent_id        TEXT,                       -- v3.0 seam (mm reply parent)
    mm_channel_type  TEXT,                       -- mm: 'O'/'P' channel, 'D'/'G' DM
    received_at      TEXT NOT NULL,             -- UTC ISO-8601
    received_epoch   INTEGER NOT NULL,          -- int epoch seconds (range/TTL)
    author_display   TEXT,
    author_email     TEXT,
    author_role      TEXT,                       -- v3.0 seam
    subject          TEXT,
    body_raw         TEXT,                       -- as ingested (HTML/markdown)
    body_normalized  TEXT,                       -- cleaned text (FTS + chunk source)
    content_hash     TEXT NOT NULL,             -- sha256(id|subject|body_normalized)
    lang             TEXT,                       -- v3.0 seam
    importance       TEXT NOT NULL DEFAULT 'Normal',
    is_flagged       INTEGER NOT NULL DEFAULT 0,
    has_attachments  INTEGER NOT NULL DEFAULT 0,
    attachment_types TEXT,                       -- JSON array
    to_recipients    TEXT,                       -- JSON array
    cc_recipients    TEXT,                       -- JSON array
    risk_level       TEXT,                       -- v3.0 seam
    pipeline_version TEXT,
    schema_version   INTEGER NOT NULL,
    first_seen_at    TEXT NOT NULL,             -- write-once
    last_seen_at     TEXT NOT NULL,             -- advances every sighting
    ingested_at      TEXT NOT NULL              -- last content-changing write
);
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);",
    "CREATE INDEX IF NOT EXISTS idx_messages_epoch ON messages(received_epoch);",
    "CREATE INDEX IF NOT EXISTS idx_messages_source ON messages(source);",
    "CREATE INDEX IF NOT EXISTS idx_messages_hash ON messages(content_hash);",
)

_MESSAGES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    subject,
    body_normalized,
    content='messages',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
"""

# External-content FTS5 contract: mirror every messages mutation into the index.
_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, subject, body_normalized)
        VALUES (new.rowid, new.subject, new.body_normalized);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, subject, body_normalized)
        VALUES ('delete', old.rowid, old.subject, old.body_normalized);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, subject, body_normalized)
        VALUES ('delete', old.rowid, old.subject, old.body_normalized);
        INSERT INTO messages_fts(rowid, subject, body_normalized)
        VALUES (new.rowid, new.subject, new.body_normalized);
    END;
    """,
)

_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,                -- "ch_"+sha256(urn|idx|text)[:16]
    message_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    char_start  INTEGER NOT NULL,                -- offset into body_normalized
    char_end    INTEGER NOT NULL,                -- exclusive (v3.0 evidence_spans)
    UNIQUE(message_id, chunk_index)
);
"""

_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id    TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      BLOB NOT NULL,                   -- float32/float16 LE bytes
    embedded_at TEXT NOT NULL
);
"""

_META = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_OTHER_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_chunks_message ON chunks(message_id);",
    "CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);",
)


def apply_schema(conn) -> None:
    """Create all tables/indexes/triggers if absent and stamp the schema version.

    Idempotent (every statement is ``IF NOT EXISTS``); safe to call on every open.
    """
    conn.execute("BEGIN")
    try:
        conn.execute(_MESSAGES)
        for stmt in _INDEXES:
            conn.execute(stmt)
        conn.execute(_MESSAGES_FTS)
        for stmt in _TRIGGERS:
            conn.execute(stmt)
        conn.execute(_CHUNKS)
        conn.execute(_EMBEDDINGS)
        for stmt in _OTHER_INDEXES:
            conn.execute(stmt)
        conn.execute(_META)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO NOTHING",
            (str(CURRENT_SCHEMA_VERSION),),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def schema_version(conn) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate(conn) -> None:
    """Forward-only migration ladder. v1 is the initial schema (no steps yet).

    Future versions add ordered ``ALTER``/backfill blocks here, each bumping the
    ``meta.schema_version`` inside one transaction.
    """
    current = schema_version(conn)
    if current > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"store schema_version {current} is newer than this build supports "
            f"({CURRENT_SCHEMA_VERSION}); upgrade digest-core."
        )
    # No migration steps for v1 → v1. (Add `if current < 2: ...` blocks here.)
