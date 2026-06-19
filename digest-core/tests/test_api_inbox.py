"""InboxAPI facade — offline verbs, insight parity, gateway seam. Needs the store extra."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.api import ApiError, GatewayUnavailable, InboxAPI
from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


def _config(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    cfg = Config()
    cfg.store.db_path = str(tmp_path / "m.db")
    cfg.ews.user_aliases = ["me@corp"]
    return cfg


def _d(day):
    return datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc)


def _msg(msg_id, body, *, thread=None, subject="S", sender="ivan@corp", when=None, to=None):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=thread or ("c-" + msg_id),
        datetime_received=when or _d(1),
        sender_email=sender,
        from_name="Ivan",
        subject=subject,
        text_body=body,
        to_recipients=to or [],
    )


def test_open_and_retrieve_delegate(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api.store.upsert_messages([_msg("a@corp", "the budget", thread="T", when=_d(2))])
        assert api.get_message("urn:email:a@corp").subject == "S"
        assert api.list_recent()[0].message_id == "urn:email:a@corp"
        assert [r.message_id for r in api.get_thread("T")] == ["urn:email:a@corp"]
        assert api.stats()["messages"] == 1
        assert api.list_threads()[0].thread_id == "T"
        assert {s.email for s in api.count_by_sender()} == {"ivan@corp"}
        assert api.timeline(days=30) == api.count_by_day(days=30)


def test_keyword_offline_semantic_degrades(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api.store.upsert_messages([_msg("a@corp", "approve the budget")])
        assert {h.message_id for h in api.search("budget")} == {"urn:email:a@corp"}
        # No gateway here → semantic/hybrid degrade to keyword (served method is visible).
        degraded = api.search("budget", mode="semantic")
        assert {h.message_id for h in degraded} == {"urn:email:a@corp"}
        assert all(h.provenance["method"] == "keyword" for h in degraded)
        # strict=True surfaces the failure instead of silently degrading.
        with pytest.raises(GatewayUnavailable):
            api.search("budget", mode="hybrid", strict=True)


def test_insight_parity(tmp_path, monkeypatch):
    from digest_core.store.carryover import find_open_loops
    from digest_core.store.pending import find_pending_requests

    cfg = _config(tmp_path, monkeypatch)
    now = _d(10)
    with InboxAPI.open(cfg) as api:
        api.store.upsert_messages(
            [
                _msg("loop@corp", "fyi update", thread="L1", when=_d(5), to=["me@corp"]),
                _msg("ask@corp", "please approve", thread="P1", when=_d(5), to=["me@corp"]),
            ]
        )
        loops = api.open_loops(now=now)
        pend = api.pending(now=now)
        direct_loops = find_open_loops(api.store.conn, user_aliases=["me@corp"], now=now)
        direct_pend = find_pending_requests(api.store.conn, user_aliases=["me@corp"], now=now)

    assert [x.thread_id for x in loops] == [x.thread_id for x in direct_loops]
    assert [x.thread_id for x in pend] == [x.thread_id for x in direct_pend]
    assert {x.thread_id for x in pend} == {"P1"}  # the ask is pending


def test_open_raises_apierror_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DIGEST_STORE_KEY", raising=False)
    cfg = Config()
    cfg.store.db_path = str(tmp_path / "m.db")
    with pytest.raises(ApiError):
        InboxAPI.open(cfg)


def test_config_user_aliases_dedupes_upn():
    cfg = Config()
    cfg.ews.user_aliases = ["Name", "n@corp"]
    cfg.ews.user_upn = "n@corp"
    assert cfg.user_aliases() == ["Name", "n@corp"]  # upn already present → not duplicated
    cfg.ews.user_upn = "upn@corp"
    assert cfg.user_aliases() == ["Name", "n@corp", "upn@corp"]


def test_context_manager_closes_store(tmp_path, monkeypatch):
    api = InboxAPI.open(_config(tmp_path, monkeypatch))
    api.close()
    with pytest.raises(Exception):
        api.store.conn.execute("SELECT 1")  # connection closed
