"""LLM credential-expiry classification (enhancement program EP-3, frontier-audit F5).

A rejected LLM_TOKEN (HTTP 401/403) used to fall through as a bare
httpx.HTTPStatusError → generic partial digest. It must surface as a
non-retryable, operator-actionable LLMAuthError and a distinct degrade reason.
"""

import json
import os
from unittest.mock import patch

import httpx
import pytest

from digest_core import run as runner
from digest_core.config import LLMConfig
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.gateway import LLMAuthError, LLMGateway
from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


@pytest.fixture(autouse=True)
def _llm_token_env():
    with patch.dict(os.environ, {"LLM_TOKEN": "rotated-away"}):
        yield


def _evidence() -> list:
    return [
        EvidenceChunk(
            evidence_id="ev-auth-001",
            content="Please review the report by Friday.",
            source_ref={"type": "email", "msg_id": "msg-auth-001"},
            priority_score=2.0,
        )
    ]


def _gateway_returning(status_code: int, calls: list) -> LLMGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status_code, json={"error": "credentials rejected"})

    gateway = LLMGateway(LLMConfig(endpoint="http://test/v1/chat/completions"))
    gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
    return gateway


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_rejection_is_classified_and_not_retried(status_code):
    calls: list = []
    gateway = _gateway_returning(status_code, calls)

    with pytest.raises(LLMAuthError) as excinfo:
        gateway.extract_actions(_evidence(), "prompt", f"trace-auth-{status_code}")

    assert "LLM_TOKEN" in str(excinfo.value), "message must name the env var to refresh"
    assert str(status_code) in str(excinfo.value)
    assert len(calls) == 1, "a rejected token stays rejected — no retry"


class AuthFailingGateway(FakeGateway):
    def extract_actions(self, evidence, prompt_template, trace_id):
        raise LLMAuthError(
            "LLM gateway rejected credentials (HTTP 401): LLM_TOKEN is likely expired"
            " or rotated. Refresh the token, update ~/.config/actionpulse/env, then re-run."
        )


def test_pipeline_writes_actionable_partial_digest_on_auth_failure(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "snapshot.json"
    out_dir = tmp_path / "out"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")

    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", AuthFailingGateway)
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
    meta = json.loads(next(out_dir.glob("trace-*.meta.json")).read_text(encoding="utf-8"))

    assert result  # degrade-not-drop: the run still completes with a partial digest
    assert meta["status"] == "partial"
    status_section = payload["sections"][0]
    assert status_section["title"] == "Status"
    banner = status_section["items"][0]["title"]
    assert "LLM_TOKEN" in banner, "banner must tell the operator what to rotate"
    assert "401" in banner
    # the raw actionable error is preserved for diagnostics
    assert "actionpulse/env" in status_section["items"][0]["source_ref"]["error"]
