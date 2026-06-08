"""Non-generative citation repair (PR11, R3/R4).

For each weak item, re-select a VERBATIM span from the same chunk body and accept it
only if it clears a higher bar (``tau_repair``) AND a CROSS-MODEL judge approves
(``assert judge_model != proposer_model``). Otherwise the item keeps its
``weak_evidence`` badge and is delivered anyway (degrade-not-drop).

Repair is substring-only and replay-safe. The judge is OFF by default (PC-2), so the
default path is a no-op that simply counts the weak items.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from digest_core.evidence.citation_gate import CitationGate
from digest_core.evidence.citations import reselect_span
from digest_core.llm.schemas import Digest, EvidenceSpan


@dataclass
class RepairOutcome:
    items_repaired: int
    items_weak: int


def repair_weak_items(
    digest: Digest,
    normalized_messages_map: Dict[str, str],
    *,
    gate: CitationGate,
    tau_repair: float = 0.0,
    judge=None,
    proposer_model: str = "",
    judge_model: str = "",
) -> RepairOutcome:
    repaired = 0
    weak = 0
    for section in digest.sections:
        for item in section.items:
            if not getattr(item, "weak_evidence", False):
                continue
            if _try_repair(
                item, normalized_messages_map, gate, tau_repair, judge, proposer_model, judge_model
            ):
                repaired += 1
            else:
                weak += 1
    return RepairOutcome(items_repaired=repaired, items_weak=weak)


def _try_repair(item, bodies, gate, tau_repair, judge, proposer_model, judge_model) -> bool:
    if judge is None:
        return False  # judge off -> degrade-not-drop, keep the weak badge
    assert judge_model and judge_model != proposer_model, "repair judge must differ (R4)"

    msg_id = (item.source_ref or {}).get("msg_id", "")
    body = bodies.get(msg_id)
    if not body:
        return False
    new_quote = reselect_span(item.title, body)
    if not new_quote or new_quote not in body:
        return False

    verdict = judge.judge_item(item.evidence_id, item.title, new_quote, body)
    if not (verdict.supported and verdict.prob_supported >= tau_repair):
        return False

    item.evidence_spans = [EvidenceSpan(msg_id=msg_id, quote=new_quote)]
    gate._annotate_item(item, None)  # re-annotate fidelity/weak with the new span
    return not getattr(item, "weak_evidence", True)
