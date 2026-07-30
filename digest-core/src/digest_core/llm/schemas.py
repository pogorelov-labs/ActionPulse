from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing import Any, ClassVar, Dict, List, Optional


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


# v1 — the LIVE digest schema (the one run.py produces today).
class Item(BaseModel):
    # populate_by_name lets code construct/serialize by the new field names while
    # validation_alias (below) still accepts the OLD email_subject/email_from keys
    # from artifacts written before the 2026-06-18 source_* rename (§9 #5).
    model_config = ConfigDict(populate_by_name=True)

    title: str
    due: Optional[str] = None
    evidence_id: str
    confidence: float
    source_ref: Dict[str, Any]
    # Source-agnostic topic slot (email subject, Mattermost channel name, …).
    # Renamed from email_subject (2026-06-18); the AliasChoices keeps OLD artifacts
    # deserializing while model_dump emits the NEW key.
    source_subject: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("source_subject", "email_subject")
    )
    # Reader enrichment (U4): populated at assemble time from the normalized
    # message behind source_ref.msg_id — the artifact stays self-contained
    # (no ingest snapshot needed to show who asked). exclude_none keeps older
    # artifacts byte-compatible. Renamed from email_from (2026-06-18) with a
    # back-compat alias for the old key.
    source_from: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("source_from", "email_from"),
        description='Sender display, e.g. "Ivan Petrov <ivan.petrov@corp.ru>"',
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


# ---------------------------------------------------------------------------
# v3 — the constrained-decoding target (D-A1), not yet on the live path.
#
# v3 predates the evidence-span/citation/P2-gate machinery (PR6–PR11): it shipped
# with only a bare `quote` string. Reviving it as the extraction target without
# that machinery would regress P2 (Traceability — golden rule #1), so every typed
# item inherits `_TraceBackbone` below.
# ---------------------------------------------------------------------------


class _TraceBackbone(BaseModel):
    """Evidence/citation/gate fields grafted onto every v3 item type (A1.1).

    All fields are optional/defaulted and split by who fills them: the extractor
    emits ``evidence_spans``; ``citations`` and the gate annotations are populated
    downstream (CitationBuilder + the shadow gate). That keeps a v3 payload valid
    straight off the model while still carrying the full P2 chain by the time it
    reaches assemble.
    """

    #: Backbone fields the **extractor must never emit** — each is owned by the
    #: machinery that computes it (``citations`` by CitationBuilder; the gate
    #: annotations by the shadow gate / reranker / ranker / dedup ledger).
    #: ``evidence_spans`` is the one exception: producing it *is* the model's job.
    #:
    #: A1.2a projects these out of the constrained-decoding schema, so the model is
    #: not asked for values it has no basis to produce. ``test_v3_traceable_schema``
    #: pins this against the actual field set, so growing the backbone without
    #: classifying the new field fails loudly rather than silently leaking it into
    #: the extraction contract.
    DOWNSTREAM_ONLY: ClassVar[frozenset] = frozenset(
        {
            "citations",
            "citation_fidelity_ok",
            "support_score",
            "weak_evidence",
            "rank_score",
            "seen_before",
        }
    )

    evidence_spans: List[EvidenceSpan] = Field(
        default_factory=list, description="Verbatim source-language spans supporting the item (R2)"
    )
    citations: List[Citation] = Field(
        default_factory=list, description="Evidence citations with validated offsets"
    )
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
        default=None, description="Rule-based actionability score from DigestRanker (0..1)"
    )
    seen_before: Optional[bool] = Field(
        default=None, description="Evidence already backed a delivered item (dedup ledger)"
    )


class ActionItemV3(_TraceBackbone):
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


class DeadlineMeetingV3(_TraceBackbone):
    """Deadline or meeting with neutral fields."""

    title: str
    evidence_id: str
    quote: str
    date_time: str = Field(description="ISO-8601 with TZ")
    date_label: Optional[str] = Field(None, description="'today'/'tomorrow' if applicable")
    location: Optional[str] = None
    participants: List[str] = Field(default_factory=list, description="Meeting participants")


class RiskBlockerV3(_TraceBackbone):
    """Risk or blocker with neutral fields."""

    title: str
    evidence_id: str
    quote: str
    severity: str = Field(description="High/Medium/Low")
    impact: str
    owners: List[str] = Field(default_factory=list, description="Owners/responsible parties")


class FYIItemV3(_TraceBackbone):
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
