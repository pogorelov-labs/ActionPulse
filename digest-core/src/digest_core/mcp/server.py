"""ActionPulse MCP server — exposes the local InboxAPI over stdio.

The tool bodies are module-level functions (no MCP SDK needed to call them, so they
are unit-testable); ``_build_app`` registers them on a FastMCP app, importing the SDK
lazily so a default install without the ``mcp`` extra never imports it.

Exposure policy:
* FULL CONTENT by default — tools/resources return message bodies and RAG answers.
  ``ACTIONPULSE_MCP_REDACT_BODIES=1`` → metadata-only: bodies/snippets/passages emptied,
  ``compare`` term lists blanked, and ``ask``/``summarize_thread`` disabled (their output
  is body-DERIVED — a synthesized answer or verbatim quotes). DM bodies are ALWAYS redacted
  at rest (#9). Invariant under redact: nothing body-derived (verbatim or synthesized)
  crosses the wire — only metadata (subjects/authors/dates/counts/cosine/ids).
* Store-MUTATING maintenance (sweep_ttl / embed_backlog / reembed / vacuum) is OFF
  unless ``ACTIONPULSE_MCP_ENABLE_MAINTENANCE=1`` — an autonomous agent should not
  prune or re-embed your store unprompted.
* CORP-NETWORK source tools: ``list_containers`` / ``get_reactions`` (read-only) are
  always registered; the live ``fetch_source`` pull is OFF unless
  ``ACTIONPULSE_MCP_ENABLE_FETCH=1``.

The store key (``DIGEST_STORE_KEY``) is read from the environment (the 0600
``~/.config/actionpulse/env`` the CLI already loads) — NEVER from a tool argument or a
client config. Gateway verbs (semantic/ask/...) degrade or error honestly off-corp.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from digest_core.api import ApiError, GatewayUnavailable, InboxAPI
from digest_core.config import Config

_api: Optional[InboxAPI] = None


def _get_api() -> InboxAPI:
    """The process-wide InboxAPI (opened on first use). Raises ApiError if the store
    is off / the key is unset — surfaced to the client as a tool error, not a crash.

    The failure is re-raised pointing at ``health``: every read tool here fails
    *identically* on a missing precondition, so the bare store error tells an agent
    nothing about which of driver / key / enabled / data is the broken link.
    """
    global _api
    if _api is None:
        try:
            _api = InboxAPI.open(Config())
        except ApiError as exc:
            raise ApiError(
                f"{exc}  —  call the `health` tool for which precondition is missing "
                "and the exact command that fixes it."
            ) from exc
    return _api


def _redact_bodies() -> bool:
    return bool(os.getenv("ACTIONPULSE_MCP_REDACT_BODIES"))


# -- serialization (dataclasses -> JSON-able dicts) ------------------------


def _record_dict(rec: Any) -> Optional[Dict[str, Any]]:
    if rec is None:
        return None
    d = asdict(rec)
    if _redact_bodies():
        d["body"] = ""
    return d


def _records(recs: Any) -> List[Dict[str, Any]]:
    return [_record_dict(r) for r in recs]


def _hit_dict(h: Any) -> Dict[str, Any]:
    d = asdict(h)
    if _redact_bodies():
        d["snippet"] = ""
    return d


def _ask_dict(res: Any) -> Dict[str, Any]:
    d = asdict(res)
    d.pop("raw", None)  # internal LLM verdict — not for the client
    if _redact_bodies():
        for p in d.get("passages", []):
            p["text"] = ""
    return d


# -- tool bodies -----------------------------------------------------------


def _tool_search(
    query: str,
    mode: str = "keyword",
    source: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Search your messages. mode: keyword (offline) | semantic | hybrid (needs the corp
    gateway). When the gateway is unreachable, semantic/hybrid DEGRADE to keyword and the
    response says so in ``served_mode``/``degraded`` (so an empty result is not mistaken
    for 'nothing matches'). since is YYYY-MM-DD."""
    api = _get_api()
    served = mode
    try:
        hits = api.search(query, mode=mode, source=source, since=since, limit=limit, strict=True)
    except GatewayUnavailable:
        served = "keyword"
        hits = api.search(query, mode="keyword", source=source, since=since, limit=limit)
    return {
        "requested_mode": mode,
        "served_mode": served,
        "degraded": served != mode,
        "results": [_hit_dict(h) for h in hits],
    }


