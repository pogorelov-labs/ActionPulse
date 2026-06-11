"""Non-generative citation repair (PR11 + EP-12 part 2, R3/R4).

For each weak item, re-select a VERBATIM span from the same chunk body and accept it
only if it clears a higher bar (``tau_repair``) AND a CROSS-MODEL judge approves
(``assert judge_model != proposer_model``). Otherwise the item keeps its
``weak_evidence`` badge and is delivered anyway (degrade-not-drop) — with D1's
quarantine on, that means the trailing «Не подтверждено» section; a repaired item
escapes it. This is the quarantine's rescue path.

Repair is substring-only and replay-safe. The judge is OFF by default
(``judge.enabled``; D4 resolved PC-2, live flip waits for EP-14 corp validation),
so the default path is a no-op that simply counts the weak items. Any judge
failure mid-run keeps the weak badge and never crashes; exhausting the judge's
stage call budget (8/run) stops further repair attempts for the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import structlog

from digest_core.evidence.citation_gate import CitationGate
from digest_core.evidence.citations import reselect_span
from digest_core.llm.rate_broker import StageCallBudgetExceeded
from digest_core.llm.schemas import Digest, EvidenceSpan

logger = structlog.get_logger()


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
    active_judge = judge
    for section in digest.sections:
        for item in section.items:
            if not getattr(item, "weak_evidence", False):
                continue
            try:
                fixed = _try_repair(
                    item,
                    normalized_messages_map,
                    gate,
                    tau_repair,
                    active_judge,
                    proposer_model,
                    judge_model,
                )
            except StageCallBudgetExceeded as exc:
                # Budget spent: stop attempting for the rest of the run; the
                # remaining weak items keep their badge (degrade-not-drop).
                logger.warning(
                    "Repair judge stage budget exhausted; remaining weak items stay weak",
                    budget=exc.budget,
                    evidence_id=item.evidence_id,
                )
                active_judge = None
                fixed = False
            if fixed:
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

    try:
        verdict = judge.judge_item(item.evidence_id, item.title, new_quote, body)
    except StageCallBudgetExceeded:
        raise  # caller deactivates the judge for the rest of the run
    except Exception as exc:
        # Degrade-not-drop (R3): a judge failure (429 after retries, timeout,
        # auth, malformed verdict) keeps the weak badge — never crashes the run.
        logger.warning(
            "Repair judge call failed; item keeps its weak badge",
            error_type=type(exc).__name__,
            evidence_id=item.evidence_id,
        )
        return False
    if not (verdict.supported and verdict.prob_supported >= tau_repair):
        return False

    item.evidence_spans = [EvidenceSpan(msg_id=msg_id, quote=new_quote)]
    gate._annotate_item(item, None)  # re-annotate fidelity/weak with the new span
    return not getattr(item, "weak_evidence", True)
