"""The live ingest stage is routed through the multi-source seam (PR12b / B4).

These tests pin the *wiring*: ``_stage_ingest`` must reach the live EWS fetch via
``run_sources([EWSSourceAdapter(...)])`` and hand the next stage the exact same
ordered ``List[NormalizedMessage]`` a direct ``EWSIngest.fetch_messages`` call
produced. Routing through the seam must not swallow fetch exceptions (so the
degradation policy still sees the real error), must keep ``last_fetch_stats``
flowing into ``run_meta``, and must leave the ``--dump-ingest`` / ``--replay-
ingest`` paths byte-identical (ADR-012 offline contract is sacred).
"""

from datetime import datetime, timezone

import pytest

from digest_core import run as runner
from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.progress import NullSink


def _msg(msg_id: str) -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c-1",
        subject="s",
        text_body="b",
        sender_email="a@b.com",
        datetime_received=datetime(2026, 3, 29, tzinfo=timezone.utc),
        to_recipients=["u@corp"],
        cc_recipients=[],
    )


class _DummyMetrics:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _ctx(tmp_path) -> runner.RunContext:
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    return runner.RunContext(
        trace_id="t-1",
        config=Config(),
        metrics=_DummyMetrics(),
        digest_date="2026-03-29",
        output_dir=out,
        json_path=out / "d.json",
        md_path=out / "d.md",
        metadata_path=out / "d.meta.json",
        dry_run=False,
        force=False,
        validate_citations=False,
        dump_ingest=None,
        replay_ingest=None,
        record_llm=None,
        replay_llm=None,
        sink=NullSink(),
        run_meta={"stage_durations_ms": {}},
    )


class _FakeIngest:
    """Stands in for EWSIngest: records the fetch call, returns a fixture."""

    def __init__(self, messages, time_config):
        self._messages = messages
        self.time_config = time_config
        self.last_fetch_stats = {"pages": 2, "retries": 1, "skipped": 3}
        self.fetch_calls = []

    def fetch_messages(self, digest_date, time_config):
        self.fetch_calls.append((digest_date, time_config))
        return self._messages


def test_stage_ingest_routes_live_fetch_through_run_sources(tmp_path, monkeypatch):
    """Live ingest output == direct fetch_messages output, via the seam."""
    ctx = _ctx(tmp_path)
    fixture = [_msg("m-1"), _msg("m-2"), _msg("m-3")]
    fake = _FakeIngest(fixture, ctx.config.time)

    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: fake)
    # Isolate the seam: keep normalize as identity so we compare exactly what the
    # seam handed downstream against what fetch_messages returned.
    monkeypatch.setattr(runner, "_normalize_messages", lambda msgs, cfg, sink=None: list(msgs))

    # Spy on run_sources to prove the live path actually goes through it.
    calls = {}
    real_run_sources = runner.run_sources

    def _spy(adapters, digest_date, *, strict=False):
        calls["adapters"] = adapters
        calls["digest_date"] = digest_date
        calls["strict"] = strict
        return real_run_sources(adapters, digest_date, strict=strict)

    monkeypatch.setattr(runner, "run_sources", _spy)

    result = runner._stage_ingest(ctx)

    # Seam was used, single EWS adapter, strict so failures still propagate.
    assert calls["digest_date"] == "2026-03-29"
    assert calls["strict"] is True
    assert [a.name for a in calls["adapters"]] == ["ews"]

    # Same type/shape/order/objects the direct call would have produced.
    assert result == fixture
    assert [m.msg_id for m in result] == ["m-1", "m-2", "m-3"]
    assert all(isinstance(m, NormalizedMessage) for m in result)

    # fetch_messages still called once with the run's date + config.time.
    assert fake.fetch_calls == [("2026-03-29", ctx.config.time)]

    # Stage-health stats still flow off the same ingest instance into run_meta.
    assert ctx.run_meta["ews_fetch_stats"]["source"] == "ews"
    assert ctx.run_meta["ews_fetch_stats"]["message_count"] == 3
    assert ctx.run_meta["ews_fetch_stats"]["retries"] == 1
    assert ctx.run_meta["ews_fetch_stats"]["skipped"] == 3


def test_stage_ingest_matches_direct_ews_fetch(tmp_path, monkeypatch):
    """Belt-and-suspenders: seam output is identical to a bare fetch_messages."""
    ctx = _ctx(tmp_path)
    fixture = [_msg("a"), _msg("b")]
    fake = _FakeIngest(fixture, ctx.config.time)

    # What a direct EWSIngest.fetch_messages call would return today.
    direct = fake.fetch_messages("2026-03-29", ctx.config.time)
    fake.fetch_calls.clear()

    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: fake)
    monkeypatch.setattr(runner, "_normalize_messages", lambda msgs, cfg, sink=None: list(msgs))

    seam_out = runner._stage_ingest(ctx)

    assert seam_out == direct
    assert [m.msg_id for m in seam_out] == [m.msg_id for m in direct]


def test_stage_ingest_seam_propagates_fetch_errors(tmp_path, monkeypatch):
    """strict=True: a fetch failure must NOT be swallowed by the seam.

    The degradation policy distinguishes config errors (crash) from operational
    errors (degrade); that only works if the original exception reaches _guard.
    """

    class _BoomIngest:
        time_config = object()
        last_fetch_stats = {}

        def fetch_messages(self, digest_date, time_config):
            raise ConnectionError("EWS unreachable")

    ctx = _ctx(tmp_path)
    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: _BoomIngest())

    with pytest.raises(ConnectionError, match="EWS unreachable"):
        runner._stage_ingest(ctx)


def test_dump_replay_roundtrip_unchanged_through_seam(tmp_path, monkeypatch):
    """Live fetch via seam -> --dump-ingest -> --replay-ingest yields same messages.

    Proves the on-disk snapshot format and replay consumption are untouched by
    routing the live fetch through the seam.
    """
    snapshot = tmp_path / "ingest.json"

    # 1) Live run via the seam, dumping a snapshot.
    dump_ctx = _ctx(tmp_path)
    dump_ctx.dump_ingest = str(snapshot)
    fixture = [_msg("m-1"), _msg("m-2")]
    fake = _FakeIngest(fixture, dump_ctx.config.time)
    monkeypatch.setattr(runner, "EWSIngest", lambda *a, **k: fake)
    monkeypatch.setattr(runner, "_normalize_messages", lambda msgs, cfg, sink=None: list(msgs))

    live_messages = runner._stage_ingest(dump_ctx)
    assert snapshot.exists()

    # 2) Replay the snapshot (no EWS at all) and compare downstream input.
    replay_ctx = _ctx(tmp_path)
    replay_ctx.replay_ingest = str(snapshot)
    replay_messages = runner._stage_ingest(replay_ctx)

    assert [m.msg_id for m in replay_messages] == [m.msg_id for m in live_messages]
    assert len(replay_messages) == len(live_messages) == 2
    assert replay_ctx.run_meta["ews_fetch_stats"]["source"] == "replay"
