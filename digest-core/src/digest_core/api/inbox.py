"""InboxAPI — the single local surface over the encrypted message store.

One facade, one open ``MessageStore`` for the process lifetime (opening per call
would re-run schema/migrate/PRAGMA every time). Offline verbs — retrieve / list /
count / keyword search / insights / maintenance — need no network. Gateway verbs
(semantic & hybrid search, ``ask``, ``related``, ``summarize_thread``, ``compare``,
embeddings) are wired in the gateway-verbs phase and raise ``GatewayUnavailable``
when the corp gateway is absent — they never hang.

    with InboxAPI.open(config) as api:
        api.search("budget")
        api.get_thread(thread_id)
        api.pending()
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from digest_core.api.errors import ApiError, GatewayUnavailable
from digest_core.config import Config
from digest_core.store.db import MessageStore, StoreError
from digest_core.store.retrieve import DayCount, MessageRecord, SenderCount, ThreadSummary
from digest_core.store.search import SearchHit


class InboxAPI:
    """Local, single-surface API over the store. Use as a context manager."""

    def __init__(self, store: MessageStore, config: Config) -> None:
        self._store = store
        self._config = config
        self._backend: Any = None  # lazily built by gateway verbs (follow-up phase)

    @classmethod
    def open(cls, config: Optional[Config] = None) -> "InboxAPI":
        """Open the encrypted store from ``config.store``.

        Raises ``ApiError`` when the store is off / the driver is missing / the key is
        wrong (wrapping ``StoreError`` and the ``ValueError`` from an unset key).
        """
        config = config or Config()
        try:
            store = MessageStore.open(config.store)
        except (StoreError, ValueError) as exc:
            raise ApiError(str(exc)) from exc
        return cls(store, config)

    # -- retrieve (offline) ------------------------------------------------

    def get_message(self, message_id: str) -> Optional[MessageRecord]:
        return self._store.get_message(message_id)

    def get_thread(self, thread_id: str, *, limit: int = 200) -> List[MessageRecord]:
        return self._store.get_thread(thread_id, limit=limit)

    def list_recent(self, *, limit: int = 50, source: Optional[str] = None) -> List[MessageRecord]:
        return self._store.list_recent(limit=limit, source=source)

    def list_by_sender(self, email: str, *, limit: int = 50) -> List[MessageRecord]:
        return self._store.list_by_sender(email, limit=limit)

    def list_by_date_range(
        self, start: str, end: str, *, source: Optional[str] = None, limit: int = 200
    ) -> List[MessageRecord]:
        return self._store.list_by_date_range(start, end, source=source, limit=limit)

    def list_threads(self, *, limit: int = 50, source: Optional[str] = None) -> List[ThreadSummary]:
        return self._store.list_threads(limit=limit, source=source)

    # -- counts / insights-lite (offline) ----------------------------------

    def count_by_sender(self, *, limit: int = 20, since: Optional[str] = None) -> List[SenderCount]:
        return self._store.count_by_sender(limit=limit, since=since)

    def count_by_day(self, *, days: int = 30, source: Optional[str] = None) -> List[DayCount]:
        return self._store.count_by_day(days=days, source=source)

    def timeline(self, *, days: int = 30, source: Optional[str] = None) -> List[DayCount]:
        """Message volume per UTC day — a named view over ``count_by_day``."""
        return self.count_by_day(days=days, source=source)

    def stats(self) -> Dict[str, Any]:
        return self._store.stats()

    # -- search (keyword offline; semantic/hybrid via the gateway) ---------

    def search(
        self,
        query: str,
        *,
        mode: str = "keyword",
        source: Optional[str] = None,
        since: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[SearchHit]:
        """Search the store. ``keyword`` is offline; ``semantic``/``hybrid`` need the
        embedding gateway (corp network) and raise ``GatewayUnavailable`` when absent."""
        if mode == "keyword":
            return self._store.search(
                query, mode="keyword", source=source, since=since, limit=limit
            )
        return self._search_gateway(query, mode=mode, source=source, since=since, limit=limit)

    def _search_gateway(self, query: str, *, mode: str, source, since, limit) -> List[SearchHit]:
        # Gateway-backed search is wired in the gateway-verbs phase; until then the
        # local API serves keyword only.
        raise GatewayUnavailable(
            f"{mode} search needs the embedding gateway (corp network); "
            "use mode='keyword' offline"
        )

    # -- insights (offline) ------------------------------------------------

    def open_loops(
        self,
        *,
        now: Optional[datetime] = None,
        lookback_days: int = 7,
        stale_days: int = 2,
        max_items: int = 5,
    ):
        """Threads you were in that have gone quiet (cross-day open loops)."""
        from digest_core.store.carryover import find_open_loops

        return find_open_loops(
            self._store.conn,
            user_aliases=self._aliases(),
            now=now or datetime.now(timezone.utc),
            lookback_days=lookback_days,
            stale_days=stale_days,
            max_items=max_items,
        )

    def pending(
        self,
        *,
        now: Optional[datetime] = None,
        lookback_days: int = 7,
        max_items: int = 5,
    ):
        """Prior-day messages that asked you something you haven't answered since."""
        from digest_core.store.pending import find_pending_requests

        return find_pending_requests(
            self._store.conn,
            user_aliases=self._aliases(),
            now=now or datetime.now(timezone.utc),
            lookback_days=lookback_days,
            max_items=max_items,
        )

    def _aliases(self) -> List[str]:
        return self._config.user_aliases()

    # -- maintenance (offline) ---------------------------------------------

    def sweep_ttl(self, ttl_days: Optional[int] = None) -> int:
        return self._store.sweep_ttl(ttl_days)

    def vacuum(self) -> None:
        self._store.vacuum()

    def checkpoint(self) -> None:
        self._store.checkpoint()

    # -- lifecycle ---------------------------------------------------------

    @property
    def store(self) -> MessageStore:
        return self._store

    def close(self) -> None:
        backend = self._backend
        if backend is not None:
            with suppress(Exception):
                close = getattr(backend, "close", None)
                if callable(close):
                    close()
        self._store.close()

    def __enter__(self) -> "InboxAPI":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False
