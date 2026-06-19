"""Per-run LLM budget visibility (decision D6 / ADR-008 v2, narrowed by C5/C8).

Every run must show the OPERATOR its call count and token spend vs budget — in
run_meta and in the structured log. It must NOT appear in the recipient-facing
MM message anymore (the trace/budget footer was removed).
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

from digest_core import run as runner
from digest_core.config import Config, LLMConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.gateway import LLMGateway
from digest_core.llm.schemas import Digest, Section
from digest_core.run import _llm_budget_summary
from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


def test_budget_summary_math():
    trace = {"run_tokens_used": 21476, "run_calls_made": 2}
    summary = _llm_budget_summary(trace, Config().llm)
    assert summary["calls_made"] == 2
    assert summary["extractor_call_budget"] == 2
    assert summary["tokens_used"] == 21476
    assert summary["max_tokens_per_run"] == 30000
    assert summary["tokens_pct"] == 71.6  # the real corp day, as it happens


def test_budget_summary_handles_missing_trace():
    summary = _llm_budget_summary({}, Config().llm)
    assert summary["calls_made"] == 0
    assert summary["tokens_used"] == 0


def test_gateway_counts_network_calls():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        body = json.dumps({"sections": []})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": body}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
            },
        )

    with patch.dict(os.environ, {"LLM_TOKEN": "mock"}):
        gateway = LLMGateway(LLMConfig(endpoint="http://test/v1/chat/completions"))
        gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
        gateway.extract_actions(
            [
                EvidenceChunk(
                    evidence_id="ev-b",
                    content="text",
                    source_ref={"type": "email", "msg_id": "m-b"},
                    priority_score=2.0,  # empty sections + positive → quality retry fires
                )
            ],
            "PROMPT",
            "trace-budget-vis",
        )
    assert len(calls) == 2
    assert gateway.last_request_meta["run_calls_made"] == 2


def _usage_handler(prompt_tokens: int, completion_tokens: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"sections": []})}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            },
        )

    return handler


def _chunk():
    return EvidenceChunk(
        evidence_id="ev",
        content="text",
        source_ref={"type": "email", "msg_id": "m"},
        priority_score=1.0,
    )


def test_cost_cap_trips_when_priced():
    # TD-006: with non-zero token prices, a run exceeding cost_limit_per_run raises.
    import pytest

    from digest_core.llm.gateway import CostBudgetExceeded

    with patch.dict(os.environ, {"LLM_TOKEN": "mock"}):
        cfg = LLMConfig(
            endpoint="http://test/v1/chat/completions",
            price_per_1k_input_usd=1.0,
            price_per_1k_output_usd=1.0,
            cost_limit_per_run=1.5,
        )
        gateway = LLMGateway(cfg)
        gateway.client = httpx.Client(transport=httpx.MockTransport(_usage_handler(1000, 1000)))
        # First call costs $2.00 (1k in + 1k out at $1/1k) > $1.50 cap.
        with pytest.raises(CostBudgetExceeded):
            gateway.extract_actions([_chunk()], "PROMPT", "trace-cost")


def test_cost_cap_noop_with_default_zero_prices():
    # Default $0 prices (the corp gateway) → cost stays 0, so even a tiny cap never trips.
    with patch.dict(os.environ, {"LLM_TOKEN": "mock"}):
        cfg = LLMConfig(endpoint="http://test/v1/chat/completions", cost_limit_per_run=0.000001)
        gateway = LLMGateway(cfg)
        gateway.client = httpx.Client(transport=httpx.MockTransport(_usage_handler(100, 50)))
        gateway.extract_actions([_chunk()], "PROMPT", "trace-free")
        assert gateway.last_request_meta["run_cost_usd"] == 0.0


def test_mm_message_never_carries_budget():
    # Owner C5/C8: the budget is operator-only. Even when a budget is threaded
    # for signature compatibility, the delivered message must not surface it.
    deliverer = MattermostDeliverer(Config().deliver.mattermost)
    digest = Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-06-11",
        trace_id="t-budget",
        sections=[Section(title="Мои действия", items=[])],
    )
    budget = {
        "calls_made": 2,
        "tokens_used": 21476,
        "max_tokens_per_run": 30000,
        "tokens_pct": 71.6,
    }
    text = deliverer._format_digest(digest, None, budget)
    assert "llm:" not in text
    assert "21476" not in text
    assert "_trace:" not in text
    assert "items:" not in text

    plain = deliverer._format_digest(digest, None, None)
    assert "llm:" not in plain


def test_run_meta_carries_llm_budget(monkeypatch, tmp_path):
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
        state=str(tmp_path / "state"),
        force=True,
        replay_ingest=str(snapshot_path),
    )

    meta = json.loads(next(out_dir.glob("trace-*.meta.json")).read_text(encoding="utf-8"))
    budget = meta["llm_budget"]
    assert set(budget) == {
        "calls_made",
        "extractor_call_budget",
        "tokens_used",
        "max_tokens_per_run",
        "tokens_pct",
    }
    assert budget["max_tokens_per_run"] == 30000