def _tool_get_message(message_id: str) -> Optional[Dict[str, Any]]:
    """Fetch one message by its id (URN)."""
    return _record_dict(_get_api().get_message(message_id))


def _tool_get_thread(thread_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """All messages in a thread, oldest first."""
    return _records(_get_api().get_thread(thread_id, limit=limit))


def _tool_list_recent(limit: int = 50, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Most recent messages first; source is 'email' or 'mm'."""
    return _records(_get_api().list_recent(limit=limit, source=source))


def _tool_list_by_sender(email: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Messages from a given sender email, newest first."""
    return _records(_get_api().list_by_sender(email, limit=limit))


def _tool_list_by_date_range(
    start: str, end: str, source: Optional[str] = None, limit: int = 200
) -> List[Dict[str, Any]]:
    """Messages between two YYYY-MM-DD dates (inclusive, UTC days), oldest first."""
    return _records(_get_api().list_by_date_range(start, end, source=source, limit=limit))


def _tool_list_threads(limit: int = 50, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Most-recently-active threads, with count and latest subject/author."""
    return [asdict(t) for t in _get_api().list_threads(limit=limit, source=source)]


def _tool_count_by_sender(limit: int = 20, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Top senders by message count (optionally since a YYYY-MM-DD date)."""
    return [asdict(s) for s in _get_api().count_by_sender(limit=limit, since=since)]


def _tool_count_by_day(days: int = 30, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Message counts per UTC day over the last N days."""
    return [asdict(d) for d in _get_api().count_by_day(days=days, source=source)]


def _tool_timeline(days: int = 30, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Message volume per day over the last N days (a named view of count_by_day)."""
    return [asdict(d) for d in _get_api().timeline(days=days, source=source)]


def _tool_stats() -> Dict[str, Any]:
    """Store summary: message/chunk/embedding counts by source and date range."""
    return _get_api().stats()


def _tool_history(
    query: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    section: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Search across PAST DIGESTS (the curated output history), newest first. Unlike `search`
    (the raw message store), this scans what your digests actually surfaced over time.
    section: my_actions | urgent | fyi | status | unconfirmed. since/until are YYYY-MM-DD."""
    hits = _get_api().history(query, since=since, until=until, section=section, limit=limit)
    return [
        {
            "digest_date": h.digest_date,
            "section": h.section_key or h.section_title,
            "title": h.item.title,
            "due": h.item.due,
            "source_from": h.item.source_from,
            "source_subject": h.item.source_subject,
            "evidence_id": h.item.evidence_id,
        }
        for h in hits
    ]


def _tool_related(message_id: str, limit: int = 10) -> Dict[str, Any]:
    """Messages similar to a given one (uses stored vectors). Needs the gateway only if the
    source isn't embedded yet; ``degraded: true`` means the gateway was unreachable, so the
    empty result reflects that — not that nothing is similar."""
    try:
        hits = _get_api().related(message_id, limit=limit)
        return {"results": [_hit_dict(h) for h in hits], "degraded": False}
    except GatewayUnavailable:
        return {
            "results": [],
            "degraded": True,
            "reason": "gateway unavailable to embed this message",
        }


def _ask_disabled_under_redact() -> Dict[str, Any]:
    return {
        "answered": False,
        "answer": "Disabled: ACTIONPULSE_MCP_REDACT_BODIES is set, and a grounded answer "
        "(plus verbatim citation quotes) would carry message-body content.",
        "citations": [],
        "passages": [],
    }


def _tool_ask(
    question: str, top_k: int = 8, source: Optional[str] = None, since: Optional[str] = None
) -> Dict[str, Any]:
    """Grounded, cited answer over your messages. Needs the corp gateway (errors off-corp).
    Disabled (no gateway call) when ACTIONPULSE_MCP_REDACT_BODIES is set."""
    if _redact_bodies():
        return _ask_disabled_under_redact()
    return _ask_dict(_get_api().ask(question, top_k=top_k, source=source, since=since))


def _tool_summarize_thread(thread_id: str) -> Dict[str, Any]:
    """Summarize a thread, leading with anything awaiting your reply. Needs the corp gateway.
    Disabled when ACTIONPULSE_MCP_REDACT_BODIES is set."""
    if _redact_bodies():
        return _ask_disabled_under_redact()
    return _ask_dict(_get_api().summarize_thread(thread_id))


def _tool_compare(message_id_a: str, message_id_b: str) -> Dict[str, Any]:
    """Compare two messages: vector cosine (null if unembedded) + shared/distinct key terms."""
    d = asdict(_get_api().compare(message_id_a, message_id_b))
    if _redact_bodies():
        # the term lists are salient words pulled from the bodies → blank under redact
        d["shared_terms"] = d["distinct_a"] = d["distinct_b"] = []
    return d


def _tool_open_loops(
    lookback_days: int = 7, stale_days: int = 2, max_items: int = 5
) -> List[Dict[str, Any]]:
    """Threads you were in that have gone quiet — likely still waiting on you."""
    return [
        asdict(x)
        for x in _get_api().open_loops(
            lookback_days=lookback_days, stale_days=stale_days, max_items=max_items
        )
    ]


def _tool_pending(lookback_days: int = 7, max_items: int = 5) -> List[Dict[str, Any]]:
    """Prior-day messages that asked you something you haven't answered since."""
    return [asdict(x) for x in _get_api().pending(lookback_days=lookback_days, max_items=max_items)]


# maintenance (registered only when ACTIONPULSE_MCP_ENABLE_MAINTENANCE is set)


def _tool_sweep_ttl(ttl_days: Optional[int] = None) -> int:
    """Delete messages older than the TTL (returns the count deleted)."""
    return _get_api().sweep_ttl(ttl_days)


def _tool_embed_backlog() -> Dict[str, int]:
    """Embed chunks with no vector yet (needs the corp gateway)."""
    return _get_api().embed_backlog()


def _tool_reembed(force: bool = False) -> Dict[str, int]:
    """Re-embed; force drops existing vectors first. Needs the corp gateway."""
    return _get_api().reembed(force=force)


def _tool_vacuum() -> str:
    """Reclaim free space in the encrypted store."""
    _get_api().vacuum()
    return "ok"


# sources (corp network). list_containers/get_reactions are read-only and always
# registered (flagged corp-only); fetch_source triggers a live pull, so it's behind
# ACTIONPULSE_MCP_ENABLE_FETCH.


def _tool_list_containers(source: str) -> List[Dict[str, Any]]:
    """CORP-ONLY. Folders (source='ews') or channels (source='mm') for a source."""
    return _get_api().list_containers(source)


def _tool_get_reactions(post_id: str) -> List[Dict[str, Any]]:
    """CORP-ONLY. Mattermost reactions on a post."""
    return _get_api().get_reactions(post_id)


def _tool_fetch_source(source: str, digest_date: str) -> List[Dict[str, Any]]:
    """CORP-ONLY, off unless ACTIONPULSE_MCP_ENABLE_FETCH. Live-fetch a source
    ('ews'|'mm') for a date (YYYY-MM-DD) without persisting."""
    return _records(_get_api().fetch_source(source, digest_date))


# health (the one tool that must never fail)


def _probe_store() -> Dict[str, Any]:
    """Inspect the store WITHOUT going through the cached ``_get_api``.

    Deliberately separate: ``_get_api`` memoises a *successful* open, and health has to
    report the truth right now — including the case where opening is exactly what fails.
    """
    from digest_core.store.db import HAS_SQLCIPHER

    out: Dict[str, Any] = {
        "driver_installed": bool(HAS_SQLCIPHER),
        "key_set": False,
        "enabled": False,
        "db_exists": False,
        "openable": False,
        "messages": None,
    }
    try:
        store_cfg = Config().store
    except Exception as exc:  # a broken config must not take health down with it
        out["error"] = f"config could not be loaded: {type(exc).__name__}: {exc}"
        return out
    out["enabled"] = bool(getattr(store_cfg, "enabled", False))
    out["key_env"] = getattr(store_cfg, "key_env", "DIGEST_STORE_KEY")
    try:
        store_cfg.get_key()
        out["key_set"] = True
    except Exception:
        out["key_set"] = False  # unset key is a *state*, not an error to propagate
    try:
        from pathlib import Path

        db = Path(store_cfg.resolved_db_path())
        out["db_path"] = str(db)
        out["db_exists"] = db.exists()
        out["db_size_bytes"] = db.stat().st_size if db.exists() else 0
    except Exception as exc:
        out["error"] = f"db path unresolvable: {type(exc).__name__}: {exc}"
    if out["driver_installed"] and out["key_set"]:
        try:
            api = _get_api()
            stats = api.stats()
            out["openable"] = True
            out["messages"] = stats.get("messages")
            out["newest"] = stats.get("newest")
            out["oldest"] = stats.get("oldest")
            out["embeddings"] = stats.get("embeddings")
        except Exception as exc:
            out["open_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _tool_health() -> Dict[str, Any]:
    """Is this server actually able to answer? Never fails — call it first when anything errors.

    Every other tool here is a read projection over the encrypted store, so a missing
    precondition makes ~20 tools fail identically with no way to tell WHICH link is broken.
    This reports each link and the exact command that fixes it. Returns counts, paths and
    flags only — no message content, so it is unaffected by ACTIONPULSE_MCP_REDACT_BODIES.
    """
    store = _probe_store()
    blockers: List[Dict[str, str]] = []
    if not store["driver_installed"]:
        blockers.append(
            {
                "problem": "SQLCipher driver missing — the store cannot be opened at all.",
                "fix": "uv sync --extra store   (macOS also needs: brew install sqlcipher openssl@3)",
            }
        )
    if not store["key_set"]:
        blockers.append(
            {
                "problem": f"{store.get('key_env', 'DIGEST_STORE_KEY')} is not set, so the "
                "encrypted store cannot be decrypted.",
                "fix": "actionpulse store init   (writes a 0600 key to ~/.config/actionpulse/env)",
            }
        )
    if not store["enabled"]:
        # Read-path-only: reads work on an existing DB even with ingestion off, so this
        # is a warning about FRESHNESS, not about the server being broken. Say which.
        blockers.append(
            {
                "problem": "store.enabled is false — nothing new is being ingested, so this "
                "server can only serve whatever is already in the DB.",
                "fix": "add `store:\\n  enabled: true` to configs/config.yaml "
                "(or export DIGEST_STORE_ENABLED=1)",
            }
        )
    if store.get("open_error"):
        blockers.append(
            {
                "problem": f"the store exists but could not be opened: {store['open_error']}",
                "fix": "usually a wrong/rotated DIGEST_STORE_KEY. `actionpulse store drop` "
                "starts over (destroys stored history).",
            }
        )
    if store["openable"] and not store["messages"]:
        blockers.append(
            {
                "problem": "the store is open and healthy but EMPTY — every search/list will "
                "return nothing, which is easy to misread as 'no matches'.",
                "fix": "run a digest on the corp network to populate it: `actionpulse run` "
                "(or `actionpulse daemon install` to keep it fresh automatically)",
            }
        )
    try:
        daemon = _tool_daemon_status()
    except Exception as exc:
        daemon = {"error": f"{type(exc).__name__}: {exc}"}
    if daemon.get("installed") is False:
        blockers.append(
            {
                "problem": "the background ingestion daemon is not installed, so the store is "
                "only as fresh as the last manual run.",
                "fix": "actionpulse daemon install   (macOS LaunchAgent)",
            }
        )
    return {
        # `ok` is deliberately about *serving*, not about being fully set up: a healthy
        # empty store can answer every query correctly, it just answers "nothing".
        "ok": bool(store["openable"]),
        "can_serve_content": bool(store["openable"] and store["messages"]),
        "store": store,
        "daemon": {
            k: daemon.get(k) for k in ("installed", "is_stale", "staleness_days", "last_run")
        },
        "exposure": {
            "redact_bodies": _redact_bodies(),
            "maintenance_enabled": bool(os.getenv("ACTIONPULSE_MCP_ENABLE_MAINTENANCE")),
            "fetch_enabled": bool(os.getenv("ACTIONPULSE_MCP_ENABLE_FETCH")),
        },
        "blockers": blockers,
    }


# background ingestion daemon (status is always on; trigger_ingest is fetch-gated)


def _tool_daemon_status() -> Dict[str, Any]:
    """Background ingestion daemon status: last/next run, per-source message counts, whether
    the LaunchAgent is installed, corp reachability, and staleness — so you can tell when the
    store is going stale (e.g. because you've been off-corp). Read-only; no message content,
    unaffected by ACTIONPULSE_MCP_REDACT_BODIES (counts and timestamps only)."""
    from digest_core.daemon import status

    return status.summarize()


def _tool_trigger_ingest(sources: str = "mm") -> Dict[str, Any]:
    """CORP-AWARE, off unless ACTIONPULSE_MCP_ENABLE_FETCH. Run one ingestion tick NOW and
    PERSIST to the store — unlike ``fetch_source``, which never writes. ``sources`` is
    comma-separated ('mm'|'ews'); MM ingests anywhere, EWS only when on-corp (an off-corp
    tick skips it, non-fatal). Returns the tick summary (counts + reachability)."""
    from digest_core.daemon import tick

    src = [s.strip() for s in sources.split(",") if s.strip()] or None
    return tick.ingest_once(sources=src).as_status()


# -- resources -------------------------------------------------------------


def _resource_message(message_id: str) -> Optional[Dict[str, Any]]:
    # ids are URNs (urn:email:… / urn:mm:…) with ':' / '@'; unquote so an encoded URI
    # path segment round-trips to the stored id (see the triple-slash template below).
    return _record_dict(_get_api().get_message(urllib.parse.unquote(message_id)))


def _resource_thread(thread_id: str) -> List[Dict[str, Any]]:
    return _records(_get_api().get_thread(urllib.parse.unquote(thread_id)))


def _resource_stats() -> Dict[str, Any]:
    return _get_api().stats()


# -- prompts ---------------------------------------------------------------


def _prompt_inbox_triage() -> str:
    return (
        "Help me triage my inbox. Call the open_loops, pending and list_recent tools, "
        "then summarize what needs my attention — most urgent first — citing message ids."
    )


def _prompt_catch_up_on_thread(thread_id: str) -> str:
    return (
        f"Catch me up on thread {thread_id}. Call summarize_thread with that id and relay "
        "the summary, leading with anything awaiting my reply."
    )


# Tool registry: (function, public name). Read-only + reasoning; always registered.
_TOOLS = [
    (_tool_health, "health"),
    (_tool_search, "search"),
    (_tool_get_message, "get_message"),
    (_tool_get_thread, "get_thread"),
    (_tool_list_recent, "list_recent"),
    (_tool_list_by_sender, "list_by_sender"),
    (_tool_list_by_date_range, "list_by_date_range"),
    (_tool_list_threads, "list_threads"),
    (_tool_count_by_sender, "count_by_sender"),
    (_tool_count_by_day, "count_by_day"),
    (_tool_timeline, "timeline"),
    (_tool_stats, "stats"),
    (_tool_history, "history"),
    (_tool_related, "related"),
    (_tool_ask, "ask"),
    (_tool_summarize_thread, "summarize_thread"),
    (_tool_compare, "compare"),
    (_tool_open_loops, "open_loops"),
    (_tool_pending, "pending"),
    (_tool_daemon_status, "daemon_status"),
]
_MAINTENANCE_TOOLS = [
    (_tool_sweep_ttl, "sweep_ttl"),
    (_tool_embed_backlog, "embed_backlog"),
    (_tool_reembed, "reembed"),
    (_tool_vacuum, "vacuum"),
]
# Read-only corp-network source tools; always registered (flagged corp-only).
_SOURCE_TOOLS = [
    (_tool_list_containers, "list_containers"),
    (_tool_get_reactions, "get_reactions"),
]


def _build_app():
    """Construct the FastMCP app and register tools/resources/prompts.

    Imports the MCP SDK lazily so importing this module never requires the ``mcp``
    extra. Raises ModuleNotFoundError(name='mcp') when the extra is absent.
    """
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("actionpulse")
    for fn, name in _TOOLS + _SOURCE_TOOLS:
        app.tool(name=name)(fn)
    if os.getenv("ACTIONPULSE_MCP_ENABLE_MAINTENANCE"):
        for fn, name in _MAINTENANCE_TOOLS:
            app.tool(name=name)(fn)
    if os.getenv("ACTIONPULSE_MCP_ENABLE_FETCH"):
        app.tool(name="fetch_source")(_tool_fetch_source)
        app.tool(name="trigger_ingest")(_tool_trigger_ingest)
    # Triple slash → the id is the URI PATH (not the authority), so a URN's ':'/'@'
    # survive instead of being mangled into a host/port/userinfo split.
    app.resource("message:///{message_id}")(_resource_message)
    app.resource("thread:///{thread_id}")(_resource_thread)
    app.resource("stats://store")(_resource_stats)
    app.prompt(name="inbox_triage")(_prompt_inbox_triage)
    app.prompt(name="catch_up_on_thread")(_prompt_catch_up_on_thread)
    return app


def run_server() -> None:
    """Hydrate the store key from the env file, build the app, serve over stdio."""
    from digest_core.ui.menu import load_env_file

    load_env_file()  # DIGEST_STORE_KEY from ~/.config/actionpulse/env — never from config
    app = _build_app()
    try:
        app.run()
    finally:
        global _api
        if _api is not None:
            _api.close()  # checkpoint WAL + close the gateway client on shutdown
            _api = None
