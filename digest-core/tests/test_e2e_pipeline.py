import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import tenacity.nap
import yaml
from typer.testing import CliRunner

from digest_core import run as runner
from digest_core.run import RunDigestResult
from digest_core.cli import app
from digest_core.config import Config, LLMConfig, MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.diagnostics import export_diagnostics
from digest_core.evidence.split import EvidenceChunk
from digest_core.ingest.ews import NormalizedMessage
from digest_core.llm.gateway import LLMGateway
from digest_core.llm.schemas import Digest

LONG_BODY = (
    "Пожалуйста, подготовь обновление статуса проекта, пришли его мне и зафиксируй "
    "основные риски в заметках для руководителя. " * 10
).strip()


class DummyMetrics:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class FakeDeliverer:
    deliveries = []

    def __init__(self, config):
        self.config = config

    def deliver_digest(self, digest, json_path=None):
        self.__class__.deliveries.append(digest)
        return {"status": "sent", "parts": 1}


class FakeGateway:
    def __init__(self, *args, **kwargs):
        self.last_request_meta = {
            "tokens_in": 123,
            "tokens_out": 45,
            "http_status": 200,
            "latency_ms": 12,
            "retry_count": 0,
            "validation_errors": 0,
        }

    def extract_actions(self, evidence, prompt_template, trace_id):
        return {
            "sections": [
                {
                    "title": "К сведению",
                    "items": [
                        {
                            "title": "Команда обновила статус проекта",
                            "due": None,
                            "evidence_id": evidence[0].evidence_id,
                            "confidence": 0.76,
                            "source_ref": {
                                "type": "email",
                                "msg_id": evidence[0].source_ref["msg_id"],
                            },
                        }
                    ],
                },
                {
                    "title": "Мои действия",
                    "items": [
                        {
                            "title": "Подготовить обновление статуса проекта",
                            "due": "today",
                            "evidence_id": evidence[0].evidence_id,
                            "confidence": 0.91,
                            "source_ref": {
                                "type": "email",
                                "msg_id": evidence[0].source_ref["msg_id"],
                            },
                        }
                    ],
                },
            ]
        }

    def get_request_stats(self):
        return {
            "last_latency_ms": 12,
            "model": "qwen35-397b-a17b",
            "timeout_s": 120,
        }


class FailingGateway(FakeGateway):
    def extract_actions(self, evidence, prompt_template, trace_id):
        raise httpx.ReadTimeout("timed out")


def make_message() -> NormalizedMessage:
    return NormalizedMessage(
        msg_id="msg-1",
        conversation_id="conv-1",
        datetime_received=datetime.now(timezone.utc),
        sender_email="manager@corp.com",
        subject="Статус проекта",
        text_body=LONG_BODY,
        to_recipients=["user@corp.com"],
        cc_recipients=[],
        importance="High",
        is_flagged=False,
        has_attachments=False,
        attachment_types=[],
        from_email="manager@corp.com",
        from_name="Manager",
        to_emails=["user@corp.com"],
        cc_emails=[],
        message_id="msg-1",
        body_norm=LONG_BODY,
        received_at=datetime.now(timezone.utc),
    )


def make_evidence_chunk() -> EvidenceChunk:
    return EvidenceChunk(
        evidence_id="ev-1",
        conversation_id="conv-1",
        content=LONG_BODY,
        source_ref={"type": "email", "msg_id": "msg-1", "conversation_id": "conv-1"},
        token_count=120,
        priority_score=2.5,
        message_metadata={"subject": "Статус проекта", "from": "manager@corp.com"},
        addressed_to_me=True,
        user_aliases_matched=["user@corp.com"],
        signals={"action_verbs": ["подготовь"], "dates": ["2026-03-30"]},
    )


