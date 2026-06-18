"""Store honors guardrail #9: DM (1:1 'D' / group 'G') bodies are never at rest.

Requires the `store` extra (skipped otherwise).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.config import StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER, MessageStore
from digest_core.store.models import DM_AT_REST_REDACTION

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    return StoreConfig(db_path=str(tmp_path / "messages.db"))


def _mm(msg_id, body, channel_type):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c-" + msg_id,
        datetime_received=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        sender_email="peer@corp",
        subject="channel",
        text_body=body,
        source="mm",
        mm_channel_type=channel_type,
    )


def test_dm_and_group_bodies_redacted_no_chunks_channel_kept(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages(
            [
                _mm("mm:dm1", "secret 1:1 gossip NEEDLEX", "D"),
                _mm("mm:gm1", "group DM chatter NEEDLEY", "G"),
                _mm("mm:ch1", "public channel budget post", "O"),
            ]
        )
        rows = {
            r[0]: (r[1], r[2], r[3])
            for r in store.conn.execute(
                "SELECT id, mm_channel_type, body_raw, body_normalized FROM messages"
            ).fetchall()
        }
        # DM + group-DM bodies redacted (row + metadata kept).
        assert rows["urn:mm:dm1"] == ("D", DM_AT_REST_REDACTION, DM_AT_REST_REDACTION)
        assert rows["urn:mm:gm1"] == ("G", DM_AT_REST_REDACTION, DM_AT_REST_REDACTION)
        # Public-channel post is an email-equivalent work artifact → kept.
        assert rows["urn:mm:ch1"][0] == "O"
        assert "budget" in rows["urn:mm:ch1"][2]

        # No chunks/embeddings for DMs; the channel post is chunked.
        chunk_msgs = {
            r[0] for r in store.conn.execute("SELECT DISTINCT message_id FROM chunks").fetchall()
        }
        assert chunk_msgs == {"urn:mm:ch1"}

        # DM content is not keyword-searchable (FTS indexes only the marker).
        assert store.search("NEEDLEX", mode="keyword") == []

    # And of course never present in the encrypted file on disk.
    raw = (tmp_path / "messages.db").read_bytes()
    assert b"NEEDLEX" not in raw and b"NEEDLEY" not in raw


def test_unknown_mm_channel_type_fails_closed(tmp_path, monkeypatch):
    """Fail-closed: an mm message with a missing/unknown channel type is redacted
    (we must not persist a body just because the type was indeterminate)."""
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages([_mm("mm:huh", "secret NEEDLEZ", None)])
        row = store.conn.execute("SELECT body_raw, body_normalized FROM messages").fetchone()
        chunks = store.conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert row == (DM_AT_REST_REDACTION, DM_AT_REST_REDACTION)
    assert chunks == 0
    assert b"NEEDLEZ" not in (tmp_path / "messages.db").read_bytes()
