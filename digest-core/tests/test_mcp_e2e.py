"""Real MCP protocol round-trip (in-memory client+server) — exercises the wire path
(initialize → list_tools → call_tool → read_resource → get_prompt) that the unit tests,
which call tool bodies directly, never touch. Needs the store + mcp extras.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.mcp import server
from digest_core.store import HAS_SQLCIPHER

try:
    from mcp.shared.memory import create_connected_server_and_client_session

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(
    not (HAS_SQLCIPHER and HAS_MCP), reason="needs the store + mcp extras"
)


def _msg(msg_id, body, *, thread=None):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=thread or ("c-" + msg_id),
        datetime_received=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        sender_email="ivan@corp",
        subject="Subj",
        text_body=body,
    )


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    from digest_core.api import InboxAPI

    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    cfg = Config()
    cfg.store.db_path = str(tmp_path / "m.db")
    a = InboxAPI.open(cfg)
    a.store.upsert_messages([_msg("a@corp", "the quarterly budget", thread="T")])
    monkeypatch.setattr(server, "_api", a)
    yield a
    a.close()


@pytest.mark.asyncio
async def test_real_protocol_roundtrip(seeded):
    app = server._build_app()
    async with create_connected_server_and_client_session(app) as client:
        await client.initialize()

        names = {t.name for t in (await client.list_tools()).tools}
        assert {"search", "ask", "compare", "stats", "get_message", "open_loops"} <= names

        stats = await client.call_tool("stats", {})
        assert stats.isError is False

        found = await client.call_tool("search", {"query": "budget"})
        assert found.isError is False
        assert "a@corp" in str(found.content) or "budget" in str(found.content).lower()

        # a gateway tool off-corp returns a CLEAN error envelope (isError=True), not a
        # crashed stdio pipe — the retrieved passage triggers the gateway, which fails offline.
        asked = await client.call_tool("ask", {"question": "the budget?"})
        assert asked.isError is True and "gateway" in str(asked.content).lower()

        prompt = await client.get_prompt("inbox_triage", {})
        assert prompt.messages
