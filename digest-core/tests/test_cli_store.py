"""CLI: `search` + the `store` sub-app (PR5).

`store init` needs no driver; the rest require the `store` extra (skipped otherwise).
Isolation: ACTIONPULSE_HOME + DIGEST_STORE_* env so the DB lands under tmp_path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from digest_core.cli import app
from digest_core.config import StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER, MessageStore

runner = CliRunner()
needs_driver = pytest.mark.skipif(
    not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)"
)


def _msg(msg_id, body, source="email"):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c-" + msg_id,
        subject="Subject",
        text_body=body,
        sender_email="ivan@corp",
        datetime_received=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        source=source,
    )


def _enable(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    monkeypatch.setenv("DIGEST_STORE_ENABLED", "1")
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)


def _seed(messages):
    with MessageStore.open(StoreConfig()) as store:
        store.upsert_messages(messages)


def test_store_init_generates_and_detects_key(tmp_path, monkeypatch):
    envp = tmp_path / "env"
    monkeypatch.setattr("digest_core.ui.menu.ENV_PATH", envp)
    monkeypatch.delenv("DIGEST_STORE_KEY", raising=False)

    r = runner.invoke(app, ["store", "init"])
    assert r.exit_code == 0
    assert "DIGEST_STORE_KEY=" in envp.read_text(encoding="utf-8")
    assert oct(envp.stat().st_mode)[-3:] == "600"

    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    r2 = runner.invoke(app, ["store", "init"])
    assert r2.exit_code == 0 and "already set" in r2.output


@needs_driver
def test_search_reports_when_store_off(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    monkeypatch.setenv("DIGEST_STORE_ENABLED", "0")
    r = runner.invoke(app, ["search", "anything"])
    assert r.exit_code == 1
    assert "store is off" in r.output.lower()


@needs_driver
def test_search_keyword_stats_json(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    _seed([_msg("a@corp", "please approve the budget"), _msg("b@corp", "release notes")])

    r = runner.invoke(app, ["search", "budget", "--keyword"])
    assert r.exit_code == 0 and "budget" in r.output.lower()

    rj = runner.invoke(app, ["search", "budget", "--keyword", "--json"])
    assert rj.exit_code == 0
    data = json.loads(rj.output)
    assert data and data[0]["message_id"] == "urn:email:a@corp"

    rs = runner.invoke(app, ["store", "stats"])
    assert rs.exit_code == 0 and "messages" in rs.output and "email=2" in rs.output


@needs_driver
def test_semantic_search_without_backend_errors_cleanly(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    _seed([_msg("a@corp", "budget")])
    # No corp network in tests → building the embeddings backend / calling it fails;
    # the command must exit 1 with a clear message, not a traceback.
    r = runner.invoke(app, ["search", "budget", "--semantic"])
    assert r.exit_code == 1


@needs_driver
def test_store_purge_and_drop(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    _seed([_msg("a@corp", "old message")])  # dated 2026-06-01, well in the past

    rp = runner.invoke(app, ["store", "purge", "--ttl-days", "1", "--yes"])
    assert rp.exit_code == 0 and "Purged 1" in rp.output

    rd = runner.invoke(app, ["store", "drop", "--yes"])
    assert rd.exit_code == 0 and "Deleted" in rd.output
    assert not (tmp_path / "var" / "store" / "messages.db").exists()
