"""OTel GenAI tracing (EP-8, frontier-audit F6).

The contract: flag off → strict no-op; flag on → a run/stage/gen_ai.* span tree
reaches the file exporter with structural attributes ONLY — never payload text.
Collector reachability in corp is a W3 check (offline honesty).
"""

import json
from pathlib import Path

import pytest

from digest_core import run as runner
from digest_core.config import Config, LLMConfig
from digest_core.observability import tracing
from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


@pytest.fixture(autouse=True)
def _reset_tracing():
    tracing.reset_tracing()
    yield
    tracing.reset_tracing()


def test_flag_off_is_a_strict_noop():
    config = Config()
    assert config.observability.otel_enabled is False
    assert tracing.configure_tracing(config.observability, {}) is False
    # helpers are inert without a tracer
    tracing.start_run_span("t", "2026-06-11")
    tracing.record_stage_span("ingest", 0.1)
    with tracing.llm_call_span("qwen35-397b-a17b") as span:
        assert span is None
    tracing.end_run_span("ok")


def test_missing_extra_degrades_to_disabled(monkeypatch):
    """A configured flag without the 'otel' extra must not break the run."""
    import builtins

    real_import = builtins.__import__

    def _no_otel(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError("otel extra not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_otel)
    obs = Config().observability.model_copy(update={"otel_enabled": True})
    assert tracing.configure_tracing(obs, {}) is False


def _read_spans(path: Path) -> list:
    # ConsoleSpanExporter writes one pretty-printed JSON object per span; the
    # objects are concatenated. Split on the brace that starts each span record.
    raw = path.read_text(encoding="utf-8")
    spans = []
    for blob in raw.split('{\n    "name"')[1:]:
        spans.append(json.loads('{\n    "name"' + blob))
    return spans


def test_replay_run_emits_payload_free_span_tree(monkeypatch, tmp_path):
    spans_path = tmp_path / "spans.jsonl"
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        f"observability:\n  otel_enabled: true\n  otel_export_path: {spans_path}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(config_yaml))
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    snapshot_path = tmp_path / "snapshot.json"
    out_dir = tmp_path / "out"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")
    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", FakeGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

    assert runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(out_dir),
        model="qwen35-397b-a17b",
        window="calendar_day",
        state=None,
        force=True,
        replay_ingest=str(snapshot_path),
    )
    tracing.reset_tracing()  # flush + close the export file

    meta = json.loads(next(out_dir.glob("trace-*.meta.json")).read_text(encoding="utf-8"))
    assert meta["otel"]["enabled"] is True

    spans = _read_spans(spans_path)
    names = [span["name"] for span in spans]
    assert "digest.run" in names
    assert any(name.startswith("stage.") for name in names)

    run_span = next(span for span in spans if span["name"] == "digest.run")
    assert run_span["attributes"]["actionpulse.trace_id"] == meta["trace_id"]
    stage_span = next(span for span in spans if span["name"].startswith("stage."))
    assert stage_span["context"]["trace_id"] == run_span["context"]["trace_id"]

    # the guardrail: no payload text ever reaches telemetry
    raw = spans_path.read_text(encoding="utf-8")
    assert "Пожалуйста" not in raw and "production server" not in raw.lower()


def test_llm_call_span_carries_gen_ai_attributes(tmp_path):
    import httpx

    spans_path = tmp_path / "spans.jsonl"
    obs = Config().observability.model_copy(
        update={"otel_enabled": True, "otel_export_path": str(spans_path)}
    )
    assert tracing.configure_tracing(obs, {"service.version": "test"}) is True

    from digest_core.evidence.split import EvidenceChunk
    from digest_core.llm.gateway import LLMGateway

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"sections": []})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": body}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 111, "completion_tokens": 7, "total_tokens": 118},
            },
        )

    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {"LLM_TOKEN": "mock"}):
        gateway = LLMGateway(LLMConfig(endpoint="http://test/v1/chat/completions"))
        gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
        gateway.extract_actions(
            [
                EvidenceChunk(
                    evidence_id="ev-otel",
                    content="SECRET-BODY-TEXT must not appear in spans.",
                    source_ref={"type": "email", "msg_id": "m-otel"},
                    priority_score=0.5,
                )
            ],
            "PROMPT-TEXT must not appear in spans.",
            "trace-otel",
        )
    tracing.reset_tracing()

    spans = _read_spans(spans_path)
    llm_span = next(span for span in spans if span["name"].startswith("chat "))
    attrs = llm_span["attributes"]
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.system"] == tracing.GEN_AI_SYSTEM
    assert attrs["gen_ai.request.model"] == "qwen35-397b-a17b"
    assert attrs["gen_ai.usage.input_tokens"] == 111
    assert attrs["gen_ai.usage.output_tokens"] == 7
    assert attrs["gen_ai.response.finish_reasons"] == ["stop"]

    raw = spans_path.read_text(encoding="utf-8")
    assert "SECRET-BODY-TEXT" not in raw and "PROMPT-TEXT" not in raw
