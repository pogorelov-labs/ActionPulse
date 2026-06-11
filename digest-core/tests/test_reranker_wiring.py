"""Reranker → citation gate wiring (EP-12 part 1, D4/PC-2).

Covers the run.py construction seam (`_build_reranker`), the degrade-not-drop
contract of the gate when the fleet fails (429, stage budget, transport), and
the real-RerankerClient-through-the-gate path on a mock transport. The flag
stays OFF by default — flag-off byte-identity is held by the frozen eval-replay
baseline plus `test_flag_off_constructs_no_reranker` here.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

import digest_core.run as run_module
from digest_core.config import LLMConfig, RerankerConfig
from digest_core.evidence.citation_gate import CitationGate
from digest_core.llm.fleet import RerankerClient
from digest_core.llm.rate_broker import RateBroker
from digest_core.llm.schemas import Digest, EvidenceSpan, Item, Section
from digest_core.run import _apply_shadow_citation_gate, _build_reranker

BODY = "Пожалуйста, пришли отчёт до пятницы и согласуй бюджет."


def _digest(items):
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-03-29",
        trace_id="t",
        sections=[Section(title="Мои действия", items=items)],
    )


def _item(n=1, **overrides):
    base = dict(
        title="Прислать отчёт",
        evidence_id=f"ev-{n}",
        confidence=0.3,
        source_ref={"type": "email", "msg_id": "m-1"},
        evidence_spans=[EvidenceSpan(msg_id="m-1", quote="пришли отчёт до пятницы")],
    )
    base.update(overrides)
    return Item(**base)


def _resp(payload, *, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.raise_for_status = Mock()
    response.json.return_value = payload
    return response


def _ctx(reranker_cfg, *, replay_llm=None, record_llm=None, broker=None):
    return SimpleNamespace(
        config=SimpleNamespace(
            reranker=reranker_cfg,
            llm=LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b"),
        ),
        replay_llm=replay_llm,
        record_llm=record_llm,
        rate_broker=broker,
        trace_id="t",
        metrics=None,
        run_meta={},
    )


# --- gate degrade-not-drop ----------------------------------------------------


def test_gate_degrades_to_fidelity_only_on_score_failure():
    class FlakyReranker:
        calls = 0

        def score(self, query, docs):
            FlakyReranker.calls += 1
            raise httpx.ConnectError("boom")

    cfg = RerankerConfig(enabled=True, low_confidence_threshold=0.7, budget_per_run=10)
    gate = CitationGate({"m-1": BODY}, reranker=FlakyReranker(), config=cfg)
    digest = gate.annotate(_digest([_item(1), _item(2)]))

    items = digest.sections[0].items
    # No crash, no drop: both items annotated, fidelity-only.
    assert all(item.citation_fidelity_ok is True for item in items)
    assert all(item.support_score is None for item in items)
    assert all(item.weak_evidence is False for item in items)
    # One failed attempt, then the reranker is nulled for the rest of the run.
    assert FlakyReranker.calls == 1
    assert gate.reranker is None


def test_real_client_score_plumbs_through_gate(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    client = RerankerClient(
        LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b")
    )
    client._client = Mock()
    client._client.post = Mock(
        return_value=_resp({"results": [{"index": 0, "relevance_score": 0.83}]})
    )
    cfg = RerankerConfig(enabled=True, low_confidence_threshold=0.7, tau=0.5, budget_per_run=10)
    gate = CitationGate({"m-1": BODY}, reranker=client, config=cfg)
    digest = gate.annotate(_digest([_item()]))

    item = digest.sections[0].items[0]
    assert item.support_score == 0.83
    assert item.weak_evidence is False  # 0.83 >= tau 0.5 and offset ok
    assert client._client.post.call_args[0][0] == "https://gw.corp/api/rerank"


def test_stage_budget_exhaustion_degrades_gate(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    broker = RateBroker(
        fleet_rpm={"bge-reranker-v2-m3": 10},
        stage_call_budgets={"reranker": 1},
        sleep=lambda d: None,
    )
    client = RerankerClient(
        LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b"),
        rate_broker=broker,
        stage="reranker",
    )
    client._client = Mock()
    client._client.post = Mock(return_value=_resp({"scores": [0.9]}))

    cfg = RerankerConfig(enabled=True, low_confidence_threshold=0.7, tau=0.0, budget_per_run=10)
    gate = CitationGate({"m-1": BODY}, reranker=client, config=cfg)
    digest = gate.annotate(_digest([_item(1), _item(2), _item(3)]))

    items = digest.sections[0].items
    assert items[0].support_score == 0.9  # first call within the stage budget
    assert items[1].support_score is None  # budget hit -> degrade
    assert items[2].support_score is None
    assert gate.reranker is None  # fidelity-only for the rest of the run
    assert all(item.weak_evidence is False for item in items)  # offsets still ok


def test_429_penalizes_bucket_and_degrades_gate(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    broker = RateBroker(fleet_rpm={"bge-reranker-v2-m3": 10}, sleep=lambda d: None)
    client = RerankerClient(
        LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b"),
        rate_broker=broker,
    )
    err_response = Mock(status_code=429, headers={"Retry-After": "30"})
    http_error = httpx.HTTPStatusError("429", request=Mock(), response=err_response)
    bad = Mock()
    bad.raise_for_status = Mock(side_effect=http_error)
    client._client = Mock()
    client._client.post = Mock(return_value=bad)

    cfg = RerankerConfig(enabled=True, low_confidence_threshold=0.7, budget_per_run=10)
    gate = CitationGate({"m-1": BODY}, reranker=client, config=cfg)
    digest = gate.annotate(_digest([_item(1), _item(2)]))

    assert all(item.support_score is None for item in digest.sections[0].items)
    assert gate.reranker is None
    assert broker.acquire("bge-reranker-v2-m3") >= 60.0  # cool-down floor applied


# --- run.py construction seam -------------------------------------------------


def test_build_reranker_flag_off_returns_none():
    assert _build_reranker(_ctx(RerankerConfig(enabled=False))) is None


def test_build_reranker_replay_without_sidecar_disables(tmp_path):
    recording = tmp_path / "llm.json"
    recording.write_text("{}", encoding="utf-8")
    ctx = _ctx(RerankerConfig(enabled=True), replay_llm=str(recording))
    assert _build_reranker(ctx) is None


def test_build_reranker_replay_with_sidecar_uses_it(tmp_path):
    recording = tmp_path / "llm.json"
    recording.write_text("{}", encoding="utf-8")
    sidecar = tmp_path / "llm.json.fleet.json"
    sidecar.write_text(
        json.dumps(
            {"endpoints": {"rerank": [{"request_hash": "x", "response": {"scores": [1.0]}}]}}
        ),
        encoding="utf-8",
    )
    client = _build_reranker(_ctx(RerankerConfig(enabled=True), replay_llm=str(recording)))
    assert client is not None
    assert client._replay_data is not None  # zero-network replay mode
    assert client.score("q", ["d"]) == [1.0]


def test_build_reranker_live_uses_config_knobs():
    broker = RateBroker(sleep=lambda d: None)
    cfg = RerankerConfig(enabled=True, model="bge-reranker-v2-m3", endpoint_path="/rerank")
    client = _build_reranker(_ctx(cfg, record_llm="/tmp/rec.json", broker=broker))
    assert isinstance(client, RerankerClient)
    assert client.model == "bge-reranker-v2-m3"
    assert client.endpoint_path == "/rerank"
    assert client._stage == "reranker"
    assert client._broker is broker
    assert str(client._record_path) == "/tmp/rec.json.fleet.json"
    client.close()


def test_flag_off_constructs_no_reranker(monkeypatch):
    """Default config must not even construct a fleet client (byte-identity proof)."""

    def boom(*args, **kwargs):
        raise AssertionError("RerankerClient must not be constructed with the flag off")

    monkeypatch.setattr(run_module, "RerankerClient", boom)
    ctx = _ctx(RerankerConfig())  # enabled=False is the default
    messages = [SimpleNamespace(msg_id="m-1", text_body=BODY)]
    digest = _apply_shadow_citation_gate(ctx, _digest([_item()]), messages)
    item = digest.sections[0].items[0]
    assert item.citation_fidelity_ok is True
    assert item.support_score is None
    assert "fleet_reranker_calls" not in ctx.run_meta
