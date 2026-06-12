from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


# Citation model for extractive traceability
class Citation(BaseModel):
    """
    Citation with validated offsets into normalized email body.

    Offsets are calculated AFTER:
    1. HTML→text normalization
    2. Email cleaner (quote/signature removal)

    This ensures stable offsets for evidence extraction.
    """

    msg_id: str = Field(description="Message ID reference")
    start: int = Field(ge=0, description="Start offset in normalized text")
    end: int = Field(gt=0, description="End offset in normalized text")
    preview: str = Field(max_length=200, description="Text preview text[start:end] for validation")
    checksum: Optional[str] = Field(
        None, description="SHA-256 of normalized email body for integrity check"
    )


class EvidenceSpan(BaseModel):
    """Verbatim pointer-to-support for a digest item (R2).

    The extractor returns the exact quote *text* in the source language; numeric
    offsets are derived server-side via CitationBuilder.find() into the normalized
    body, so the model never counts characters (one coordinate system).
    """

    msg_id: str = Field(description="Message ID the quote is taken from")
    quote: str = Field(description="Verbatim substring of the cited chunk body")


# Legacy v1 models
class Item(BaseModel):
    title: str
    due: Optional[str] = None
    evidence_id: str
    confidence: float
    source_ref: Dict[str, Any]
    email_subject: Optional[str] = Field(default=None)
    # Reader enrichment (U4): populated at assemble time from the normalized
    # message behind source_ref.msg_id — the artifact stays self-contained
    # (no ingest snapshot needed to show who asked). exclude_none keeps older
    # artifacts byte-compatible.
    email_from: Optional[str] = Field(
        default=None, description='Sender display, e.g. "Ivan Petrov <ivan.petrov@corp.ru>"'
    )
    evidence_spans: List[EvidenceSpan] = Field(
        default_factory=list, description="Verbatim source-language spans supporting the item"
    )
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )
    # P2 gate annotations (PR8). Optional so exclude_none keeps un-gated artifacts
    # byte-compatible; the shadow gate populates them on every evidence-backed item.
    citation_fidelity_ok: Optional[bool] = Field(
        default=None, description="Spans resolve to offsets in the immutable normalized body"
    )
    support_score: Optional[float] = Field(
        default=None, description="Reranker(span, claim) support score, when scored"
    )
    weak_evidence: Optional[bool] = Field(
        default=None, description="Item lacks offset-verifiable or above-threshold support"
    )
    rank_score: Optional[float] = Field(
        default=None,
        description="Rule-based actionability score from DigestRanker (0..1)",
    )
    # EP-7 dedup ledger annotation. Optional so exclude_none keeps artifacts
    # byte-compatible while memory.dedup_ledger is off (the default).
    seen_before: Optional[bool] = Field(
        default=None,
        description="Evidence behind this item already backed a delivered item (dedup ledger)",
    )


class Section(BaseModel):
    title: str
    items: List[Item]


class Digest(BaseModel):
    schema_version: str = "1.0"
    prompt_version: str
    digest_date: str
    trace_id: str
    sections: List[Section]
    total_emails_processed: int = Field(default=0)
    emails_with_actions: int = Field(default=0)


# Enhanced v2 models
class ActionItem(BaseModel):
    """Action item with evidence and quote."""

    title: str = Field(description="Brief action title")
    description: str = Field(description="Detailed description")
    evidence_id: str = Field(description="Evidence ID reference")
    quote: str = Field(description="1-2 sentence quote from evidence")
    due_date: Optional[str] = Field(None, description="ISO-8601 date or 'today'/'tomorrow'")
    due_date_normalized: Optional[str] = Field(
        None, description="ISO-8601 with TZ America/Sao_Paulo"
    )
    due_date_label: Optional[str] = Field(None, description="'today'/'tomorrow' if applicable")
    actors: List[str] = Field(default_factory=list, description="People involved")
    confidence: str = Field(description="High/Medium/Low")
    response_channel: Optional[str] = Field(None, description="email/slack/meeting")
    email_subject: Optional[str] = Field(default=None)
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )
    rank_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Ranking score (0.0-1.0) for actionability"
    )


class DeadlineMeeting(BaseModel):
    """Deadline or meeting with evidence."""

    title: str
    evidence_id: str
    quote: str
    date_time: str = Field(description="ISO-8601 with TZ")
    date_label: Optional[str] = Field(None, description="'today'/'tomorrow' if applicable")
    location: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    email_subject: Optional[str] = Field(default=None)
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )
    rank_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Ranking score (0.0-1.0) for actionability"
    )


class RiskBlocker(BaseModel):
    """Risk or blocker with evidence."""

    title: str
    evidence_id: str
    quote: str
    severity: str = Field(description="High/Medium/Low")
    impact: str
    email_subject: Optional[str] = Field(default=None)
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )
    rank_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Ranking score (0.0-1.0) for actionability"
    )


class FYIItem(BaseModel):
    """FYI item with evidence."""

    title: str
    evidence_id: str
    quote: str
    category: Optional[str] = None
    email_subject: Optional[str] = Field(default=None)
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )
    rank_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Ranking score (0.0-1.0) for actionability"
    )