def test_config_env_overrides_yaml(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "ews": {"user_upn": "yaml@corp.com"},
                "llm": {"endpoint": "https://yaml.example"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("EWS_USER_UPN", "env@corp.com")
    monkeypatch.setenv("LLM_ENDPOINT", "https://env.example")

    config = Config()

    assert config.ews.user_upn == "env@corp.com"
    assert config.llm.endpoint == "https://env.example"


def test_llm_gateway_retries_429(monkeypatch):
    calls = []
    evidence = [make_evidence_chunk()]

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "sections": [
                                        {
                                            "title": "Мои действия",
                                            "items": [
                                                {
                                                    "title": "Подготовить обновление статуса проекта",
                                                    "due": "2026-03-30",
                                                    "evidence_id": "ev-1",
                                                    "confidence": 0.92,
                                                    "source_ref": {
                                                        "type": "email",
                                                        "msg_id": "msg-1",
                                                    },
                                                }
                                            ],
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            request=request,
        )

    monkeypatch.setenv("LLM_TOKEN", "token")
    monkeypatch.setattr(tenacity.nap, "sleep", lambda seconds: None)

    gateway = LLMGateway(LLMConfig(endpoint="https://llm.example"))
    gateway.client = httpx.Client(
        transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0)
    )

    result = gateway.extract_actions(evidence, "system prompt", "trace-1")

    assert len(calls) == 2
    assert result["_meta"]["retry_count"] == 1
    assert result["sections"][0]["title"] == "Мои действия"


def test_mattermost_delivery_formats_and_splits(monkeypatch):
    posts = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json):
            posts.append({"url": url, "json": json})
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setenv("MM_WEBHOOK_URL", "https://mm.example/webhook")
    monkeypatch.setattr("digest_core.deliver.mattermost.httpx.Client", FakeClient)

    digest = Digest(
        prompt_version="extract_actions.v1",
        digest_date="2026-03-29",
        trace_id="trace-mm",
        sections=[
            {
                "title": "Мои действия",
                "items": [
                    {
                        "title": "Подготовить обновление статуса проекта",
                        "due": "today",
                        "evidence_id": "ev-1",
                        "confidence": 0.91,
                        "source_ref": {"type": "email", "msg_id": "msg-1"},
                    }
                ],
            },
            {
                "title": "К сведению",
                "items": [
                    {
                        "title": "Общий статус программы обновлен",
                        "due": None,
                        "evidence_id": "ev-2",
                        "confidence": 0.76,
                        "source_ref": {"type": "email", "msg_id": "msg-2"},
                    }
                ],
            },
        ],
    )

    config = MattermostDeliverConfig(max_message_length=120)
    receipt = MattermostDeliverer(config).deliver_digest(digest)

    assert receipt["status"] == "sent"
    assert len(posts) >= 2
    assert "Источники" not in posts[0]["json"]["text"]
    assert "trace-mm" in posts[-1]["json"]["text"]


def test_pipeline_replay_runs_from_repo_root(monkeypatch, tmp_path):
    FakeDeliverer.deliveries.clear()
    snapshot_path = tmp_path / "snapshot.json"
    out_dir = tmp_path / "out"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")

    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", FakeGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

    result = runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(out_dir),
        model="qwen35-397b-a17b",
        window="calendar_day",
        state=str(tmp_path / "state"),
        force=True,
        replay_ingest=str(snapshot_path),
    )

    payload = json.loads((out_dir / "digest-2026-03-29.json").read_text(encoding="utf-8"))

    assert result
    assert isinstance(result, RunDigestResult)
    assert result.citation_validation_ok is True
    assert [section["title"] for section in payload["sections"]] == [
        "Мои действия",
        "К сведению",
    ]
    assert FakeDeliverer.deliveries
    assert list(out_dir.glob("trace-*.meta.json"))


