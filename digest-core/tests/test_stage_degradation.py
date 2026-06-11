"""Per-stage graceful degradation (PR4).

A failed early stage (ingest/threads/evidence/select) degrades to an empty/partial
digest that is still assembled and delivered (degrade-not-drop). assemble failures
crash (exit 1); with degrade disabled, every stage crashes.
"""

import json
from datetime import datetime, timezone

import pytest

from digest_core import run as runner
from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.run import degradation_policy

LONG_BODY = ("Пожалуйста, подготовь обновление статуса и пришли мне до пятницы. " * 12).strip()


def _raise(*args, **kwargs):
    raise RuntimeError("boom")


def _raise_operational(*args, **kwargs):
    raise ConnectionError("EWS unreachable")


def _raise_config(*args, **kwargs):
    raise ValueError("Environment variable EWS_PASSWORD not set")


def _raise_missing_file(*args, **kwargs):
    raise FileNotFoundError("[Errno 2] No such file or directory: '/etc/ssl/corp-ca.pem'")


# --- pure policy ------------------------------------------------------------


def test_policy_maps_each_stage():
    cfg = Config()
    # Network errors always degrade ingest/normalize to empty.
    assert degradation_policy("ingest", ConnectionError(), cfg) == "empty"
    # A missing file degrades only in replay mode (missing snapshot); in live mode
    # it is a config error (e.g. bad verify_ca) and must crash.
    assert degradation_policy("normalize", OSError(), cfg, replay=True) == "empty"
    assert degradation_policy("ingest", FileNotFoundError(), cfg) == "crash"  # live: bad CA path
    assert degradation_policy("ingest", ValueError("no creds"), cfg) == "crash"  # config error
    assert degradation_policy("threads", Exception(), cfg) == "partial"
    assert degradation_policy("evidence", Exception(), cfg) == "partial"
    assert degradation_policy("select", Exception(), cfg) == "partial"
    assert degradation_policy("assemble", Exception(), cfg) == "crash"
    assert degradation_policy("unknown", Exception(), cfg) == "crash"


def test_policy_disabled_crashes_everywhere():
    cfg = Config()
    cfg.degrade.enable = False
    for stage in ("ingest", "normalize", "threads", "evidence", "select", "assemble"):
        assert degradation_policy(stage, Exception(), cfg) == "crash"


# --- integration ------------------------------------------------------------


class _DummyMetrics:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeDeliverer:
    sent = []

    def __init__(self, config):
        pass

    def deliver_digest(self, digest, json_path=None, **kwargs):
        _FakeDeliverer.sent.append(digest)
        return {"status": "sent", "parts": 1}


class _EchoGateway:
    def __init__(self, *args, **kwargs):
        self.last_request_meta = {
            "tokens_in": 1,
            "tokens_out": 1,
            "http_status": 200,
            "latency_ms": 1,
            "retry_count": 0,
            "validation_errors": 0,
        }

    def extract_actions(self, evidence, prompt_template, trace_id):
        return {
            "sections": [
                {
                    "title": "Мои действия",
                    "items": [
                        {
                            "title": "x",
                            "due": None,
                            "evidence_id": evidence[0].evidence_id,
                            "confidence": 0.9,
                            "source_ref": {
                                "type": "email",
                                "msg_id": evidence[0].source_ref["msg_id"],
                            },
                        }
                    ],
                }
            ]
        }

    def get_request_stats(self):
        return {"last_latency_ms": 1, "model": "qwen35-397b-a17b", "timeout_s": 120}


def _message():
    return NormalizedMessage(
        msg_id="m-1",
        conversation_id="c-1",
        datetime_received=datetime(2026, 3, 29, tzinfo=timezone.utc),
        sender_email="a@corp.com",
        subject="S",
        text_body=LONG_BODY,
        to_recipients=["u@corp.com"],
        cc_recipients=[],
        body_norm=LONG_BODY,
    )


