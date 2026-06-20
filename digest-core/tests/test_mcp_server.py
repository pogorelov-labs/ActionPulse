"""MCP server — tool bodies, redaction, key-safety, app registration.

Tool bodies are tested directly (no SDK needed). App-registration tests need the
``mcp`` extra; all need the ``store`` extra for the InboxAPI behind the tools.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.mcp import server
from digest_core.store import HAS_SQLCIPHER

try:
    import mcp.server.fastmcp  # noqa: F401

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


def _d(day):
    return datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc)


def _msg(msg_id, body, *, thread=None, subject="S", to=None, when=None):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=thread or ("c-" + msg_id),
        datetime_received=when or _d(1),
        sender_email="ivan@corp",
        subject=subject,
        text_body=body,
        to_recipients=to or [],
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    from digest_core.api import InboxAPI

    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    cfg = Config()
    cfg.store.db_path = str(tmp_path / "m.db")
    cfg.ews.user_aliases = ["me@corp"]
    a = InboxAPI.open(cfg)
    monkeypatch.setattr(server, "_api", a)  # the tools call _get_api() -> this
    yield a
    a.close()


def test_tool_bodies_serialize(api):
    api.store.upsert_messages(
        [_msg("a@corp", "the budget", thread="T", to=["me@corp"], when=_d(2))]
    )
    rec = server._tool_get_message("urn:email:a@corp")
    assert rec["subject"] == "S" and rec["message_id"] == "urn:email:a@corp"
    search = server._tool_search("budget")
    assert search["served_mode"] == "keyword" and search["degraded"] is False
    assert search["results"][0]["message_id"] == "urn:email:a@corp"
    assert server._tool_stats()["messages"] == 1
    assert server._tool_list_threads()[0]["thread_id"] == "T"
    assert isinstance(server._tool_open_loops(), list)
    assert isinstance(server._tool_pending(), list)


def test_compare_tool_returns_dict(api):
    api.store.upsert_messages(
        [_msg("a@corp", "the budget plan"), _msg("b@corp", "budget plan review")]
    )
    cmp = server._tool_compare("urn:email:a@corp", "urn:email:b@corp")
    assert cmp["cosine"] is None  # unembedded
    assert "budget" in cmp["shared_terms"] and "plan" in cmp["shared_terms"]


def test_redact_bodies_flag_no_body_derived_content(api, monkeypatch):
    api.store.upsert_messages(
        [_msg("a@corp", "secret budget body here"), _msg("b@corp", "secret budget review")]
    )
    monkeypatch.setenv("ACTIONPULSE_MCP_REDACT_BODIES", "1")
    rec = server._tool_get_message("urn:email:a@corp")
    assert rec["body"] == "" and "secret" not in str(rec)
    # compare term lists are pulled from bodies → blanked under redact (cosine/ids survive)
    cmp = server._tool_compare("urn:email:a@corp", "urn:email:b@corp")
    assert cmp["shared_terms"] == [] and cmp["distinct_a"] == [] and cmp["distinct_b"] == []
    assert "budget" not in str(cmp) and "secret" not in str(cmp)
    # ask/summarize produce body-derived answers + verbatim quotes → disabled under redact
    ask = server._tool_ask("what about the budget?")
    assert ask["answered"] is False and ask["citations"] == [] and ask["passages"] == []
    assert "Disabled" in ask["answer"] and "secret" not in str(ask)


def test_dm_body_never_crosses_tool_boundary(api):
    """Always-on at-rest DM redaction must hold through the MCP tool surface — independent of
    ACTIONPULSE_MCP_REDACT_BODIES, a DM body must never appear in get_message/search output.
    Closes the #156 gap: the redaction tests had only ever seeded email bodies, so a DM-body
    leak through the tool serializers would have passed unnoticed."""
    dm = NormalizedMessage(
        msg_id="dm1",
        conversation_id="d-1",
        datetime_received=_d(2),
        sender_email="alice@corp",
        subject="",
        text_body="private counterparty secret text",
        to_recipients=["me@corp"],
        source="mm",
        mm_channel_type="D",
    )
    api.store.upsert_messages([dm])
    rec = server._tool_get_message("urn:mm:dm1")
    assert rec is not None  # the message exists…
    assert "counterparty" not in str(rec) and "secret" not in str(rec)  # …but its body doesn't
    # DM bodies create no chunk rows at rest → keyword/semantic search cannot surface the text.
    res = server._tool_search("counterparty")
    assert "counterparty" not in str(res) and "secret" not in str(res)


def test_ask_tool_empty_store_no_gateway(api):
    res = server._tool_ask("anything at all")
    assert res["answered"] is False
    assert "raw" not in res  # internal verdict stripped


def test_no_tool_accepts_a_key_parameter():
    for fn, _name in server._TOOLS + server._SOURCE_TOOLS + server._MAINTENANCE_TOOLS:
        params = inspect.signature(fn).parameters
        assert not any("key" in p.lower() for p in params), fn  # key comes from ENV only


def test_source_tools_delegate(api, monkeypatch):
    # list_containers (EWS) reports the configured folders without a network call
    api._config.ews.folders = ["Inbox", "Sent"]
    assert {c["name"] for c in server._tool_list_containers("ews")} == {"Inbox", "Sent"}
    # fetch_source maps live NormalizedMessages → record dicts (no persistence)
    fake = [_msg("live@corp", "fresh budget memo", to=["me@corp"])]
    monkeypatch.setattr(
        api, "_source_adapter", lambda source: type("A", (), {"fetch": lambda s, d: fake})()
    )
    recs = server._tool_fetch_source("ews", "2026-06-19")
    assert recs[0]["message_id"] == "urn:email:live@corp" and recs[0]["subject"] == "S"
    assert api.store.stats()["messages"] == 0  # fetch_source does NOT write the store


@pytest.mark.skipif(not HAS_MCP, reason="mcp extra not installed")
def test_build_app_registers_read_reason_and_source_tools():
    import asyncio

    app = server._build_app()
    tools = {t.name for t in asyncio.run(app.list_tools())}
    assert {"search", "ask", "compare", "open_loops", "pending", "get_message"} <= tools
    assert "history" in tools  # cross-digest history is a first-class tool (C1 facade parity)
    assert {"list_containers", "get_reactions"} <= tools  # corp source reads always on
    assert len(tools) == 20  # 18 read/reason + 2 source; maintenance + fetch OFF
    assert "sweep_ttl" not in tools and "fetch_source" not in tools


@pytest.mark.skipif(not HAS_MCP, reason="mcp extra not installed")
def test_gated_tools_only_behind_flags(monkeypatch):
    import asyncio

    monkeypatch.setenv("ACTIONPULSE_MCP_ENABLE_MAINTENANCE", "1")
    monkeypatch.setenv("ACTIONPULSE_MCP_ENABLE_FETCH", "1")
    app = server._build_app()
    tools = {t.name for t in asyncio.run(app.list_tools())}
    assert {"sweep_ttl", "embed_backlog", "reembed", "vacuum", "fetch_source"} <= tools
    assert len(tools) == 25  # 20 + 4 maintenance + fetch_source