class ExtractedActionItem(BaseModel):
    """Rule-based extracted action or mention (not from LLM)."""

    type: str = Field(description="action, question, or mention")
    who: str = Field(description="Who should act (usually 'user')")
    verb: str = Field(description="Action verb or question type")
    text: str = Field(max_length=500, description="Full text of action/mention")
    due: Optional[str] = Field(None, description="Deadline if found")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    evidence_id: str = Field(description="Evidence ID reference")
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )
    email_subject: Optional[str] = Field(default=None)
    rank_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Ranking score (0.0-1.0) for actionability"
    )


class EnhancedDigest(BaseModel):
    """Enhanced digest with structured sections and evidence references."""

    schema_version: str = "2.0"
    prompt_version: str
    digest_date: str
    trace_id: str
    timezone: str = "America/Sao_Paulo"

    # Structured sections
    my_actions: List[ActionItem] = Field(default_factory=list)
    others_actions: List[ActionItem] = Field(default_factory=list)
    deadlines_meetings: List[DeadlineMeeting] = Field(default_factory=list)
    risks_blockers: List[RiskBlocker] = Field(default_factory=list)
    fyi: List[FYIItem] = Field(default_factory=list)

    # Rule-based extracted actions/mentions (separate from LLM-generated)
    extracted_actions: List[ExtractedActionItem] = Field(
        default_factory=list, description="Rule-based extracted actions and mentions"
    )

    # Markdown summary (generated after JSON)
    markdown_summary: Optional[str] = None

    # Statistics
    total_emails_processed: int = Field(default=0)
    emails_with_actions: int = Field(default=0)


# V3 models - Simplified neutral schema
class ActionItemV3(BaseModel):
    """Action item with neutral fields only."""

    title: str = Field(description="Brief action title")
    description: str = Field(description="Detailed description")
    evidence_id: str = Field(description="Evidence ID reference")
    quote: str = Field(description="1-2 sentence quote from evidence")
    due_date: Optional[str] = Field(None, description="ISO-8601 date or 'today'/'tomorrow'")
    due_date_normalized: Optional[str] = Field(None, description="ISO-8601 with TZ")
    due_date_label: Optional[str] = Field(None, description="'today'/'tomorrow' if applicable")
    owners: List[str] = Field(default_factory=list, description="Owners/responsible parties")
    confidence: str = Field(description="High/Medium/Low")
    response_channel: Optional[str] = Field(None, description="email/slack/meeting")


class DeadlineMeetingV3(BaseModel):
    """Deadline or meeting with neutral fields."""

    title: str
    evidence_id: str
    quote: str
    date_time: str = Field(description="ISO-8601 with TZ")
    date_label: Optional[str] = Field(None, description="'today'/'tomorrow' if applicable")
    location: Optional[str] = None
    participants: List[str] = Field(default_factory=list, description="Meeting participants")


class RiskBlockerV3(BaseModel):
    """Risk or blocker with neutral fields."""

    title: str
    evidence_id: str
    quote: str
    severity: str = Field(description="High/Medium/Low")
    impact: str
    owners: List[str] = Field(default_factory=list, description="Owners/responsible parties")


class FYIItemV3(BaseModel):
    """FYI item with neutral fields."""

    title: str
    evidence_id: str
    quote: str
    category: Optional[str] = None


class EnhancedDigestV3(BaseModel):
    """Enhanced digest V3 with neutral fields only - no masking."""

    schema_version: str = "3.0"
    prompt_version: str = "mvp.5"
    digest_date: str
    trace_id: str
    timezone: str = "America/Sao_Paulo"

    # Structured sections with neutral fields
    my_actions: List[ActionItemV3] = Field(default_factory=list)
    others_actions: List[ActionItemV3] = Field(default_factory=list)
    deadlines_meetings: List[DeadlineMeetingV3] = Field(default_factory=list)
    risks_blockers: List[RiskBlockerV3] = Field(default_factory=list)
    fyi: List[FYIItemV3] = Field(default_factory=list)

    # Markdown summary (generated after JSON)
    markdown_summary: Optional[str] = None

    # Statistics
    total_emails_processed: int = Field(default=0)
    emails_with_actions: int = Field(default=0)


# Hierarchical mode models
class ThreadAction(BaseModel):
    """Action item from thread summary."""

    title: str = Field(max_length=100, description="Brief action title")
    evidence_id: str = Field(description="Evidence ID reference")
    quote: str = Field(
        min_length=10,
        max_length=300,
        description="Short quote from evidence (up to 300 chars)",
    )
    who_must_act: str = Field(description="user/sender/team")
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )


class ThreadDeadline(BaseModel):
    """Deadline from thread summary."""

    title: str = Field(description="Deadline title")
    date_time: str = Field(description="ISO-8601 datetime")
    evidence_id: str = Field(description="Evidence ID reference")
    quote: str = Field(min_length=10, max_length=150, description="Short quote from evidence")
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )


class ThreadSummary(BaseModel):
    """Per-thread mini-summary output."""

    thread_id: str = Field(description="Thread/conversation ID")
    summary: str = Field(max_length=600, description="Brief summary ≤200 tokens (up to 600 chars)")
    pending_actions: List[ThreadAction] = Field(
        default_factory=list, description="Actions from this thread"
    )
    deadlines: List[ThreadDeadline] = Field(
        default_factory=list, description="Deadlines from this thread"
    )
    who_must_act: List[str] = Field(default_factory=list, description="user/others")
    open_questions: List[str] = Field(default_factory=list, description="Unresolved questions")
    evidence_ids: List[str] = Field(
        default_factory=list, description="All evidence IDs in this thread"
    )
