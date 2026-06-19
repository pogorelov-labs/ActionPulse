"""The encrypted message store facade: open/close + high-level operations.

The only place that sets the SQLCipher key + PRAGMAs and runs schema
bootstrap/migration. Everything is lazy: importing this module does NOT import
``sqlcipher3`` (the driver is touched only in ``open``), so the store package is
importable even when the ``store`` extra is absent.
"""

from __future__ import annotations

import os
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import structlog

from digest_core.store import ingest as _ingest
from digest_core.store import retention as _retention
from digest_core.store import retrieve as _retrieve
from digest_core.store import search as _search
from digest_core.store._driver import HAS_SQLCIPHER, INSTALL_HINT, connect, key_pragma
from digest_core.store.schema import apply_schema, migrate
from digest_core.store.search import SearchHit

logger = structlog.get_logger(__name__)


class StoreError(RuntimeError):
    """The store could not be opened or operated (driver missing, bad key, IO)."""


class MessageStore:
    """Facade over the encrypted SQLite DB. Use as a context manager."""

    def __init__(self, conn, config: Any) -> None:
        self.conn = conn
        self.config = config

    @classmethod
    def open(cls, config: Any) -> "MessageStore":
        """Open (creating if needed) the encrypted store described by ``config``.

        Raises ``StoreError`` with an actionable message when the driver is
        missing or the key is wrong; ``ValueError`` when ``DIGEST_STORE_KEY`` is
        unset (surfaced from ``StoreConfig.get_key``).
        """
        if not HAS_SQLCIPHER:
            raise StoreError(INSTALL_HINT)
        key = config.get_key()
        path = Path(config.resolved_db_path()).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        conn = connect(str(path))
        try:
            # PRAGMA key MUST be the first statement; the next read decrypts the
            # header so a wrong key on an existing DB fails here, not mid-run.
            conn.execute(key_pragma(key))
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            apply_schema(conn)
            migrate(conn)
        except Exception as exc:
            with suppress(Exception):
                conn.close()
            if existed:
                raise StoreError(
                    "Could not open the encrypted store — wrong DIGEST_STORE_KEY, or the "
                    f"file is not a SQLCipher database: {exc}"
                ) from exc
            raise StoreError(f"Could not initialize the encrypted store: {exc}") from exc
        cls._harden_perms(path)
        return cls(conn, config)

    @staticmethod
    def _harden_perms(path: Path) -> None:
        """Best-effort 0600 on the DB and its WAL/SHM sidecars (defense in depth)."""
        for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            with suppress(OSError):
                if p.exists():
                    os.chmod(p, 0o600)

    # -- operations --------------------------------------------------------

    def upsert_messages(
        self,
        messages: Iterable[Any],
        *,
        raw_by_id: Optional[Dict[str, str]] = None,
        pipeline_version: str = "",
        now: Optional[datetime] = None,
    ) -> Dict[str, int]:
        return _ingest.upsert_messages(
            self.conn,
            messages,
            raw_by_id=raw_by_id,
            pipeline_version=pipeline_version,
            now=now,
        )

    def sweep_ttl(self, ttl_days: Optional[int] = None, *, now: Optional[datetime] = None) -> int:
        days = self.config.ttl_days if ttl_days is None else ttl_days
        return _retention.sweep_ttl(self.conn, days, now=now)

    def embed_backlog(self, backend: Any, *, batch_size: int = 128) -> Dict[str, int]:
        """Fill the embedding backlog (chunks without a vector) via ``backend``."""
        return _ingest.embed_backlog(
            self.conn,
            backend,
            model=self.config.embedding_model,
            dtype=self.config.vector_dtype,
            batch_size=batch_size,
        )

    def reembed(
        self, backend: Any, *, force: bool = False, batch_size: int = 128
    ) -> Dict[str, int]:
        """Fill the embedding backlog. ``force=True`` first drops ALL existing vectors
        so a model/``vector_dtype`` change re-embeds (otherwise embed_backlog finds no
        work — every chunk still has its stale vector — and semantic search goes empty)."""
        if force:
            self.conn.execute("DELETE FROM embeddings")
        return self.embed_backlog(backend, batch_size=batch_size)

    def search(
        self,
        query: str,
        *,
        mode: Optional[str] = None,
        backend: Any = None,
        source: Optional[str] = None,
        since: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> "list[SearchHit]":
        """Search the store. ``mode`` defaults to ``config.search_default_mode``.

        ``semantic``/``hybrid`` require an embedding ``backend`` (the gateway
        EmbeddingsClient); ``keyword`` works offline with no backend.
        """
        mode = mode or self.config.search_default_mode
        limit = limit or self.config.search_limit
        model = self.config.embedding_model
        max_rows = self.config.bruteforce_max_rows
        if mode == "keyword":
            return _search.keyword(self.conn, query, limit=limit, source=source, since=since)
        if mode == "semantic":
            if backend is None:
                raise ValueError("semantic search requires an embedding backend")
            return _search.semantic(
                self.conn,
                backend,
                query,
                limit=limit,
                model=model,
                source=source,
                since=since,
                max_rows=max_rows,
            )
        if mode == "hybrid":
            if backend is None:
                raise ValueError("hybrid search requires an embedding backend")
            return _search.hybrid(
                self.conn,
                backend,
                query,
                limit=limit,
                model=model,
                source=source,
                since=since,
                max_rows=max_rows,
            )
        raise ValueError(f"unknown search mode {mode!r} (keyword | semantic | hybrid)")

    def context_passages(self, hits: Any, *, max_chars: int = 600) -> "list[Dict[str, Any]]":
        """Grounding passages for a list of SearchHits (RAG / `ask`).

        Per hit, return the fuller evidence text — the chunk text when the hit is
        chunk-level (semantic/hybrid), else the message's normalized body — capped at
        ``max_chars``, with the metadata a cited answer needs.
        """
        out: list[Dict[str, Any]] = []
        for h in hits:
            text = None
            if getattr(h, "chunk_id", None):
                row = self.conn.execute(
                    "SELECT text FROM chunks WHERE chunk_id = ?", (h.chunk_id,)
                ).fetchone()
                text = row[0] if row else None
            if text is None:
                row = self.conn.execute(
                    "SELECT body_normalized FROM messages WHERE id = ?", (h.message_id,)
                ).fetchone()
                text = row[0] if row else (h.snippet or "")
            out.append(
                {
                    "message_id": h.message_id,
                    "source": h.source,
                    "received_at": h.received_at,
                    "subject": h.subject or "",
                    "text": (text or "")[:max_chars],
                }
            )
        return out

    def stats(self) -> Dict[str, Any]:
        return _ingest.stats(self.conn)

    # -- retrieval (offline reads; see store/retrieve.py) -------------------

    def get_message(self, message_id: str):
        """One message by URN id, or None."""
        return _retrieve.get_message(self.conn, message_id)

    def get_thread(self, thread_id: str, *, limit: int = 200):
        """All messages in a thread, oldest-first."""
        return _retrieve.get_thread(self.conn, thread_id, limit=limit)

    def list_recent(self, *, limit: int = 50, source: Optional[str] = None):
        """Most recent messages first, optionally by source."""
        return _retrieve.list_recent(self.conn, limit=limit, source=source)

    def list_by_sender(self, email: str, *, limit: int = 50):
        """Messages from a sender (case-insensitive), newest-first."""
        return _retrieve.list_by_sender(self.conn, email, limit=limit)

    def list_by_date_range(
        self, start: str, end: str, *, source: Optional[str] = None, limit: int = 200
    ):
        """Messages in [start, end] inclusive (whole UTC days), oldest-first."""
        return _retrieve.list_by_date_range(self.conn, start, end, source=source, limit=limit)

    def list_threads(self, *, limit: int = 50, source: Optional[str] = None):
        """Most-recently-active threads first, with count + latest subject/author."""
        return _retrieve.list_threads(self.conn, limit=limit, source=source)

    def count_by_sender(self, *, limit: int = 20, since: Optional[str] = None):
        """Top senders by message count (optionally since a YYYY-MM-DD date)."""
        return _retrieve.count_by_sender(self.conn, limit=limit, since=since)

    def count_by_day(
        self, *, days: int = 30, source: Optional[str] = None, now: Optional[datetime] = None
    ):
        """Message counts per UTC day over the last ``days`` days."""
        return _retrieve.count_by_day(self.conn, days=days, source=source, now=now)

    def related(
        self, message_id: str, *, backend: Any = None, limit: int = 10
    ) -> "list[SearchHit]":
        """Messages semantically similar to ``message_id`` (offline when embedded).

        Uses the message's stored chunk vectors as the query; if it has none and a
        gateway ``backend`` is given, embeds its body on the fly. Excludes itself.
        """
        return _search.related_to_message(
            self.conn,
            message_id,
            model=self.config.embedding_model,
            limit=limit,
            max_rows=self.config.bruteforce_max_rows,
            backend=backend,
        )

    def vacuum(self) -> None:
        _retention.vacuum(self.conn)

    def checkpoint(self) -> None:
        _retention.checkpoint(self.conn)

    def close(self) -> None:
        with suppress(Exception):
            _retention.checkpoint(self.conn)
        with suppress(Exception):
            self.conn.close()

    def __enter__(self) -> "MessageStore":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False
