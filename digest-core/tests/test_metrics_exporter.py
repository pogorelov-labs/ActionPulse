"""Metrics-exporter bind failure surfacing (enhancement program EP-2, frontier-audit F6).

A failed ``start_http_server`` used to be a swallowed warning — the run looked
healthy while being unobservable. It must now be recorded (error log +
``exporter_status``) and, behind ``observability.fail_on_exporter_error``, fatal.
"""

import json
from pathlib import Path

import pytest

import digest_core.observability.metrics as metrics_mod
from digest_core import run as runner
from digest_core.config import Config
from digest_core.observability.metrics import MetricsCollector
from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


@pytest.fixture(autouse=True)
def _isolate_active_ports():
    saved = set(MetricsCollector._active_ports)
    yield
    MetricsCollector._active_ports = saved


def _bind_failure(*args, **kwargs):
    raise OSError("[Errno 48] Address already in use")


def test_bind_failure_is_recorded_not_swallowed(monkeypatch):
    monkeypatch.setattr(metrics_mod, "start_http_server", _bind_failure)
    collector = MetricsCollector(port=59118)

    status = collector.exporter_status()
    assert status["status"] == "failed"
    assert status["port"] == 59118
    assert "Address already in use" in status["error"]


def test_bind_failure_raises_when_flag_set(monkeypatch):
    monkeypatch.setattr(metrics_mod, "start_http_server", _bind_failure)
    with pytest.raises(OSError, match="Address already in use"):
        MetricsCollector(port=59119, fail_on_exporter_error=True)


def test_successful_bind_reports_ok(monkeypatch):
    monkeypatch.setattr(metrics_mod, "start_http_server", lambda *a, **k: None)
    collector = MetricsCollector(port=59120)

    status = collector.exporter_status()
    assert status == {"status": "ok", "port": 59120, "error": None}


def test_config_default_keeps_run_alive():
    assert Config().observability.fail_on_exporter_error is False


def test_run_meta_carries_exporter_status(monkeypatch, tmp_path):
    """E2E (replay): the trace shows the exporter state even with test doubles."""
    snapshot_path = tmp_path / "snapshot.json"
    out_dir = tmp_path / "out"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")

    monkeypatch.chdir(Path(__file__).resolve().parents[2])
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

    meta = json.loads(next(out_dir.glob("trace-*.meta.json")).read_text(encoding="utf-8"))
    # DummyMetrics has no real exporter — the entry degrades to "unknown", never crashes
    assert meta["metrics_exporter"]["status"] in ("ok", "failed", "not_started", "unknown")
