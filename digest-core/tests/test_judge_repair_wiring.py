"""Judge → repair wiring (EP-12 part 2, D1 rescue path, D4/PC-2).

Covers the gateway's small-verdict ``judge()`` call, the run.py construction
guards (`_build_repair_judge`), and the repair loop's degrade-not-drop behavior:
a repaired item escapes D1's quarantine, a rejected/failed one keeps its weak
badge, and the judge stage budget (8/run) deactivates repair instead of
crashing. ``judge.enabled`` stays OFF by default — flag-off runs construct no
judge gateway and stay byte-identical (frozen eval-replay baseline).
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

import digest_core.pipeline.enrichment as enrichment_module
import digest_core.run as run_module
from digest_core.config import JudgeConfig, LLMConfig, RerankerConfig
from digest_core.eval.judge import JudgeVerdict, LLMJudge
from digest_core.evidence.citation_gate import CitationGate
from digest_core.evidence.repair import repair_weak_items
from digest_core.llm.gateway import LLMGateway
from digest_core.llm.rate_broker import RateBroker, StageCallBudgetExceeded
from digest_core.llm.schemas import Digest, EvidenceSpan, Item, Section
from digest_core.run import _build_repair_judge, _quarantine_weak_items

BODY = (
    "Первое предложение про бюджет. "
    "Пожалуйста, подготовь квартальный отчёт по продажам. "
    "Третье предложение про погоду."
)


def _weak_item(n=1):
    item = Item(
        title="подготовь квартальный отчёт",
        evidence_id=f"ev-{n}",
        confidence=0.5,
        source_ref={"type": "email", "msg_id": "m-1"},
        evidence_spans=[EvidenceSpan(msg_id="m-1", quote="not in body")],
    )
    item.weak_evidence = True
    return item


def _digest(items):
    return Digest(
        schema_version="1.0",
        prompt_version="v",
        digest_date="d",
        trace_id="t",
        sections=[Section(title="Мои действия", items=items)],
    )


def _ctx(judge_cfg, *, replay_llm=None, broker=None):
    return SimpleNamespace(
        config=SimpleNamespace(
            judge=judge_cfg,
            reranker=RerankerConfig(),
            llm=LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b"),
        ),
        replay_llm=replay_llm,
        rate_broker=broker,
        trace_id="t",
        metrics=None,
        run_meta={},
        sink=None,
    )


def _chat_resp(content: str):
    response = Mock()
    response.status_code = 200
    response.raise_for_status = Mock()
    response.headers = {}
    response.json.return_value = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    return response


# --- gateway.judge() ----------------------------------------------------------


def test_gateway_judge_returns_parsed_verdict(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    gateway = LLMGateway(LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-35b-a3b"))
    gateway.client = Mock()
    gateway.client.post = Mock(
        return_value=_chat_resp('{"supported": true, "prob_supported": 0.9}')
    )
    assert gateway.judge("system", "user") == {"supported": True, "prob_supported": 0.9}
    payload = gateway.client.post.call_args.kwargs["json"]
    assert payload["model"] == "qwen35-35b-a3b"


def test_gateway_judge_respects_stage_budget(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    broker = RateBroker(
        fleet_rpm={"qwen35-35b-a3b": 30},
        stage_call_budgets={"judge": 1},
        sleep=lambda d: None,
    )
    gateway = LLMGateway(
        LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-35b-a3b"),
        rate_broker=broker,
        stage="judge",
    )
    gateway.client = Mock()
    gateway.client.post = Mock(return_value=_chat_resp('{"supported": false}'))

    assert gateway.judge("s", "u")["supported"] is False
    with pytest.raises(StageCallBudgetExceeded):
        gateway.judge("s", "u2")


def test_llm_judge_over_gateway_parses_verdict(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    gateway = LLMGateway(LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-35b-a3b"))
    gateway.client = Mock()
    gateway.client.post = Mock(
        return_value=_chat_resp('{"supported": true, "prob_supported": 0.85}')
    )
    verdict = LLMJudge(gateway).judge_item("k", "title", "span", "body")
    assert verdict.supported is True
    assert verdict.prob_supported == 0.85


# --- run.py construction guards ------------------------------------------------


def test_build_repair_judge_flag_off():
    judge, gateway = _build_repair_judge(_ctx(JudgeConfig(enabled=False)))
    assert judge is None and gateway is None


def test_build_repair_judge_requires_distinct_model():
    judge, gateway = _build_repair_judge(_ctx(JudgeConfig(enabled=True, model="qwen35-397b-a17b")))
    assert judge is None and gateway is None


def test_build_repair_judge_disabled_under_replay(tmp_path):
    recording = tmp_path / "llm.json"
    recording.write_text("{}", encoding="utf-8")
    judge, gateway = _build_repair_judge(_ctx(JudgeConfig(enabled=True), replay_llm=str(recording)))
    assert judge is None and gateway is None


def test_build_repair_judge_constructs_cross_model_gateway():
    broker = RateBroker(sleep=lambda d: None)
    judge, gateway = _build_repair_judge(_ctx(JudgeConfig(enabled=True), broker=broker))
    assert isinstance(judge, LLMJudge)
    assert gateway.config.model == "qwen35-35b-a3b"
    assert gateway.config.max_output_tokens == run_module.JUDGE_MAX_OUTPUT_TOKENS
    assert gateway._stage == "judge"
    assert gateway._rate_broker is broker
    # The extractor's config is untouched by the model override.
    gateway.close()


def test_flag_off_constructs_no_judge_gateway(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("LLMGateway must not be constructed for a disabled judge")

    # Same reason as test_reranker_wiring: `_build_repair_judge` resolves
    # LLMGateway in pipeline/enrichment.py's namespace since phase 3, so the
    # sentinel has to be installed there to stay reachable.
    monkeypatch.setattr(enrichment_module, "LLMGateway", boom)
    judge, gateway = _build_repair_judge(_ctx(JudgeConfig()))  # enabled=False default
    assert judge is None and gateway is None


# --- repair loop: rescue path + degrade-not-drop -------------------------------


class _ScriptedJudge:
    """Judge double driven by a list of verdicts / exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def judge_item(self, *args):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_repaired_item_escapes_quarantine():
    digest = _digest([_weak_item()])
    outcome = repair_weak_items(
        digest,
        {"m-1": BODY},
        gate=CitationGate({"m-1": BODY}, config=RerankerConfig()),
        tau_repair=0.5,
        judge=_ScriptedJudge([JudgeVerdict("k", True, 0.95)]),
        proposer_model="qwen35-397b-a17b",
        judge_model="qwen35-35b-a3b",
    )
    assert outcome.items_repaired == 1
    assert _quarantine_weak_items(digest) == 0  # nothing left to quarantine
    item = digest.sections[0].items[0]
    assert item.weak_evidence is False
    assert item.evidence_spans[0].quote in BODY


