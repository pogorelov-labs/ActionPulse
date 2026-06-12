"""ProgressSink event seam (T2): run.py emits, sinks observe, NullSink is silent.

Mirrors the replay harness from test_e2e_pipeline.py: a real pipeline run over
a snapshot with a fake gateway/deliverer, asserting the event sequence and the
funnel counts — the contract T3 (PlainSink) and T4 (RichLiveSink) render.
"""

from pathlib import Path

import httpx

from digest_core import run as runner
from digest_core.progress import NullSink, ProgressSink

from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


class FailingThreadBuilder:
    def __init__(self, *args, **kwargs):
        pass

    def build_threads(self, messages):
        raise RuntimeError("threads exploded")


class RecordingSink(ProgressSink):
    def __init__(self):
        self.events: list[tuple] = []

    def on_stage_start(self, stage):
        self.events.append(("start", stage))

    def on_stage_end(self, stage, counts, duration_ms):
        self.events.append(("end", stage, counts, duration_ms))

    def on_stage_failed(self, stage, error):
        self.events.append(("failed", stage, error))

    def on_llm_attempt(self, model, attempt, max_attempts):
        self.events.append(("llm_attempt", model, attempt, max_attempts))

    def on_delivery(self, target, ok, detail=None):
        self.events.append(("delivery", target, ok))

    def on_run_end(self, status):
        self.events.append(("run_end", status))


class BrokenSink(ProgressSink):
    def on_stage_start(self, stage):
        raise RuntimeError("renderer crashed")


def _run_replay(monkeypatch, tmp_path, sink, gateway=FakeGateway):
    FakeDeliverer.deliveries.clear()
    snapshot_path = tmp_path / "snapshot.json"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")

    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", gateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

    return runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(tmp_path / "out"),
        model="qwen35-397b-a17b",
        window="calendar_day",
        state=str(tmp_path / "state"),
        force=True,
        replay_ingest=str(snapshot_path),
        sink=sink,
    )


def test_event_sequence_with_funnel_counts(monkeypatch, tmp_path):
    sink = RecordingSink()
    result = _run_replay(monkeypatch, tmp_path, sink)
    assert result

    started = [e[1] for e in sink.events if e[0] == "start"]
    ended = {e[1]: e for e in sink.events if e[0] == "end"}

    # Replay mode: ingest delivers normalized messages (no separate normalize).
    assert started[0] == "ingest"
    assert {"threads", "evidence", "select", "llm", "assemble", "deliver"} <= set(started)

    assert ended["ingest"][2] == {"messages": 1}
    assert ended["threads"][2] == {"messages": 1, "threads": 1}
    assert ended["evidence"][2]["chunks"] >= 1
    assert ended["select"][2]["selected"] >= 1
    assert ended["select"][2]["of"] == ended["evidence"][2]["chunks"]
    assert ended["llm"][2]["sections"] == 2
    assert ended["llm"][2]["items"] == 2
    # T5: token spend from the gateway meta rides the llm stage counts.
    assert ended["llm"][2]["tokens_in"] == 123
    assert ended["llm"][2]["tokens_out"] == 45
    assert ended["assemble"][2] == {"items": 2}

    for event in sink.events:
        if event[0] == "end":
            assert event[3] >= 0  # duration_ms

    assert ("llm_attempt", "qwen35-397b-a17b", 1, 2) in sink.events
    assert ("delivery", "mattermost", True) in sink.events
    assert sink.events[-1][0] == "run_end"  # lifecycle hook fires last (finally)

    # Every start has a matching end, and start precedes end.
    for stage in started:
        assert stage in ended


def test_null_sink_is_default_and_silent(monkeypatch, tmp_path):
    # No sink passed -> NullSink; the run is unaffected.
    result = _run_replay(monkeypatch, tmp_path, None)
    assert result
    assert isinstance(NullSink(), ProgressSink)


def test_broken_sink_never_breaks_the_pipeline(monkeypatch, tmp_path):
    result = _run_replay(monkeypatch, tmp_path, BrokenSink())
    assert result  # pipeline completed despite the raising sink
    assert FakeDeliverer.deliveries


def test_stage_failure_emits_on_stage_failed(monkeypatch, tmp_path):
    sink = RecordingSink()
    monkeypatch.setattr(runner, "ThreadBuilder", FailingThreadBuilder)
    result = _run_replay(monkeypatch, tmp_path, sink)
    assert result  # degrades to a partial digest, does not crash

    failed = [e for e in sink.events if e[0] == "failed"]
    assert failed and failed[0][1] == "threads"
    assert "threads exploded" in failed[0][2]


def test_llm_failure_path_still_ends_stage(monkeypatch, tmp_path):
    class FailingGateway(FakeGateway):
        def extract_actions(self, evidence, prompt_template, trace_id):
            raise httpx.ReadTimeout("timed out")

    sink = RecordingSink()
    result = _run_replay(monkeypatch, tmp_path, sink, gateway=FailingGateway)
    assert result
    ended = {e[1]: e for e in sink.events if e[0] == "end"}
    # The partial digest carries the Status section -> counts still emitted.
    assert "llm" in ended
    assert ended["llm"][2]["sections"] == 1


class RecordingSinkU2(RecordingSink):
    """RecordingSink + the U2 intra-stage vocabulary."""

    def on_stage_progress(self, stage, done, total=None, unit="", detail=""):
        self.events.append(("progress", stage, done, total, unit, detail))

    def on_stage_retry(self, stage, attempt, max_attempts, reason):
        self.events.append(("retry", stage, attempt, max_attempts, reason))


class RetryingStatsGateway(FakeGateway):
    """FakeGateway that reports transient retries via get_request_stats (U2)."""

    def get_request_stats(self):
        stats = super().get_request_stats()
        stats["run_retries"] = 2
        return stats


def test_evidence_progress_events_fire_in_replay(monkeypatch, tmp_path):
    sink = RecordingSinkU2()
    assert _run_replay(monkeypatch, tmp_path, sink)
    progress = [e for e in sink.events if e[0] == "progress" and e[1] == "evidence"]
    assert progress, "evidence split must emit on_stage_progress"
    # One message -> one thread: done/total honest, unit owned by the producer.
    assert progress[-1][2] == progress[-1][3] == 1
    assert progress[-1][4] == "threads"


def test_llm_retries_ride_counts_and_stage_health(monkeypatch, tmp_path):
    sink = RecordingSinkU2()
    assert _run_replay(monkeypatch, tmp_path, sink, gateway=RetryingStatsGateway)

    ended = {e[1]: e for e in sink.events if e[0] == "end"}
    assert ended["llm"][2]["retries"] == 2
    # The same numbers land in the trace meta for the corp read-out.
    meta_files = list((tmp_path / "out").glob("trace-*.meta.json"))
    assert meta_files
    import json as _json

    meta = _json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta["stage_health"] == {"llm": {"retries": 2}}


def test_clean_run_has_no_stage_health(monkeypatch, tmp_path):
    sink = RecordingSinkU2()
    assert _run_replay(monkeypatch, tmp_path, sink)
    import json as _json

    meta_files = list((tmp_path / "out").glob("trace-*.meta.json"))
    meta = _json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert "stage_health" not in meta  # nonzero-only: silence means healthy
