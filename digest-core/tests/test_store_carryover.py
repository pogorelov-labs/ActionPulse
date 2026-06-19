"""Store-derived 'Open loops' carryover section (P3 memory pillar). Needs the store extra."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from digest_core import run as runner
from digest_core.config import Config, StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.llm.schemas import Digest, Section
from digest_core.store import HAS_SQLCIPHER, MessageStore
from digest_core.store.carryover import find_open_loops

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


def _d(day, hour=12):
    return datetime(2026, 6, day, hour, 0, tzinfo=timezone.utc)


def _msg(
    msg_id,
    *,
    thread,
    to,
    when,
    sender="ivan@corp",
    source="email",
    ctype=None,
    subject="Subj",
    author="Ivan",
):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=thread,
        subject=subject,
        text_body="body text",
        sender_email=sender,
        from_name=author,
        datetime_received=when,
        to_recipients=to,
        cc_recipients=[],
        source=source,
        mm_channel_type=ctype,
    )


def _open(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    return MessageStore.open(StoreConfig(db_path=str(tmp_path / "m.db")))


def test_find_open_loops_selects_only_stale_addressed_non_dm(tmp_path, monkeypatch):
    with _open(tmp_path, monkeypatch) as store:
        store.upsert_messages(
            [
                _msg("t1", thread="T1", to=["me@corp"], when=_d(6)),  # stale + addressed → loop
                _msg("t2a", thread="T2", to=["me@corp"], when=_d(7)),  # addressed…
                _msg(
                    "t2b", thread="T2", to=["other@corp"], when=_d(10)
                ),  # …but thread active today
                _msg("t3", thread="T3", to=["other@corp"], when=_d(6)),  # not addressed to me
                _msg(
                    "dm", thread="DM1", to=["me@corp"], when=_d(6), source="mm", ctype="D"
                ),  # DM excluded
            ]
        )
        loops = find_open_loops(
            store.conn, user_aliases=["me@corp"], now=_d(10), lookback_days=7, stale_days=2
        )
    assert {loop.thread_id for loop in loops} == {"T1"}
    assert loops[0].age_days == 4 and loops[0].author == "Ivan" and loops[0].source == "email"


def test_find_open_loops_uses_local_day_boundary(tmp_path, monkeypatch):
    """A message early on the digest's LOCAL day (already 'yesterday' in UTC) is part of
    today — not a prior-day open loop. Regression for the UTC-midnight today_start bug."""
    from zoneinfo import ZoneInfo

    moscow = ZoneInfo("Europe/Moscow")  # UTC+3
    with _open(tmp_path, monkeypatch) as store:
        # 01:00 on 2026-06-19 Moscow == 22:00 on 2026-06-18 UTC.
        early_local = datetime(2026, 6, 19, 1, 0, tzinfo=moscow)
        store.upsert_messages([_msg("t1", thread="T1", to=["me@corp"], when=early_local)])
        # Reference instant = end of the local digest day; stale_days=0 removes staleness.
        now = datetime(2026, 6, 19, 23, 59, 59, tzinfo=moscow)
        loops = find_open_loops(store.conn, user_aliases=["me@corp"], now=now, stale_days=0)
    assert loops == []  # part of today (local), so not surfaced as a cross-day loop


def test_find_open_loops_excludes_when_owner_replied_last(tmp_path, monkeypatch):
    with _open(tmp_path, monkeypatch) as store:
        store.upsert_messages(
            [
                _msg("a", thread="T1", to=["me@corp"], when=_d(6)),  # addressed to me
                _msg(
                    "b", thread="T1", to=["ivan@corp"], when=_d(7), sender="me@corp"
                ),  # I replied last
            ]
        )
        loops = find_open_loops(store.conn, user_aliases=["me@corp"], now=_d(10), stale_days=2)
    assert loops == []  # ball is in their court → not an open loop


def test_find_open_loops_empty_without_aliases(tmp_path, monkeypatch):
    with _open(tmp_path, monkeypatch) as store:
        store.upsert_messages([_msg("t1", thread="T1", to=["me@corp"], when=_d(6))])
        assert find_open_loops(store.conn, user_aliases=[], now=_d(10)) == []


def _ctx(tmp_path, *, carryover=True, enabled=True):
    cfg = Config()
    cfg.store.enabled = enabled
    cfg.store.carryover = carryover
    cfg.store.db_path = str(tmp_path / "m.db")
    cfg.ews.user_aliases = ["me@corp"]
    return SimpleNamespace(config=cfg, digest_date="2026-06-10", trace_id="t-1", run_meta={})


def _digest():
    return Digest(
        prompt_version="x",
        digest_date="2026-06-10",
        trace_id="t-1",
        sections=[Section(title="My actions", items=[])],
    )


def test_enrich_digest_appends_open_loops_section(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    ctx = _ctx(tmp_path)
    with MessageStore.open(ctx.config.store) as store:
        store.upsert_messages([_msg("t1", thread="T1", to=["me@corp"], when=_d(6))])

    digest = _digest()
    runner._enrich_digest_from_store(ctx, digest)

    carry = [s for s in digest.sections if "Open loops" in s.title]
    assert carry and carry[0].items
    item = carry[0].items[0]
    assert item.source_ref["type"] == "carryover" and item.source_ref["conversation_id"] == "T1"
    assert "Awaiting you" in item.title
    assert ctx.run_meta["carryover_items"] == 1


def test_enrich_digest_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    ctx = _ctx(tmp_path, carryover=False)
    with MessageStore.open(ctx.config.store) as store:
        store.upsert_messages([_msg("t1", thread="T1", to=["me@corp"], when=_d(6))])

    digest = _digest()
    runner._enrich_digest_from_store(ctx, digest)
    assert [s.title for s in digest.sections] == ["My actions"]
    assert "carryover_items" not in ctx.run_meta


def test_enrich_digest_non_fatal_on_store_error(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    class _Boom:
        @classmethod
        def open(cls, config=None):
            raise RuntimeError("disk full")

    # The enrich path now reads through InboxAPI; a failure to open must degrade-not-drop.
    monkeypatch.setattr("digest_core.api.InboxAPI", _Boom)
    digest = _digest()
    runner._enrich_digest_from_store(ctx, digest)  # must NOT raise
    assert [s.title for s in digest.sections] == ["My actions"]
