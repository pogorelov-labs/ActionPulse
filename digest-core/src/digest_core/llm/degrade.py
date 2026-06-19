"""
LLM degradation strategies - extractive fallback when LLM fails.
"""

from typing import List
import structlog
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.schemas import EnhancedDigest, ActionItem, DeadlineMeeting

logger = structlog.get_logger()


def extractive_fallback(
    evidence_chunks: List[EvidenceChunk],
    digest_date: str,
    trace_id: str,
    reason: str = "llm_unavailable",
) -> EnhancedDigest:
    """
    Create extractive digest when LLM fails.

    Uses rule-based extraction from evidence chunks:
    - Action verbs → my_actions
    - Dates/deadlines → deadlines_meetings
    - High priority → risks_blockers
    - Remainder → fyi

    Args:
        evidence_chunks: List of evidence chunks
        digest_date: Date of digest
        trace_id: Trace ID
        reason: Reason for degradation

    Returns:
        EnhancedDigest with extracted items
    """
    logger.warning(
        "Using extractive fallback for digest",
        reason=reason,
        chunks=len(evidence_chunks),
        trace_id=trace_id,
    )

    my_actions = []
    others_actions = []
    deadlines_meetings = []
    risks_blockers = []
    fyi = []

    # Sort chunks by priority
    sorted_chunks = sorted(evidence_chunks, key=lambda c: c.priority_score, reverse=True)

    for chunk in sorted_chunks:
        # Extract metadata
        signals = chunk.signals or {}
        addressed_to_me = chunk.addressed_to_me
        action_verbs = signals.get("action_verbs", [])
        dates = signals.get("dates", [])

        # Create quote (truncate to 300 chars)
        quote = chunk.content[:300]
        if len(chunk.content) > 300:
            quote += "..."

        # Extract actions
        if len(action_verbs) > 0:
            action = ActionItem(
                title=f"Action: {action_verbs[0] if action_verbs else 'Review'}",
                description=quote,
                evidence_id=chunk.evidence_id,
                quote=quote,
                confidence="Medium",
                actors=[],
            )

            if addressed_to_me:
                my_actions.append(action)
            else:
                others_actions.append(action)

        # Extract deadlines
        elif len(dates) > 0:
            deadline = DeadlineMeeting(
                title=f"Deadline: {dates[0]}",
                evidence_id=chunk.evidence_id,
                quote=quote,
                date_time=dates[0],
            )
            deadlines_meetings.append(deadline)

        # High priority → risks
        elif chunk.priority_score >= 2.0:
            from digest_core.llm.schemas import RiskBlocker

            risk = RiskBlocker(
                title="High priority item",
                evidence_id=chunk.evidence_id,
                quote=quote,
                severity="Medium",
                impact="Unknown",
            )
            risks_blockers.append(risk)

        # Remainder → FYI
        else:
            from digest_core.llm.schemas import FYIItem

            fyi_item = FYIItem(title="FYI", evidence_id=chunk.evidence_id, quote=quote)
            fyi.append(fyi_item)

    # Limit items
    my_actions = my_actions[:5]
    others_actions = others_actions[:5]
    deadlines_meetings = deadlines_meetings[:5]
    risks_blockers = risks_blockers[:3]
    fyi = fyi[:10]

    logger.info(
        "Extractive fallback digest created",
        my_actions=len(my_actions),
        others_actions=len(others_actions),
        deadlines=len(deadlines_meetings),
        risks=len(risks_blockers),
        fyi=len(fyi),
        trace_id=trace_id,
    )

    return EnhancedDigest(
        schema_version="2.0",
        prompt_version="extractive_fallback",
        digest_date=digest_date,
        trace_id=trace_id,
        my_actions=my_actions,
        others_actions=others_actions,
        deadlines_meetings=deadlines_meetings,
        risks_blockers=risks_blockers,
        fyi=fyi,
        total_emails_processed=len(evidence_chunks),
        emails_with_actions=len(my_actions) + len(others_actions),
    )