def _patch(monkeypatch):
    monkeypatch.setattr(runner, "MetricsCollector", _DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *a, **k: None)
    monkeypatch.setattr(runner, "LLMGateway", _EchoGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", _FakeDeliverer)
    _FakeDeliverer.sent.clear()


def _run(monkeypatch, tmp_path, *, validate_citations=False, replay_ingest=None):
    out = tmp_path / "out"
    if replay_ingest is None:
        snap = tmp_path / "snap.json"
        runner._dump_ingest_snapshot(snap, [_message()], "2026-03-29")
        replay_ingest = str(snap)
    return (
        runner.run_digest(
            from_date="2026-03-29",
            sources=["ews"],
            out=str(out),
            model="qwen35-397b-a17b",
            window="calendar_day",
            state=str(tmp_path / "state"),
            force=True,
            validate_citations=validate_citations,
            replay_ingest=replay_ingest,
        ),
        out,
    )


def test_threads_failure_degrades_to_partial(monkeypatch, tmp_path):
    _patch(monkeypatch)
    monkeypatch.setattr(runner, "_stage_threads", _raise)

    result, out = _run(monkeypatch, tmp_path)

    assert result  # exit 0 — degraded, not crashed
    assert result.citation_validation_ok is True
    payload = json.loads((out / "digest-2026-03-29.json").read_text(encoding="utf-8"))
    assert "Статус" in [s["title"] for s in payload["sections"]]
    assert _FakeDeliverer.sent  # the degraded digest was delivered


def test_ingest_operational_failure_degrades_to_empty(monkeypatch, tmp_path):
    _patch(monkeypatch)
    monkeypatch.setattr(runner, "_stage_ingest", _raise_operational)

    result, out = _run(monkeypatch, tmp_path)

    assert result
    payload = json.loads((out / "digest-2026-03-29.json").read_text(encoding="utf-8"))
    assert payload["sections"] == []  # empty digest


def test_ingest_config_error_crashes(monkeypatch, tmp_path):
    # A precondition failure (e.g. missing EWS credentials) must fail fast, not
    # silently produce an empty digest -- this keeps `cli run` exit-1 on misconfig.
    _patch(monkeypatch)
    monkeypatch.setattr(runner, "_stage_ingest", _raise_config)

    with pytest.raises(ValueError):
        _run(monkeypatch, tmp_path)


def test_live_ingest_missing_file_crashes(monkeypatch, tmp_path):
    # Reproduces the CI regression: a live run whose verify_ca path is absent raises
    # FileNotFoundError in EWS setup. In LIVE mode (no replay) that is a config error
    # and must crash, not degrade to a silent empty digest.
    _patch(monkeypatch)
    monkeypatch.setattr(runner, "_stage_ingest", _raise_missing_file)

    with pytest.raises(FileNotFoundError):
        runner.run_digest(
            from_date="2026-03-29",
            sources=["ews"],
            out=str(tmp_path / "out"),
            model="qwen35-397b-a17b",
            window="calendar_day",
            state=str(tmp_path / "state"),
            force=True,
            replay_ingest=None,  # LIVE mode -> missing file is a config error
        )


def test_replay_missing_snapshot_degrades(monkeypatch, tmp_path):
    _patch(monkeypatch)

    result, out = _run(monkeypatch, tmp_path, replay_ingest=str(tmp_path / "does-not-exist.json"))

    assert result  # missing snapshot -> ingest degrades to empty, not a crash
    assert (out / "digest-2026-03-29.json").exists()


def test_assemble_failure_still_crashes(monkeypatch, tmp_path):
    _patch(monkeypatch)
    monkeypatch.setattr(runner, "_stage_assemble", _raise)

    with pytest.raises(RuntimeError):
        _run(monkeypatch, tmp_path)


def test_validate_citations_fails_gate_on_degraded(monkeypatch, tmp_path):
    _patch(monkeypatch)
    monkeypatch.setattr(runner, "_stage_select", _raise)

    result, _ = _run(monkeypatch, tmp_path, validate_citations=True)

    assert result.pipeline_succeeded is True
    assert result.citation_validation_ok is False  # exit 2 under --validate-citations


def test_degrade_disabled_crashes(monkeypatch, tmp_path):
    _patch(monkeypatch)

    def _config_no_degrade():
        cfg = Config()
        cfg.degrade.enable = False
        return cfg

    monkeypatch.setattr(runner, "Config", _config_no_degrade)
    monkeypatch.setattr(runner, "_stage_threads", _raise)

    with pytest.raises(RuntimeError):
        _run(monkeypatch, tmp_path)
