"""The `health` tool — the one tool that must answer when nothing else can.

Every other tool on this server is a read projection over the encrypted store, so a
single missing precondition (driver / key / enabled / data) makes ~20 tools fail
*identically*. `health` exists to say WHICH link is broken, which means its own
contract is unusually strict: **it must never raise**, including — especially —
when opening the store is exactly what fails.

Each test below breaks one link and asserts health still returns, names the problem,
and offers a fix. That is the property; the wording is not.
"""

from __future__ import annotations

import pytest

from digest_core.config import Config
from digest_core.mcp import server
from digest_core.store import HAS_SQLCIPHER

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No cached API and no inherited key — health must read the world as it is."""
    monkeypatch.setattr(server, "_api", None)
    monkeypatch.delenv("DIGEST_STORE_KEY", raising=False)
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    yield
    server._api = None


def _blocker_text(health) -> str:
    return " ".join(b["problem"] + " " + b["fix"] for b in health["blockers"])


def test_health_reports_missing_key_without_raising(monkeypatch):
    """The commonest first-run state: extras installed, `store init` never run."""
    health = server._tool_health()  # must not raise
    assert health["ok"] is False
    assert health["store"]["key_set"] is False
    assert "DIGEST_STORE_KEY" in _blocker_text(health)
    assert "store init" in _blocker_text(health)


def test_health_reports_disabled_store_as_a_freshness_problem(monkeypatch, tmp_path):
    """`store.enabled: false` does NOT stop reads (the read path never checks it).

    So health must describe it as "nothing new is being ingested", not "broken" —
    saying otherwise would send someone chasing a fault that isn't there.
    """
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    monkeypatch.setenv("DIGEST_STORE_ENABLED", "0")
    health = server._tool_health()
    assert health["store"]["enabled"] is False
    text = _blocker_text(health)
    assert "ingest" in text.lower()
    # It opened fine despite being "disabled" — that asymmetry is the point.
    assert health["store"]["openable"] is True
    assert health["ok"] is True


def test_health_survives_an_unopenable_store(monkeypatch, tmp_path):
    """A wrong/rotated key is the case where _get_api() itself throws.

    health probes deliberately outside the cached _get_api, so this must be reported
    rather than propagated.
    """
    db = tmp_path / "not-a-db.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"this is definitely not a SQLCipher database" * 8)
    monkeypatch.setenv("DIGEST_STORE_KEY", "cd" * 32)
    monkeypatch.setenv("DIGEST_STORE_DB_PATH", str(db))
    assert Config().store.resolved_db_path() == str(db), "the override must actually bind"

    health = server._tool_health()  # must not raise
    assert health["ok"] is False
    assert health["store"]["openable"] is False
    assert "open_error" in health["store"]
    assert "DIGEST_STORE_KEY" in _blocker_text(health)


def test_health_flags_an_empty_store_as_a_trap(monkeypatch, tmp_path):
    """An empty store answers every query with "nothing" — indistinguishable from
    "no matches" unless something says so. That is a no-silent-caps issue."""
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    health = server._tool_health()
    assert health["ok"] is True  # it CAN serve...
    assert health["can_serve_content"] is False  # ...but has nothing to serve
    assert "EMPTY" in _blocker_text(health) or "empty" in _blocker_text(health)


def test_health_never_raises_even_if_everything_underneath_does(monkeypatch):
    """The contract, stated as a test: no exception from any dependency escapes.

    Config, the store probe and daemon status are each made to explode; health still
    has to return a dict a client can read.
    """

    def boom(*a, **k):
        raise RuntimeError("everything is on fire")

    monkeypatch.setattr(server, "_tool_daemon_status", boom)
    monkeypatch.setattr("digest_core.store.db.MessageStore.open", boom)
    health = server._tool_health()
    assert isinstance(health, dict)
    assert health["ok"] is False
    assert "blockers" in health


def test_health_leaks_no_message_content(monkeypatch, tmp_path):
    """health is metadata-only, so it is safe to call under REDACT_BODIES.

    It reports counts/paths/flags; if it ever grew a body/subject field it would
    become a redaction bypass, since it is the one tool an agent always calls.
    """
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    monkeypatch.setenv("ACTIONPULSE_MCP_REDACT_BODIES", "1")
    from digest_core.api import InboxAPI
    from digest_core.ingest.ews import NormalizedMessage
    from datetime import datetime, timezone

    cfg = Config()
    cfg.store.db_path = str(tmp_path / "m.db")
    api = InboxAPI.open(cfg)
    try:
        api.store.upsert_messages(
            [
                NormalizedMessage(
                    msg_id="a@corp",
                    conversation_id="T",
                    datetime_received=datetime(2026, 6, 1, tzinfo=timezone.utc),
                    sender_email="ivan@corp",
                    subject="SECRET-SUBJECT",
                    text_body="SECRET-BODY-TEXT",
                )
            ]
        )
        monkeypatch.setattr(server, "_api", api)
        blob = repr(server._tool_health())
    finally:
        api.close()
    assert "SECRET-BODY-TEXT" not in blob
    assert "SECRET-SUBJECT" not in blob
    assert server._tool_health()["exposure"]["redact_bodies"] is True


def test_health_is_registered_and_listed_first():
    """Registration order is deliberate: an agent scanning tools/list should meet
    `health` before the 20 tools that can fail for one shared reason."""
    names = [name for _, name in server._TOOLS]
    assert names[0] == "health"
