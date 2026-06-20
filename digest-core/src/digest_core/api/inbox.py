"""InboxAPI — the single local surface over the encrypted message store.

One facade, one open ``MessageStore`` for the process lifetime (opening per call
would re-run schema/migrate/PRAGMA every time). Offline verbs — retrieve / list /
count / keyword search / insights / maintenance — need no network. Gateway verbs
(semantic & hybrid search, ``ask``, ``related``, ``summarize_thread``, ``compare``,
embeddings) lazily build the corp embedding/LLM client; when the gateway is absent
they degrade honestly (search → keyword; ``compare`` cosine → None) or raise
``GatewayUnavailable`` — they never hang.

    with InboxAPI.open(config) as api:
        api.search("budget")
        api.get_thread(thread_id)
        api.pending()
"""

from __future__ import annotations

import re
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import httpx
import structlog

from digest_core.api.errors import ApiError, CorpOnlyError, GatewayUnavailable
from digest_core.config import Config
from digest_core.store.db import MessageStore, StoreError
from digest_core.store.retrieve import DayCount, MessageRecord, SenderCount, ThreadSummary
from digest_core.store.search import SearchHit

if TYPE_CHECKING:
    from digest_core.history import HistoryHit

logger = structlog.get_logger(__name__)

# Terms ignored when diffing two messages (bilingual EN+RU function words).
_STOPWORDS = frozenset(
    "the a an and or but for to of in on at by with from is are was were be been being this "
    "that these those it its as if then than so we you i he she they them our your my me re fwd "
    # apostrophe tails the len>=3 tokenizer leaves behind (don't->don, wasn't->wasn, ...)
    "don won isn aren wasn weren hasn haven didn doesn couldn wouldn shouldn ain "
    "и в во не на с со что как а но или для по от до из за то же бы ли о об к у не да".split()
)
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def _salient_terms(text: str) -> set:
    """Lowercased content words (len>=3, no stopwords/digits) for a keyword diff."""
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS}


@dataclass(frozen=True)
class CompareResult:
    message_id_a: str
    message_id_b: str
    cosine: Optional[float]  # None when either message has no stored vectors
    shared_terms: List[str] = field(default_factory=list)
    distinct_a: List[str] = field(default_factory=list)
    distinct_b: List[str] = field(default_factory=list)


def _message_to_record(msg: Any) -> MessageRecord:
    """Map a freshly-fetched ``NormalizedMessage`` to a ``MessageRecord`` (the same id +
    DM-at-rest redaction the store would apply), so live and stored reads look identical."""
    from digest_core.store.models import (
        DM_AT_REST_REDACTION,
        build_urn,
        redact_mm_body_at_rest,
    )

    source = getattr(msg, "source", "email") or "email"
    mm_ct = getattr(msg, "mm_channel_type", None)
    body = getattr(msg, "text_body", "") or ""
    if redact_mm_body_at_rest(source, mm_ct):
        body = DM_AT_REST_REDACTION  # never surface a DM body, even on a live fetch (#9)
    received = getattr(msg, "datetime_received", None)
    return MessageRecord(
        message_id=build_urn(source, msg.msg_id),
        source=source,
        thread_id=getattr(msg, "conversation_id", None),
        received_at=received.isoformat() if received else "",
        author_display=getattr(msg, "from_name", None) or "",
        author_email=getattr(msg, "from_email", "") or getattr(msg, "sender_email", "") or "",
        subject=getattr(msg, "subject", "") or "",
        body=body,
        importance=getattr(msg, "importance", "Normal") or "Normal",
        is_flagged=bool(getattr(msg, "is_flagged", False)),
        has_attachments=bool(getattr(msg, "has_attachments", False)),
        attachment_types=list(getattr(msg, "attachment_types", []) or []),
        to_recipients=list(getattr(msg, "to_recipients", []) or []),
        cc_recipients=list(getattr(msg, "cc_recipients", []) or []),
        mm_channel_type=mm_ct,
    )