def test_rejected_item_lands_in_quarantine():
    digest = _digest([_weak_item()])
    outcome = repair_weak_items(
        digest,
        {"m-1": BODY},
        gate=CitationGate({"m-1": BODY}, config=RerankerConfig()),
        tau_repair=0.5,
        judge=_ScriptedJudge([JudgeVerdict("k", False, 0.1)]),
        proposer_model="qwen35-397b-a17b",
        judge_model="qwen35-35b-a3b",
    )
    assert outcome.items_repaired == 0
    assert outcome.items_weak == 1
    assert _quarantine_weak_items(digest) == 1
    assert digest.sections[-1].title == "Unconfirmed"


def test_budget_exhaustion_stops_repair_keeps_items():
    digest = _digest([_weak_item(1), _weak_item(2), _weak_item(3)])
    judge = _ScriptedJudge([JudgeVerdict("k", True, 0.95), StageCallBudgetExceeded("judge", 1)])
    outcome = repair_weak_items(
        digest,
        {"m-1": BODY},
        gate=CitationGate({"m-1": BODY}, config=RerankerConfig()),
        tau_repair=0.5,
        judge=judge,
        proposer_model="qwen35-397b-a17b",
        judge_model="qwen35-35b-a3b",
    )
    assert outcome.items_repaired == 1
    assert outcome.items_weak == 2
    assert judge.calls == 2  # budget hit deactivates the judge: no third call
    items = digest.sections[0].items
    assert items[0].weak_evidence is False
    assert items[1].weak_evidence is True and items[2].weak_evidence is True


def test_judge_transport_failure_keeps_weak_never_crashes():
    digest = _digest([_weak_item(1), _weak_item(2)])
    judge = _ScriptedJudge([httpx.ConnectError("offline"), JudgeVerdict("k", True, 0.95)])
    outcome = repair_weak_items(
        digest,
        {"m-1": BODY},
        gate=CitationGate({"m-1": BODY}, config=RerankerConfig()),
        tau_repair=0.5,
        judge=judge,
        proposer_model="qwen35-397b-a17b",
        judge_model="qwen35-35b-a3b",
    )
    # First item degraded (kept weak), second still attempted and repaired.
    assert outcome.items_repaired == 1
    assert outcome.items_weak == 1
    assert digest.sections[0].items[0].weak_evidence is True
    assert digest.sections[0].items[1].weak_evidence is False


def test_malformed_verdict_degrades_to_weak(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    gateway = LLMGateway(LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-35b-a3b"))
    gateway.client = Mock()
    # Valid JSON, wrong shape: no "supported" key -> verdict falls to False.
    gateway.client.post = Mock(return_value=_chat_resp(json.dumps({"verdict": "да"})))
    digest = _digest([_weak_item()])
    outcome = repair_weak_items(
        digest,
        {"m-1": BODY},
        gate=CitationGate({"m-1": BODY}, config=RerankerConfig()),
        tau_repair=0.5,
        judge=LLMJudge(gateway),
        proposer_model="qwen35-397b-a17b",
        judge_model="qwen35-35b-a3b",
    )
    assert outcome.items_repaired == 0
    assert digest.sections[0].items[0].weak_evidence is True