def test_pipeline_validate_citations_replay(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "snapshot.json"
    out_dir = tmp_path / "out"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")

    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", FakeGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

    result = runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(out_dir),
        model="qwen35-397b-a17b",
        window="calendar_day",
        state=str(tmp_path / "state"),
        validate_citations=True,
        force=True,
        replay_ingest=str(snapshot_path),
    )

    payload = json.loads((out_dir / "digest-2026-03-29.json").read_text(encoding="utf-8"))
    meta_path = next(out_dir.glob("trace-*.meta.json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    assert result.citation_validation_ok is True
    assert meta.get("citation_validation_ok") is True
    for section in payload["sections"]:
        for item in section["items"]:
            assert item.get("citations"), f"expected citations for item {item.get('evidence_id')}"


def test_pipeline_writes_partial_digest_on_llm_failure(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "snapshot.json"
    out_dir = tmp_path / "out"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")

    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", FailingGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

    result = runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(out_dir),
        model="qwen35-397b-a17b",
        window="calendar_day",
        state=str(tmp_path / "state"),
        force=True,
        replay_ingest=str(snapshot_path),
    )

    payload = json.loads((out_dir / "digest-2026-03-29.json").read_text(encoding="utf-8"))

    assert result
    assert isinstance(result, RunDigestResult)
    assert payload["sections"][0]["title"] == "Статус"
    assert "таймаут" in payload["sections"][0]["items"][0]["title"].lower()


def test_export_diagnostics_creates_bundle(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    log_path = tmp_path / "run.log"
    log_path.write_text("{}", encoding="utf-8")
    json_path = tmp_path / "digest-2026-03-29.json"
    json_path.write_text("{}", encoding="utf-8")
    md_path = tmp_path / "digest-2026-03-29.md"
    md_path.write_text("# test", encoding="utf-8")

    metadata = {
        "trace_id": "trace-export",
        "digest_date": "2026-03-29",
        "log_file": str(log_path),
        "artifact_paths": {"json": str(json_path), "md": str(md_path)},
        "pipeline_metrics": {"total_items": 1},
        "stage_durations_ms": {"ingest": 10},
        "status": "ok",
        "partial": False,
        "evidence_summary": {"chunk_count": 1},
        "ews_fetch_stats": {"message_count": 1},
        "llm_request_trace": {"retry_count": 0},
        "config_sanitized": {"deliver": {"mattermost": {"webhook_url_env": "MM_WEBHOOK_URL"}}},
    }
    (tmp_path / "trace-trace-export.meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )

    archive_path = export_diagnostics(
        trace_id="trace-export",
        out_dir=tmp_path / "bundle",
    )

    assert archive_path.exists()
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()

    assert any(name.endswith("pipeline-metrics.json") for name in names)
    assert any(name.endswith("config-sanitized.yaml") for name in names)


def test_export_diagnostics_cli(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    (tmp_path / "run.log").write_text("{}", encoding="utf-8")
    (tmp_path / "digest-2026-03-29.json").write_text("{}", encoding="utf-8")
    (tmp_path / "digest-2026-03-29.md").write_text("# test", encoding="utf-8")
    (tmp_path / "trace-trace-cli.meta.json").write_text(
        json.dumps(
            {
                "trace_id": "trace-cli",
                "digest_date": "2026-03-29",
                "log_file": str(tmp_path / "run.log"),
                "artifact_paths": {
                    "json": str(tmp_path / "digest-2026-03-29.json"),
                    "md": str(tmp_path / "digest-2026-03-29.md"),
                },
                "pipeline_metrics": {},
                "stage_durations_ms": {},
                "status": "ok",
                "partial": False,
                "evidence_summary": {},
                "ews_fetch_stats": {},
                "llm_request_trace": {},
                "config_sanitized": {},
            }
        ),
        encoding="utf-8",
    )

    cli_runner = CliRunner()
    result = cli_runner.invoke(
        app,
        [
            "export-diagnostics",
            "--trace-id",
            "trace-cli",
            "--out",
            str(tmp_path / "bundle"),
        ],
    )

    assert result.exit_code == 0
    assert "diagnostic-trace-cli-2026-03-29.tar.gz" in result.output