class InboxAPI:
    """Local, single-surface API over the store. Use as a context manager."""

    def __init__(self, store: MessageStore, config: Config) -> None:
        self._store = store
        self._config = config
        self._backend_client: Any = None  # lazily built EmbeddingsClient (gateway)

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

    def history(
        self,
        query: Optional[str] = None,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        section: Optional[str] = None,
        limit: int = 50,
        out_dir: Optional[str] = None,
    ) -> "List[HistoryHit]":
        """Search across PAST DIGEST ARTIFACTS (the curated output history), newest first.

        Complements ``search`` (the raw message store) and ``get_thread`` (one thread):
        this scans what the digests actually surfaced over time. Store-free in
        implementation (it reads the out dir), but exposed on the facade so MCP / a bot
        reach it through one surface; the CLI ``history`` command calls ``search_history``
        directly so it keeps working when the store is off. ``section`` is a canonical key
        (my_actions / urgent / fyi / status / unconfirmed).
        """
        from pathlib import Path

        from digest_core import paths
        from digest_core.history import search_history

        target = Path(out_dir).expanduser() if out_dir else paths.out_dir(create=False)
        return search_history(target, query, since=since, until=until, section=section, limit=limit)

    # -- search (keyword offline; semantic/hybrid via the gateway) ---------

    def search(
        self,
        query: str,
        *,
        mode: str = "keyword",
        source: Optional[str] = None,
        since: Optional[str] = None,
        limit: Optional[int] = None,
        strict: bool = False,
    ) -> List[SearchHit]:
        """Search the store. ``keyword`` is offline; ``semantic``/``hybrid`` use the
        embedding gateway and, when it is unreachable, DEGRADE to keyword (the served
        method is visible in each hit's ``provenance['method']`` and logged) — unless
        ``strict=True``, which raises ``GatewayUnavailable`` instead.
        """
        if mode == "keyword":
            return self._store.search(
                query, mode="keyword", source=source, since=since, limit=limit
            )
        if mode not in ("semantic", "hybrid"):
            raise ValueError(f"unknown search mode {mode!r} (keyword | semantic | hybrid)")
        try:
            return self._store.search(
                query, mode=mode, backend=self._backend(), source=source, since=since, limit=limit
            )
        except Exception as exc:  # noqa: BLE001 - gateway/embeddings failure → degrade or raise
            if strict:
                raise GatewayUnavailable(
                    f"{mode} search needs the embedding gateway (corp network): {exc}"
                ) from exc
            logger.warning("api_search_degraded", requested=mode, served="keyword", error=str(exc))
            return self._store.search(
                query, mode="keyword", source=source, since=since, limit=limit
            )

    # -- gateway backend (lazy; corp network only) -------------------------

    def _backend(self) -> Any:
        """Build + cache the gateway EmbeddingsClient on first use. Constructing it is
        offline-safe; the network failure surfaces on the first ``embed`` call."""
        if self._backend_client is None:
            from digest_core.llm.fleet import EmbeddingsClient

            self._backend_client = EmbeddingsClient.from_config(self._config)
        return self._backend_client

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
            now=now or datetime.now(self._user_tz()),
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
            now=now or datetime.now(self._user_tz()),
            lookback_days=lookback_days,
            max_items=max_items,
        )

    def _aliases(self) -> List[str]:
        return self._config.user_aliases()

    def _user_tz(self):
        """The configured user timezone as a stdlib tzinfo (ZoneInfo), UTC on a bad name.

        Used so cross-day verbs reckon "today" against the user's local calendar day —
        the same day the digest window uses — rather than the UTC day.
        """
        from zoneinfo import ZoneInfo

        try:
            return ZoneInfo(self._config.time.user_timezone)
        except Exception:  # noqa: BLE001 - unknown tz name → UTC (matches naive-now fallback)
            return timezone.utc

    # -- reasoning (gateway; corp network) ---------------------------------

    def related(self, message_id: str, *, limit: int = 10) -> List[SearchHit]:
        """Messages similar to one you have, by stored chunk vectors. Offline when the
        store is embedded; if the source isn't embedded it uses the gateway to embed its
        body. Raises ``GatewayUnavailable`` (not ``[]``) when that on-the-fly embed can't
        reach the gateway — an empty list reads as "nothing similar", which would mask a
        connectivity failure. Genuine errors (bad id, store fault) propagate unmasked."""
        try:
            return self._store.related(message_id, backend=self._backend(), limit=limit)
        except GatewayUnavailable:
            raise
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            logger.warning("api_related_gateway_unavailable", message_id=message_id, error=str(exc))
            raise GatewayUnavailable(
                f"related needs the gateway to embed this message: {type(exc).__name__}"
            ) from exc

    def ask(
        self,
        question: str,
        *,
        mode: str = "keyword",
        top_k: int = 8,
        source: Optional[str] = None,
        since: Optional[str] = None,
    ):
        """Grounded, cited answer over your messages (corp LLM). Retrieval defaults to
        keyword (offline); the answer itself always needs the gateway, so it raises
        ``GatewayUnavailable`` offline. Returns an ``ask.AskResult``."""
        from digest_core.ask import AskUnavailable, answer_question

        backend = self._backend() if mode != "keyword" else None
        try:
            return answer_question(
                self._store,
                question,
                backend=backend,
                config=self._config,
                mode=mode,
                top_k=top_k,
                source=source,
                since=since,
            )
        except AskUnavailable as exc:
            raise GatewayUnavailable(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - ask is corp-only; any failure ≈ gateway down
            raise GatewayUnavailable(f"ask needs the corp gateway ({type(exc).__name__})") from exc

    def summarize_thread(self, thread_id: str, *, limit: int = 200):
        """One grounded summary of a thread (corp LLM), leading with anything awaiting you.
        Returns an ``ask.AskResult``; raises ``ApiError`` if the thread is empty."""
        from digest_core.ask import AskUnavailable, summarize_passages

        records = self._store.get_thread(thread_id, limit=limit)
        if not records:
            raise ApiError(f"no messages in thread {thread_id!r}")
        passages = [
            {
                "message_id": r.message_id,
                "source": r.source,
                "received_at": r.received_at,
                "subject": r.subject,
                "text": (r.body or "")[:600],
            }
            for r in records
        ]
        try:
            return summarize_passages(passages, config=self._config)
        except AskUnavailable as exc:
            raise GatewayUnavailable(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - summarize is corp-only; any failure ≈ gateway
            raise GatewayUnavailable(
                f"summarize_thread needs the corp gateway ({type(exc).__name__})"
            ) from exc

    def compare(
        self, message_id_a: str, message_id_b: str, *, top_terms: int = 15
    ) -> CompareResult:
        """Compare two messages: cosine over their stored vectors (None when either is
        unembedded) + a shared/distinct keyword diff. Offline — no gateway call."""
        from digest_core.store.search import message_cosine

        ra = self._store.get_message(message_id_a)
        rb = self._store.get_message(message_id_b)
        if ra is None or rb is None:
            raise ApiError("one or both messages not found")
        cosine = message_cosine(
            self._store.conn, message_id_a, message_id_b, model=self._config.store.embedding_model
        )
        ta = _salient_terms(f"{ra.subject} {ra.body}")
        tb = _salient_terms(f"{rb.subject} {rb.body}")
        return CompareResult(
            message_id_a=message_id_a,
            message_id_b=message_id_b,
            cosine=cosine,
            shared_terms=sorted(ta & tb)[:top_terms],
            distinct_a=sorted(ta - tb)[:top_terms],
            distinct_b=sorted(tb - ta)[:top_terms],
        )

    # -- maintenance (offline reads; embed/reembed need the gateway) -------

    def sweep_ttl(self, ttl_days: Optional[int] = None) -> int:
        return self._store.sweep_ttl(ttl_days)

    def embed_backlog(self, *, batch_size: int = 128) -> Dict[str, int]:
        """Embed chunks that have no vector yet (gateway)."""
        return self._store.embed_backlog(self._backend(), batch_size=batch_size)

    def reembed(self, *, force: bool = False, batch_size: int = 128) -> Dict[str, int]:
        """Re-embed; ``force`` drops existing vectors first (model/dtype change). Gateway."""
        return self._store.reembed(self._backend(), force=force, batch_size=batch_size)

    def vacuum(self) -> None:
        self._store.vacuum()

    def checkpoint(self) -> None:
        self._store.checkpoint()

    # -- sources (live EWS/MM; corp network) -------------------------------

    def fetch_source(self, source: str, digest_date: str) -> List[MessageRecord]:
        """Live-fetch a source's messages for a date (YYYY-MM-DD) WITHOUT persisting —
        the read-shaped API never writes the store. Corp-network only; raises
        ``CorpOnlyError`` when the adapter can't reach it. DM bodies stay redacted."""
        adapter = self._source_adapter(source)
        try:
            messages = adapter.fetch(digest_date)
        except Exception as exc:  # noqa: BLE001 - live fetch is corp-only; surface clearly
            raise CorpOnlyError(f"{source} fetch failed (needs the corp network): {exc}") from exc
        return [_message_to_record(m) for m in messages]

    def list_containers(self, source: str) -> List[Dict[str, Any]]:
        """Folders (EWS) or channels (MM) for a source. Corp-network for MM."""
        from digest_core.ingest.source_adapter import canonical_source

        canonical = canonical_source(source)
        if canonical == "mm":
            with self._mm_client() as client:
                try:
                    channels = client.get_my_channels()
                except Exception as exc:  # noqa: BLE001
                    raise CorpOnlyError(f"Mattermost channels unavailable: {exc}") from exc
            return [
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "display_name": c.get("display_name"),
                    "type": c.get("type"),
                }
                for c in channels
            ]
        if canonical == "ews":
            # EWS has no folder-enumeration API; report the configured folders.
            return [{"name": f} for f in (self._config.ews.folders or [])]
        raise ApiError(f"unknown source {source!r} (ews | mm)")

    def get_reactions(self, post_id: str) -> List[Dict[str, Any]]:
        """Mattermost reactions on a post (corp network) — for the feedback loop."""
        with self._mm_client() as client:
            try:
                return client.get_post_reactions(post_id)
            except Exception as exc:  # noqa: BLE001
                raise CorpOnlyError(f"Mattermost reactions unavailable: {exc}") from exc

    def _source_adapter(self, source: str):
        from digest_core.ingest.source_adapter import build_adapter

        try:
            return build_adapter(source, self._config)
        except ValueError as exc:  # keep the InboxAPI's ApiError contract for unknown sources
            raise ApiError(str(exc)) from exc

    @contextmanager
    def _mm_client(self):
        """A Mattermost read client whose underlying ``httpx.Client`` is closed on exit.
        The long-lived MCP server holds one InboxAPI, so a per-call client that is never
        closed leaks a connection pool (sockets/fds) on every source verb."""
        from digest_core.ingest.mattermost import MattermostReadClient

        mm = self._config.mm_source
        base_url = mm.get_base_url()
        if not base_url:
            raise CorpOnlyError("Mattermost base URL not set (MM_BASE_URL / mm_source.base_url).")
        try:
            token = mm.get_token()  # raises ValueError if MM_PAT unset
        except ValueError as exc:
            raise CorpOnlyError(f"Mattermost PAT not set: {exc}") from exc
        with httpx.Client(timeout=httpx.Timeout(mm.timeout_s), verify=mm.verify_ssl) as http:
            yield MattermostReadClient(base_url, token, http_client=http, per_page=mm.per_page)

    # -- lifecycle ---------------------------------------------------------

    @property
    def store(self) -> MessageStore:
        return self._store

    def close(self) -> None:
        backend = self._backend_client
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
