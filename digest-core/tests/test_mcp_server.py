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
    assert server._tool_search("budget")[0]["message_id"] == "urn:email:a@corp"
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


def test_redact_bodies_flag(api, monkeypatch):
    api.store.upsert_messages([_msg("a@corp", "secret body text here")])
    monkeypatch.setenv("ACTIONPULSE_MCP_REDACT_BODIES", "1")
    rec = server._tool_get_message("urn:email:a@corp")
    assert rec["body"] == "" and "secret" not in str(rec)


def test_ask_tool_empty_store_no_gateway(api):
    res = server._tool_ask("anything at all")
    assert res["answered"] is False
    assert "raw" not in res  # internal verdict stripped


def test_no_tool_accepts_a_key_parameter():
    for fn, _name in server._TOOLS + server._MAINTENANCE_TOOLS:
        params = inspect.signature(fn).parameters
        assert not any("key" in p.lower() for p in params), fn  # key comes from ENV only


@pytest.mark.skipif(not HAS_MCP, reason="mcp extra not installed")
def test_build_app_registers_read_and_reason_tools():
    import asyncio

    app = server._build_app()
    tools = {t.name for t in asyncio.run(app.list_tools())}
    assert {"search", "ask", "compare", "open_loops", "pending", "get_message"} <= tools
    assert len(tools) == 17  # maintenance OFF by default
    assert "sweep_ttl" not in tools


@pytest.mark.skipif(not HAS_MCP, reason="mcp extra not installed")
def test_maintenance_tools_only_behind_flag(monkeypatch):
    import asyncio

    monkeypatch.setenv("ACTIONPULSE_MCP_ENABLE_MAINTENANCE", "1")
    app = server._build_app()
    tools = {t.name for t in asyncio.run(app.list_tools())}
    assert {"sweep_ttl", "embed_backlog", "reembed", "vacuum"} <= tools
    assert len(tools) == 21
