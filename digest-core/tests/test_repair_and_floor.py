"""Non-generative repair + support-recall floor (PR11)."""

import pytest

from digest_core.config import RerankerConfig
from digest_core.evidence.citation_gate import CitationGate
from digest_core.evidence.citations import reselect_span
from digest_core.evidence.repair import repair_weak_items
from digest_core.eval.judge import JudgeVerdict
from digest_core.llm.schemas import Digest, EvidenceSpan, Item, Section
from digest_core.run import _support_recall

BODY = (
    "Первое предложение про бюджет. "
    "Пожалуйста, подготовь квартальный отчёт по продажам. "
    "Третье предложение про погоду."
)


# --- reselect_span (non-generative) -----------------------------------------


def test_reselect_span_picks_best_overlap_verbatim():
    span = reselect_span("подготовь квартальный отчёт", BODY)
    assert span == "Пожалуйста, подготовь квартальный отчёт по продажам."
    assert span in BODY  # verbatim substring


def test_reselect_span_empty_cases():
    assert reselect_span("", BODY) == ""
    assert reselect_span("xyz", "") == ""
    assert reselect_span("совершенно непохожие токены", BODY) == ""


# --- repair -----------------------------------------------------------------


def _weak_digest():
    item = Item(
        title="подготовь квартальный отчёт",
        evidence_id="ev-1",
        confidence=0.5,
        source_ref={"type": "email", "msg_id": "m-1"},
        evidence_spans=[EvidenceSpan(msg_id="m-1", quote="not in body")],
    )
    item.weak_evidence = True
    return Digest(
        schema_version="1.0",
        prompt_version="v",
        digest_date="d",
        trace_id="t",
        sections=[Section(title="Мои действия", items=[item])],
    )


class _ApprovingJudge:
    def judge_item(self, *args):
        return JudgeVerdict("k", True, 0.95)


def test_repair_judge_off_is_noop_keeps_weak_badge():
    digest = _weak_digest()
    outcome = repair_weak_items(digest, {"m-1": BODY}, gate=CitationGate({"m-1": BODY}), judge=None)
    assert outcome.items_repaired == 0
    assert outcome.items_weak == 1
    assert digest.sections[0].items[0].weak_evidence is True  # delivered, not dropped


def test_repair_with_cross_model_judge_clears_weak():
    digest = _weak_digest()
    gate = CitationGate({"m-1": BODY}, config=RerankerConfig())
    outcome = repair_weak_items(
        digest,
        {"m-1": BODY},
        gate=gate,
        tau_repair=0.5,
        judge=_ApprovingJudge(),
        proposer_model="qwen35-397b-a17b",
        judge_model="qwen35-35b-a3b",
    )
    assert outcome.items_repaired == 1
    item = digest.sections[0].items[0]
    assert item.weak_evidence is False
    assert item.evidence_spans[0].quote in BODY  # re-selected verbatim span


def test_repair_requires_distinct_judge_model():
    digest = _weak_digest()
    with pytest.raises(AssertionError):
        repair_weak_items(
            digest,
            {"m-1": BODY},
            gate=CitationGate({"m-1": BODY}),
            judge=_ApprovingJudge(),
            proposer_model="same",
            judge_model="same",
        )


# --- support recall ---------------------------------------------------------


def test_support_recall_excludes_system_and_counts_weak():
    items = [
        Item(
            title="a",
            evidence_id="ev-1",
            confidence=0.9,
            source_ref={"type": "email", "msg_id": "m"},
        ),
        Item(
            title="b",
            evidence_id="ev-2",
            confidence=0.9,
            source_ref={"type": "email", "msg_id": "m"},
        ),
        Item(title="sys", evidence_id="system", confidence=0.0, source_ref={"type": "system"}),
    ]
    items[0].citation_fidelity_ok = True
    items[1].citation_fidelity_ok = False
    items[1].weak_evidence = True
    digest = Digest(
        schema_version="1.0",
        prompt_version="v",
        digest_date="d",
        trace_id="t",
        sections=[Section(title="X", items=items)],
    )
    recall, weak = _support_recall(digest)
    assert recall == 0.5  # 1 of 2 evidence-backed items verified (system excluded)
    assert weak == 1
