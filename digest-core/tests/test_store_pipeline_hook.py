"""The opt-in store persist hook inside _stage_ingest (PR4).

Requires the `store` extra (skipped otherwise). Offline: a fake EWS ingest +
the real NORMALIZE stage, so raw-vs-normalized capture is exercised.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from digest_core import run as runner
from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.progress import NullSink
from digest_core.store import HAS_SQLCIPHER, MessageStore

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")

# Fixtures must stay inside the store's 30-day TTL window. _persist_to_store sweeps
# TTL right after upsert (run.py), so a message dated >30 days ago is inserted and
# then immediately swept — the "persists" assertions below would see 0 rows. Anchor
# the fixtures to a recent wall-clock date instead of a hardcoded one (a hardcoded
# 2026-06-01 turned main red once now-30d passed it). The TTL sweep itself is
# covered separately in test_store_db.py (test_ttl_sweep_removes_old).
_RECENT = datetime.now(timezone.utc) - timedelta(days=1)
_RECENT_DATE = _RECENT.date().isoformat()


def _msg(msg_id: str, body: str, source: str = "email", mm_channel_type=None) -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c-1",
        subject="Subject",
        text_body=body,
        sender_email="ivan@corp",
        datetime_received=_RECENT,
        to_recipients=["u@corp"],
        cc_recipients=[],
        source=source,
        mm_channel_type=mm_channel_type,
    )


class _DummyMetrics:
    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeIngest:
    def __init__(self, messages, time_config):
        self._messages = messages
        self.time_config = time_config
        self.last_fetch_stats = {}

    def fetch_messages(self, digest_date, time_config):
        return self._messages


def _ctx(tmp_path, monkeypatch, *, enabled=True, replay_ingest=None) -> runner.RunContext:
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    cfg = Config()
    cfg.store.enabled = enabled
    cfg.store.db_path = str(tmp_path / "messages.db")
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    return runner.RunContext(
        trace_id="t-1",
        config=cfg,
        metrics=_DummyMetrics(),
        digest_date=_RECENT_DATE,
        output_dir=out,
        json_path=out / "d.json",
        md_path=out / "d.md",
        metadata_path=out / "d.meta.json",
        dry_run=False,
        force=False,
        validate_citations=False,
        dump_ingest=None,
        replay_ingest=replay_ingest,
        record_llm=None,
        replay_llm=None,
        sink=NullSink(),
        run_meta={"stage_durations_ms": {}},
    )


def test_hook_persists_after_normalize_keeping_raw_and_clean(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch, enabled=True)
    fake = _FakeIngest([_msg("m1@corp", "<p>approve the <b>budget</b></p>")], ctx.config.time)
    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: fake)

    out = runner._stage_ingest(ctx)
    assert len(out) == 1
    assert ctx.run_meta["store"]["inserted"] == 1

    with MessageStore.open(ctx.config.store) as store:
        row = store.conn.execute("SELECT id, body_raw, body_normalized FROM messages").fetchone()
    assert row[0] == "urn:email:m1@corp"
    assert "<b>budget</b>" in row[1]  # raw HTML captured pre-normalize
    assert "<b>" not in row[2] and "budget" in row[2]  # normalized body cleaned


def test_hook_noop_when_disabled(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch, enabled=False)
    fake = _FakeIngest([_msg("m1@corp", "body")], ctx.config.time)
    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: fake)

    runner._stage_ingest(ctx)
    assert "store" not in ctx.run_meta
    assert not (tmp_path / "messages.db").exists()


def test_hook_is_non_fatal_on_store_error(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch, enabled=True)
    fake = _FakeIngest([_msg("m1@corp", "body")], ctx.config.time)
    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: fake)

    class _BoomStore:
        @classmethod
        def open(cls, cfg):
            raise RuntimeError("disk full")

    monkeypatch.setattr("digest_core.store.MessageStore", _BoomStore)

    out = runner._stage_ingest(ctx)  # must NOT raise
    assert len(out) == 1  # digest continues with its messages
    assert "disk full" in ctx.run_meta["store"]["error"]


def test_replay_path_persists_store(tmp_path, monkeypatch):
    snap = tmp_path / "snap.json"
    runner._dump_ingest_snapshot(snap, [_msg("r1@corp", "replayed body")], _RECENT_DATE)
    ctx = _ctx(tmp_path, monkeypatch, enabled=True, replay_ingest=str(snap))

    runner._stage_ingest(ctx)
    with MessageStore.open(ctx.config.store) as store:
        assert store.stats()["messages"] == 1
        row = store.conn.execute("SELECT id, body_raw, body_normalized FROM messages").fetchone()
    assert row[0] == "urn:email:r1@corp"
    assert row[1] == row[2]  # replay has no separate raw body → raw == normalized


def test_dm_redacted_through_real_normalize(tmp_path, monkeypatch):
    """Regression for the BLOCKER: a DM pushed through the REAL NORMALIZE stage must
    still be redacted at rest. NORMALIZE used to drop mm_channel_type, so by the time
    the store saw a DM the field was None and the body was persisted unredacted."""
    from digest_core.store.models import DM_AT_REST_REDACTION

    ctx = _ctx(tmp_path, monkeypatch, enabled=True)
    fake = _FakeIngest(
        [
            _msg("mm:dm1", "secret 1:1 NEEDLEX", source="mm", mm_channel_type="D"),
            _msg("mm:gm1", "group secret NEEDLEY", source="mm", mm_channel_type="G"),
            _msg("mm:ch1", "public channel budget post", source="mm", mm_channel_type="O"),
        ],
        ctx.config.time,
    )
    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: fake)

    runner._stage_ingest(ctx)
    with MessageStore.open(ctx.config.store) as store:
        rows = {
            r[0]: (r[1], r[2])
            for r in store.conn.execute(
                "SELECT id, body_raw, body_normalized FROM messages"
            ).fetchall()
        }
        chunk_msgs = {
            r[0] for r in store.conn.execute("SELECT DISTINCT message_id FROM chunks").fetchall()
        }
    assert rows["urn:mm:dm1"] == (DM_AT_REST_REDACTION, DM_AT_REST_REDACTION)
    assert rows["urn:mm:gm1"] == (DM_AT_REST_REDACTION, DM_AT_REST_REDACTION)
    assert "budget" in rows["urn:mm:ch1"][1]  # 'O' channel post kept + chunked
    assert chunk_msgs == {"urn:mm:ch1"}
    raw = (tmp_path / "messages.db").read_bytes()
    assert b"NEEDLEX" not in raw and b"NEEDLEY" not in raw  # DM bodies never at rest


def test_store_enabled_suppresses_pre_ingest_skip(tmp_path, monkeypatch):
    """Regression for the BLOCKER: with the store enabled, the pre-ingest freshness
    skip must be suppressed so _stage_ingest (and thus _persist_to_store) always runs
    — otherwise a same-day re-run silently bypasses the archive. The skip itself still
    fires when the store is off (covered by test_idempotency)."""
    assert runner._store_archiving(_ctx(tmp_path, monkeypatch, enabled=True)) is True
    assert runner._store_archiving(_ctx(tmp_path, monkeypatch, enabled=False)) is False
