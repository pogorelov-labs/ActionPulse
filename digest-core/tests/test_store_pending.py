"""Store-derived 'Awaiting your reply' pending-request detection. Needs the store extra."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from digest_core import run as runner
from digest_core.config import Config, StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.llm.schemas import Digest, Section
from digest_core.store import HAS_SQLCIPHER, MessageStore
from digest_core.store.pending import classify_ask, find_pending_requests

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


def _d(day, hour=12):
    return datetime(2026, 6, day, hour, 0, tzinfo=timezone.utc)


def _msg(
    msg_id,
    *,
    thread,
    to,
    when,
    body="hello",
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
        text_body=body,
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


def test_classify_ask_kinds():
    assert classify_ask("", "Please approve the budget") == "approval"
    assert classify_ask("Re: согласование", "прошу согласовать смету") == "approval"
    assert classify_ask("", "What do you think about X?") == "question"
    assert classify_ask("", "Подскажите, когда дедлайн?") == "question"
    assert classify_ask("", "Please send the file") == "request"
    assert classify_ask("", "FYI, the report is attached.") is None


def test_find_pending_uses_local_day_boundary(tmp_path, monkeypatch):
    """An ask early on the digest's LOCAL day (already 'yesterday' in UTC) belongs to today,
    not the prior-day pending list. Regression for the UTC-midnight today_start bug."""
    from zoneinfo import ZoneInfo

    moscow = ZoneInfo("Europe/Moscow")  # UTC+3
    with _open(tmp_path, monkeypatch) as store:
        # 01:00 on 2026-06-19 Moscow == 22:00 on 2026-06-18 UTC.
        early_local = datetime(2026, 6, 19, 1, 0, tzinfo=moscow)
        store.upsert_messages(
            [_msg("p1", thread="A", to=["me@corp"], when=early_local, body="Please approve")]
        )
        now = datetime(2026, 6, 19, 23, 59, 59, tzinfo=moscow)
        pend = find_pending_requests(store.conn, user_aliases=["me@corp"], now=now)
    assert pend == []  # part of today (local), so not yet "pending from a prior day"


def test_find_pending_basic_and_exclusions(tmp_path, monkeypatch):
    with _open(tmp_path, monkeypatch) as store:
        store.upsert_messages(
            [
                _msg("p1", thread="A", to=["me@corp"], when=_d(6), body="Please approve the plan"),
                _msg("q1", thread="B", to=["me@corp"], when=_d(6), body="Can you confirm? wdyt"),
                _msg("ask", thread="C", to=["me@corp"], when=_d(6), body="Please approve"),
                # …but I replied in C afterwards → answered
                _msg(
                    "mine", thread="C", to=["ivan@corp"], when=_d(7), sender="me@corp", body="done"
                ),
                _msg("noask", thread="D", to=["me@corp"], when=_d(6), body="FYI attached"),
                _msg("notme", thread="E", to=["x@corp"], when=_d(6), body="Please approve"),
                _msg(
                    "dm",
                    thread="F",
                    to=["me@corp"],
                    when=_d(6),
                    body="Please approve",
                    source="mm",
                    ctype="D",
                ),
                _msg(
                    "own",
                    thread="G",
                    to=["ivan@corp"],
                    when=_d(6),
                    sender="me@corp",
                    body="Please approve",
                ),
            ]
        )
        out = find_pending_requests(store.conn, user_aliases=["me@corp"], now=_d(10))
    assert {p.thread_id for p in out} == {"A", "B"}
    kinds = {p.thread_id: p.kind for p in out}
    assert kinds == {"A": "approval", "B": "question"}
    # approvals sort before questions
    assert out[0].thread_id == "A"


def test_find_pending_dedup_newest_ask_per_thread(tmp_path, monkeypatch):
    with _open(tmp_path, monkeypatch) as store:
        store.upsert_messages(
            [
                _msg("old", thread="A", to=["me@corp"], when=_d(5), body="Please approve v1"),
                _msg(
                    "new",
                    thread="A",
                    to=["me@corp"],
                    when=_d(7),
                    body="Any update? please approve v2",
                ),
            ]
        )
        out = find_pending_requests(store.conn, user_aliases=["me@corp"], now=_d(10))
    assert len(out) == 1 and out[0].asked_msg_id.endswith("new")  # store id is the URN


def _ctx(tmp_path, *, pending=True, carryover=True, enabled=True):
    cfg = Config()
    cfg.store.enabled = enabled
    cfg.store.pending = pending
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


def test_enrich_pending_only(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    ctx = _ctx(tmp_path, carryover=False)
    with MessageStore.open(ctx.config.store) as store:
        store.upsert_messages(
            [_msg("p1", thread="A", to=["me@corp"], when=_d(6), body="Please approve")]
        )
    digest = _digest()
    runner._enrich_digest_from_store(ctx, digest)

    titles = [s.title for s in digest.sections]
    assert "Awaiting your reply" in titles and "Open loops" not in titles
    pend = [s for s in digest.sections if s.title == "Awaiting your reply"][0]
    assert pend.items[0].source_ref["type"] == "pending"
    assert pend.items[0].source_ref["kind"] == "approval"
    assert ctx.run_meta["pending_items"] == 1


def test_enrich_pending_dedups_carryover(tmp_path, monkeypatch):
    # Thread A is an unanswered approval (pending) that has ALSO gone quiet
    # (carryover-eligible). Pending wins; it must not also appear as an Open loop.
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    ctx = _ctx(tmp_path)  # pending + carryover both on
    with MessageStore.open(ctx.config.store) as store:
        store.upsert_messages(
            [_msg("p1", thread="A", to=["me@corp"], when=_d(6), body="Please approve")]
        )
    digest = _digest()
    runner._enrich_digest_from_store(ctx, digest)

    titles = [s.title for s in digest.sections]
    assert "Awaiting your reply" in titles
    assert "Open loops" not in titles  # deduped out
    assert ctx.run_meta["pending_items"] == 1
    assert "carryover_items" not in ctx.run_meta
