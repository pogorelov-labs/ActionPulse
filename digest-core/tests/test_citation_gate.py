"""P2 citation gate in shadow mode (PR8)."""

import json

from digest_core.config import RerankerConfig
from digest_core.evidence.citation_gate import CitationGate, normalize_confidence
from digest_core.llm.schemas import Digest, EvidenceSpan, Item, Section

BODY = "Пожалуйста, пришли отчёт до пятницы и согласуй бюджет."


def _digest(items):
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-03-29",
        trace_id="t",
        sections=[Section(title="Мои действия", items=items)],
    )


def _item(**overrides):
    base = dict(
        title="Прислать отчёт",
        evidence_id="ev-1",
        confidence=0.9,
        source_ref={"type": "email", "msg_id": "m-1"},
        evidence_spans=[EvidenceSpan(msg_id="m-1", quote="пришли отчёт до пятницы")],
    )
    base.update(overrides)
    return Item(**base)


def test_offset_ok_when_span_in_body():
    digest = CitationGate({"m-1": BODY}).annotate(_digest([_item()]))
    item = digest.sections[0].items[0]
    assert item.citation_fidelity_ok is True
    assert item.weak_evidence is False
    assert item.support_score is None


def test_weak_when_span_not_in_body():
    digest = CitationGate({"m-1": "unrelated body"}).annotate(_digest([_item()]))
    item = digest.sections[0].items[0]
    assert item.citation_fidelity_ok is False
    assert item.weak_evidence is True


def test_weak_when_no_spans():
    digest = CitationGate({"m-1": BODY}).annotate(_digest([_item(evidence_spans=[])]))
    item = digest.sections[0].items[0]
    assert item.citation_fidelity_ok is False
    assert item.weak_evidence is True


def test_system_item_not_annotated():
    sys_item = Item(
        title="Статус", evidence_id="system", confidence=0.0, source_ref={"type": "system"}
    )
    digest = CitationGate({}).annotate(_digest([sys_item]))
    item = digest.sections[0].items[0]
    assert item.citation_fidelity_ok is None
    assert item.weak_evidence is None


def test_reranker_off_makes_no_calls():
    class BoomReranker:
        def score(self, query, docs):
            raise AssertionError("reranker must not be called when disabled")

    gate = CitationGate(
        {"m-1": BODY}, reranker=BoomReranker(), config=RerankerConfig(enabled=False)
    )
    digest = gate.annotate(_digest([_item(confidence=0.3)]))
    assert digest.sections[0].items[0].support_score is None
    assert gate.reranker_calls == 0


def test_reranker_scores_low_confidence_only():
    class StubReranker:
        def score(self, query, docs):
            return [0.42]

    cfg = RerankerConfig(enabled=True, low_confidence_threshold=0.7, tau=0.5, budget_per_run=10)

    low = CitationGate({"m-1": BODY}, reranker=StubReranker(), config=cfg).annotate(
        _digest([_item(confidence=0.3)])
    )
    item = low.sections[0].items[0]
    assert item.support_score == 0.42
    assert item.weak_evidence is True  # 0.42 < tau 0.5

    high = CitationGate({"m-1": BODY}, reranker=StubReranker(), config=cfg).annotate(
        _digest([_item(confidence=0.95)])
    )
    assert high.sections[0].items[0].support_score is None  # high-conf not spent


def test_reranker_budget_enforced():
    class StubReranker:
        def score(self, query, docs):
            return [0.9]

    cfg = RerankerConfig(enabled=True, low_confidence_threshold=0.99, budget_per_run=2, tau=0.0)
    gate = CitationGate({"m-1": BODY}, reranker=StubReranker(), config=cfg)
    gate.annotate(_digest([_item(confidence=0.1) for _ in range(5)]))
    assert gate.reranker_calls == 2


def test_normalize_confidence():
    assert normalize_confidence(0.8) == 0.8
    assert normalize_confidence("High") == 0.9
    assert normalize_confidence("low") == 0.3
    assert normalize_confidence("weird") == 0.5


def test_shadow_gate_annotates_in_live_pipeline(tmp_path):
    from digest_core.eval.corpus import load_corpus
    from digest_core.eval.replay_harness import run_case

    case = load_corpus()[0]
    run_case(case, tmp_path)
    digest = json.loads((tmp_path / f"digest-{case.digest_date}.json").read_text(encoding="utf-8"))
    items = [item for section in digest["sections"] for item in section["items"]]
    assert items
    assert all("citation_fidelity_ok" in item for item in items)
