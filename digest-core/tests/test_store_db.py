"""Encrypted store tests (require the `store` extra; skipped otherwise).

Offline: a temp encrypted DB + synthetic NormalizedMessages, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from digest_core.config import StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER, MessageStore, StoreError

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")

_KEY = "ab" * 32  # 64 hex chars → raw key


def _cfg(tmp_path, monkeypatch, **over):
    monkeypatch.setenv("DIGEST_STORE_KEY", over.pop("key", _KEY))
    return StoreConfig(db_path=str(tmp_path / "messages.db"), **over)


def _msg(msg_id, body, *, when=None, subject="Subject", source="email", conv="c1"):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=conv,
        datetime_received=when or datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        sender_email="ivan@corp",
        subject=subject,
        text_body=body,
        source=source,
    )


def test_open_creates_all_tables(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        names = {
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            ).fetchall()
        }
    assert {"messages", "messages_fts", "chunks", "embeddings", "meta"} <= names


def test_round_trip_and_stats(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with MessageStore.open(cfg) as store:
        res = store.upsert_messages([_msg("a@corp", "hello"), _msg("mm:1", "hi", source="mm")])
        assert res == {"inserted": 2, "updated": 0, "unchanged": 0, "total": 2}
        st = store.stats()
        assert st["messages"] == 2
        assert st["by_source"] == {"email": 1, "mm": 1}
    # reopen persists
    with MessageStore.open(cfg) as store:
        assert store.stats()["messages"] == 2


def test_encryption_at_rest(tmp_path, monkeypatch):
    secret = "TOPSECRETNEEDLE42"
    cfg = _cfg(tmp_path, monkeypatch)
    with MessageStore.open(cfg) as store:
        store.upsert_messages([_msg("a@corp", secret)])
    raw = (tmp_path / "messages.db").read_bytes()
    assert secret.encode() not in raw  # body is ciphertext on disk


def test_wrong_key_is_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with MessageStore.open(cfg) as store:
        store.upsert_messages([_msg("a@corp", "hello")])
    # Reopen with a different key → StoreError (not silent garbage).
    monkeypatch.setenv("DIGEST_STORE_KEY", "cd" * 32)
    with pytest.raises(StoreError):
        MessageStore.open(StoreConfig(db_path=str(tmp_path / "messages.db")))


def test_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("DIGEST_STORE_KEY", raising=False)
    with pytest.raises(ValueError):
        MessageStore.open(StoreConfig(db_path=str(tmp_path / "messages.db")))


def test_upsert_idempotent_and_seen_timestamps(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    t1 = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)
    with MessageStore.open(cfg) as store:
        store.upsert_messages([_msg("a@corp", "v1")], now=t1)
        row1 = store.conn.execute(
            "SELECT first_seen_at, last_seen_at, content_hash FROM messages"
        ).fetchone()

        # Re-ingest identical content at a later time → unchanged, last_seen advances.
        res = store.upsert_messages([_msg("a@corp", "v1")], now=t2)
        assert res["unchanged"] == 1 and res["inserted"] == 0 and res["updated"] == 0
        row2 = store.conn.execute(
            "SELECT first_seen_at, last_seen_at, content_hash FROM messages"
        ).fetchone()
        assert row2[0] == row1[0]  # first_seen write-once
        assert row2[1] == t2.isoformat()  # last_seen advanced
        assert row2[2] == row1[2]  # hash unchanged

        # Changed body → updated, new hash.
        res = store.upsert_messages([_msg("a@corp", "v2 changed")], now=t2)
        assert res["updated"] == 1
        hash3 = store.conn.execute("SELECT content_hash FROM messages").fetchone()[0]
        assert hash3 != row1[2]
        assert store.stats()["messages"] == 1  # still one row (upsert, not insert)


def test_fts_synced_by_triggers(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages(
            [
                _msg("a@corp", "approve the quarterly budget", subject="Finance"),
                _msg("b@corp", "обсудим релиз завтра", subject="Релиз"),
            ]
        )
        hit_en = store.conn.execute(
            "SELECT m.id FROM messages_fts f JOIN messages m ON m.rowid=f.rowid "
            "WHERE messages_fts MATCH 'budget'"
        ).fetchall()
        hit_ru = store.conn.execute(
            "SELECT m.id FROM messages_fts f JOIN messages m ON m.rowid=f.rowid "
            "WHERE messages_fts MATCH 'релиз'"
        ).fetchall()
        assert hit_en == [("urn:email:a@corp",)]
        assert hit_ru == [("urn:email:b@corp",)]

        # Update body → FTS reflects the new content, drops the old term.
        store.upsert_messages([_msg("a@corp", "no money here", subject="Finance")])
        assert (
            store.conn.execute(
                "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'budget'"
            ).fetchone()[0]
            == 0
        )


def test_ttl_sweep_removes_old(tmp_path, monkeypatch):
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=45)
    recent = now - timedelta(days=5)
    with MessageStore.open(_cfg(tmp_path, monkeypatch, ttl_days=30)) as store:
        store.upsert_messages(
            [_msg("old@corp", "stale", when=old), _msg("new@corp", "fresh", when=recent)]
        )
        deleted = store.sweep_ttl(now=now)
        assert deleted == 1
        remaining = [r[0] for r in store.conn.execute("SELECT id FROM messages").fetchall()]
        assert remaining == ["urn:email:new@corp"]
        # FTS row for the deleted message is gone too (delete trigger).
        assert (
            store.conn.execute(
                "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'stale'"
            ).fetchone()[0]
            == 0
        )


def test_ttl_zero_is_noop(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages([_msg("a@corp", "keep me")])
        assert store.sweep_ttl(0) == 0
        assert store.stats()["messages"] == 1
