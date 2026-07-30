"""A1 slice 1: the v3 typed-section item models carry the P2 traceability backbone.

Reviving v3 as the constrained-decoding target must NOT regress P2 (Traceability —
the #1 golden rule). v3 originally had only a bare ``quote`` string; the
``_TraceBackbone`` mixin grafts ``evidence_spans`` + ``citations`` + the P2-gate
annotations onto every typed item so the migration target keeps full traceability.
The fields are optional/defaulted, so pre-existing v3 callers/tests are unaffected.
"""

import pytest

from digest_core.llm.schemas import (
    ActionItemV3,
    Citation,
    DeadlineMeetingV3,
    EvidenceSpan,
    FYIItemV3,
    RiskBlockerV3,
)

# (model class, minimal required kwargs for that typed item)
V3_ITEM_TYPES = [
    (ActionItemV3, dict(title="t", description="d", evidence_id="e", quote="q", confidence="High")),
    (
        DeadlineMeetingV3,
        dict(title="t", evidence_id="e", quote="q", date_time="2026-07-02T10:00:00+03:00"),
    ),
    (RiskBlockerV3, dict(title="t", evidence_id="e", quote="q", severity="High", impact="x")),
    (FYIItemV3, dict(title="t", evidence_id="e", quote="q")),
]


@pytest.mark.parametrize("cls, kwargs", V3_ITEM_TYPES)
def test_every_v3_item_has_the_traceability_backbone(cls, kwargs):
    item = cls(**kwargs)
    # The evidence-span / citation backbone exists and defaults empty.
    assert item.evidence_spans == []
    assert item.citations == []
    # P2-gate annotations exist and default unset (populated downstream).
    assert item.citation_fidelity_ok is None
    assert item.support_score is None
    assert item.weak_evidence is None
    assert item.rank_score is None
    assert item.seen_before is None


def test_v3_item_round_trips_with_spans_and_gate():
    item = ActionItemV3(
        title="Send Q3 numbers",
        description="Finance asked for the Q3 figures.",
        evidence_id="ev-1",
        quote="please send the Q3 numbers by Friday",
        confidence="High",
        owners=["user"],
        evidence_spans=[EvidenceSpan(msg_id="m1", quote="send the Q3 numbers by Friday")],
        citations=[
            Citation(msg_id="m1", start=10, end=39, preview="send the Q3 numbers by Friday")
        ],
        citation_fidelity_ok=True,
        support_score=0.91,
        weak_evidence=False,
    )
    dumped = item.model_dump()
    assert dumped["evidence_spans"][0]["msg_id"] == "m1"
    assert dumped["citations"][0]["end"] == 39
    assert dumped["citation_fidelity_ok"] is True
    # Re-validate from the dump (the contract round-trips).
    assert ActionItemV3.model_validate(dumped).support_score == pytest.approx(0.91)


def test_every_backbone_field_is_classified_as_extractor_or_downstream():
    """Growing the backbone must force a decision about who fills the new field.

    Without this, adding a field to `_TraceBackbone` silently leaks it into the
    constrained-decoding contract (A1.2a projects out `DOWNSTREAM_ONLY`), and the
    model gets asked for a value it has no basis to produce.
    """
    from digest_core.llm.schemas import _TraceBackbone

    backbone = set(_TraceBackbone.model_fields)
    extractor_owned = {"evidence_spans"}
    assert backbone == extractor_owned | _TraceBackbone.DOWNSTREAM_ONLY, (
        "unclassified backbone field(s): "
        f"{sorted(backbone - extractor_owned - _TraceBackbone.DOWNSTREAM_ONLY)} — "
        "decide whether the extractor emits it or the pipeline computes it"
    )
