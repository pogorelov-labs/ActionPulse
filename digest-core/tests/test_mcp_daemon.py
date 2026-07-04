"""MCP daemon tools: daemon_status always on (read-only), trigger_ingest fetch-gated."""

from __future__ import annotations

import pytest

from digest_core.mcp import server

try:
    import mcp.server.fastmcp  # noqa: F401

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def test_daemon_status_body_is_content_free(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    out = server._tool_daemon_status()
    # metadata only — safe to always expose (no bodies, no secrets)
    assert set(out).issuperset({"installed", "is_stale", "staleness_days"})
    assert out["installed"] in (True, False)


@pytest.mark.skipif(not HAS_MCP, reason="mcp extra not installed")
def test_daemon_status_registered_and_trigger_ingest_gated(monkeypatch):
    import asyncio

    app = server._build_app()
    tools = {t.name for t in asyncio.run(app.list_tools())}
    assert "daemon_status" in tools  # always on
    assert "trigger_ingest" not in tools  # persisting tick stays behind the flag

    monkeypatch.setenv("ACTIONPULSE_MCP_ENABLE_FETCH", "1")
    gated = {t.name for t in asyncio.run(server._build_app().list_tools())}
    assert "trigger_ingest" in gated


@pytest.mark.skipif(not HAS_MCP, reason="mcp extra not installed")
def test_trigger_ingest_routes_through_tick(monkeypatch):
    from digest_core.daemon import tick

    captured = {}

    def _fake_ingest(sources=None, **kw):
        captured["sources"] = sources
        return tick.TickResult(ok=True, sources_ingested=sources or [], messages_added=2)

    monkeypatch.setattr(tick, "ingest_once", _fake_ingest)
    out = server._tool_trigger_ingest(sources="mm,ews")
    assert captured["sources"] == ["mm", "ews"]
    assert out["ok"] is True and out["messages_added"] == 2
