"""Adapt a v3 constrained extraction into the live ``Digest`` (A1.4).

Why an adapter instead of a pipeline-wide rewire
------------------------------------------------
The point of A1 is the **extraction contract**: constrain generation to a schema
so malformed / off-schema output becomes impossible and the quality-retry stops
being load-bearing. Making the *whole pipeline* speak v3 natively was the assumed
implementation, never the goal — and it would mean rewriting ~12 post-LLM passes,
the citation gate, assemble, the reader, the store and the labels module in one
change, which the 2026-07 review called the single riskiest item on the roadmap.

So the v3 payload is converted to the live ``Digest`` immediately after parsing.
Everything downstream keeps working unchanged, and the risk of the flip drops from
"fifteen files" to "one function with a truth table".

What v3 buys even through the adapter
-------------------------------------
* **Section identity becomes structural.** v1 asks the model to emit a section
  *title string* and then matches on it. v3 gives typed lists, so the section is
  decided by which list the item is in — the canonical key is derived, never parsed.
* **``source_ref`` stops being model-supplied.** v1 requires the model to echo
  ``source_ref`` and validates it (``gateway._validate_*``). Here it is looked up
  from the ``EvidenceChunk`` the pipeline itself issued, so the model cannot get it
  wrong; an item citing an unknown ``evidence_id`` is dropped rather than trusted.
* **``severity`` routes risks.** A High-severity blocker leads the digest; lower
  ones read as informational.

Known lossiness, stated rather than hidden
------------------------------------------
* v3 expresses confidence as High/Medium/Low; the live ``Item.confidence`` is a
  float. The mapping below uses the midpoints of the bands the v1 prompt already
  documents, so a v3 digest sorts and badges like a v1 one — but the model can no
  longer express 0.83 vs 0.87.
* ``owners`` / ``participants`` / ``location`` / ``impact`` have no home on the v1
  ``Item`` yet, so they are not carried. Adding them is a follow-up; nothing here
  depends on their absence.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import structlog

from digest_core.assemble.labels import FYI, MEETINGS, MY_ACTIONS, URGENT, section_title
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.schemas import Digest, EnhancedDigestV3, Item, Section

logger = structlog.get_logger()

#: v3 says High/Medium/Low; ``Item.confidence`` is a float. These are the midpoints
#: of the bands the v1 extraction prompt documents (0.90-1.00 explicit request plus
#: deadline/urgency; 0.70-0.89 clear action, no hard deadline; 0.50-0.69 implied but
#: clear), so ranking and the low-confidence badge behave the same across schemas.
CONFIDENCE_BY_WORD: Dict[str, float] = {"high": 0.95, "medium": 0.80, "low": 0.60}

#: Fallback when the model returns a word outside the enum. Deliberately the *low*
#: band: an unparseable confidence is not evidence of a confident item.
CONFIDENCE_FALLBACK = CONFIDENCE_BY_WORD["low"]


def _confidence(word: str | None) -> float:
    return CONFIDENCE_BY_WORD.get((word or "").strip().lower(), CONFIDENCE_FALLBACK)


def v3_to_digest(
    v3: EnhancedDigestV3,
    *,
    evidence: Sequence[EvidenceChunk],
    digest_date: str,
    trace_id: str,
    prompt_version: str,
    language: str,
    total_emails_processed: int = 0,
) -> Tuple[Digest, Dict[str, Any]]:
    """Flatten a v3 extraction into the live ``Digest``.

    Returns ``(digest, stats)``. ``stats`` reports what was dropped and why —
    the project rule is no silent caps, and an item vanishing between the model
    and the report is exactly the kind of loss that must be visible.
    """
    by_evidence_id = {chunk.evidence_id: chunk for chunk in evidence}
    buckets: Dict[str, List[Item]] = {}
    dropped_unknown_evidence: List[str] = []
    dropped_no_span: List[str] = []

    def add(key: str, *, title: str, evidence_id: str, confidence: str, spans, due=None) -> None:
        chunk = by_evidence_id.get(evidence_id)
        if chunk is None:
            # The model cited an evidence_id we never issued. v1 would have carried
            # this through on a model-supplied source_ref; here it cannot.
            dropped_unknown_evidence.append(evidence_id)
            return
        if not spans:
            # The extraction schema requires minItems:1, so this only happens on an
            # unconstrained path (replay of an old capture, a gateway that ignored
            # the schema). P2 is golden rule #1 — drop rather than emit unsupported.
            dropped_no_span.append(evidence_id)
            return
        buckets.setdefault(key, []).append(
            Item(
                title=title,
                due=due,
                evidence_id=evidence_id,
                confidence=_confidence(confidence),
                # Pipeline-authoritative: the chunk we issued, not what the model echoed.
                source_ref=dict(chunk.source_ref),
                evidence_spans=[span.model_dump() for span in spans],
            )
        )

    for item in v3.my_actions:
        add(
            MY_ACTIONS,
            title=item.title,
            evidence_id=item.evidence_id,
            confidence=item.confidence,
            spans=item.evidence_spans,
            due=item.due_date,
        )

    # Someone else owns the work; for this recipient it is information. `owners` has
    # no home on the v1 Item yet, so it is not carried (see module docstring).
    for item in v3.others_actions:
        add(
            FYI,
            title=item.title,
            evidence_id=item.evidence_id,
            confidence=item.confidence,
            spans=item.evidence_spans,
            due=item.due_date,
        )

    for meeting in v3.deadlines_meetings:
        add(
            MEETINGS,
            title=meeting.title,
            evidence_id=meeting.evidence_id,
            # v3 has no confidence on a meeting: it is a stated fact with a time,
            # not an inference. Treat it as high.
            confidence="high",
            spans=meeting.evidence_spans,
            due=meeting.date_time,
        )

    for risk in v3.risks_blockers:
        # This is the routing v3's typed `severity` field exists to enable.
        add(
            URGENT if (risk.severity or "").strip().lower() == "high" else FYI,
            title=risk.title,
            evidence_id=risk.evidence_id,
            confidence="high" if (risk.severity or "").strip().lower() == "high" else "medium",
            spans=risk.evidence_spans,
        )

    for note in v3.fyi:
        add(
            FYI,
            title=note.title,
            evidence_id=note.evidence_id,
            confidence="medium",
            spans=note.evidence_spans,
        )

    # Canonical order comes from labels.SECTION_ORDER_BY_KEY; emit in that order and
    # skip empties, matching what the v1 path produces.
    from digest_core.assemble.labels import SECTION_ORDER_BY_KEY

    sections = [
        Section(title=section_title(key, language), items=items)
        for key, items in sorted(
            buckets.items(), key=lambda kv: SECTION_ORDER_BY_KEY.get(kv[0], 99)
        )
        if items
    ]

    stats: Dict[str, Any] = {
        "items": sum(len(items) for items in buckets.values()),
        "sections": len(sections),
        "dropped_unknown_evidence_id": len(dropped_unknown_evidence),
        "dropped_missing_evidence_span": len(dropped_no_span),
    }
    if dropped_unknown_evidence or dropped_no_span:
        logger.warning(
            "v3 adapter dropped items",
            trace_id=trace_id,
            unknown_evidence_ids=sorted(set(dropped_unknown_evidence)),
            missing_span_evidence_ids=sorted(set(dropped_no_span)),
        )

    digest = Digest(
        prompt_version=prompt_version,
        digest_date=digest_date,
        trace_id=trace_id,
        sections=sections,
        total_emails_processed=total_emails_processed,
        emails_with_actions=len({item.evidence_id for items in buckets.values() for item in items}),
    )
    return digest, stats
