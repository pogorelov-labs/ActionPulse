"""Direct retrieval primitives over the store. Requires the `store` extra."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.config import StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER, MessageStore
from digest_core.store.models import DM_AT_REST_REDACTION

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


class FakeEmbed:
    """Keyword-axis embeddings: cosine=1 when the text shares a topic word."""

    AXES = ("budget", "release", "vacation")

    def embed(self, texts):
        out = []
        for t in texts:
            tl = (t or "").lower()
            vec = [1.0 if ax in tl else 0.0 for ax in self.AXES]
            out.append(vec if any(vec) else [0.1, 0.1, 0.1])
        return out


def _cfg(tmp_path, monkeypatch, **over):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    return StoreConfig(db_path=str(tmp_path / "messages.db"), **over)


def _d(day):
    return datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc)


def _msg(
    msg_id,
    body,
    *,
    thread=None,
    subject="Subject",
    sender="ivan@corp",
    name="Ivan",
    when=None,
    source="email",
    mm_channel_type=None,
    to=None,
    attach=None,
):
    if source == "mm" and mm_channel_type is None:
        mm_channel_type = "O"
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=thread or ("c-" + msg_id),
        datetime_received=when or _d(1),
        sender_email=sender,
        from_name=name,
        subject=subject,
        text_body=body,
        source=source,
        mm_channel_type=mm_channel_type,
        to_recipients=to or [],
        has_attachments=bool(attach),
        attachment_types=attach or [],
    )


def _seed(store):
    store.upsert_messages(
        [
            _msg("m1@corp", "kickoff", thread="T1", subject="Project", when=_d(1), to=["me@corp"]),
            _msg(
                "m2@corp",
                "reply",
                thread="T1",
                subject="Project",
                sender="boss@corp",
                name="Boss",
                when=_d(3),
            ),
            _msg(
                "m3@corp",
                "channel post",
                source="mm",
                sender="alice@corp",
                name="Alice",
                when=_d(5),
                attach=["pdf"],
            ),
            _msg(
                "dm1@corp",
                "secret colleague text",
                source="mm",
                mm_channel_type="D",
                sender="carol@corp",
                name="Carol",
                when=_d(2),
            ),
        ]
    )


def test_get_message_and_missing(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        rec = store.get_message("urn:email:m1@corp")
        assert rec is not None
        assert rec.subject == "Project" and rec.author_email == "ivan@corp"
        assert rec.to_recipients == ["me@corp"] and rec.thread_id == "T1"
        assert store.get_message("urn:email:nope@corp") is None


def test_dm_body_is_redaction_marker(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        rec = store.get_message("urn:mm:dm1@corp")
        assert rec is not None and rec.mm_channel_type == "D"
        assert rec.body == DM_AT_REST_REDACTION  # never colleague text
        assert "secret colleague" not in rec.body


def test_get_thread_ordered(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        thread = store.get_thread("T1")
        assert [r.message_id for r in thread] == ["urn:email:m1@corp", "urn:email:m2@corp"]


def test_list_recent_and_source_filter(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        recent = store.list_recent(limit=2)
        assert recent[0].message_id == "urn:mm:m3@corp"  # newest (day 5)
        mm_only = store.list_recent(source="mm")
        assert {r.source for r in mm_only} == {"mm"}
        assert {r.message_id for r in mm_only} == {"urn:mm:m3@corp", "urn:mm:dm1@corp"}
        assert store.get_message("urn:mm:m3@corp").has_attachments is True
        assert store.get_message("urn:mm:m3@corp").attachment_types == ["pdf"]


def test_list_by_sender_case_insensitive(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        hits = store.list_by_sender("BOSS@CORP")
        assert {r.message_id for r in hits} == {"urn:email:m2@corp"}


def test_list_by_date_range_inclusive(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        got = store.list_by_date_range("2026-06-01", "2026-06-03")  # days 1-3 inclusive
        assert {r.message_id for r in got} == {
            "urn:email:m1@corp",
            "urn:mm:dm1@corp",
            "urn:email:m2@corp",
        }


def test_list_threads_summary(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        threads = store.list_threads()
        by_id = {t.thread_id: t for t in threads}
        assert by_id["T1"].message_count == 2
        assert by_id["T1"].last_author == "Boss"  # latest message in T1 is from Boss
        assert threads[0].thread_id == "c-m3@corp"  # most-recently-active first (day 5)


def test_count_by_sender_and_day(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        counts = {s.email: s.count for s in store.count_by_sender()}
        assert counts == {"ivan@corp": 1, "boss@corp": 1, "alice@corp": 1, "carol@corp": 1}
        days = store.count_by_day(days=30, now=_d(6))
        assert {d.day: d.count for d in days} == {
            "2026-06-01": 1,
            "2026-06-02": 1,
            "2026-06-03": 1,
            "2026-06-05": 1,
        }


def test_related_excludes_self_and_ranks(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages(
            [
                _msg("b1@corp", "approve the quarterly budget"),
                _msg("b2@corp", "the budget review meeting"),
                _msg("r1@corp", "the release is tomorrow"),
            ]
        )
        store.embed_backlog(FakeEmbed())
        hits = store.related("urn:email:b1@corp", limit=5)
        ids = [h.message_id for h in hits]
        assert "urn:email:b1@corp" not in ids  # excludes itself
        assert ids and ids[0] == "urn:email:b2@corp"  # same budget axis ranks first
