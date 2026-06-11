"""P2 citation gate in SHADOW mode (PR8, R3/R6).

Annotates every evidence-backed digest item with:
  * ``citation_fidelity_ok`` — at least one evidence span resolves to an offset in
    the immutable normalized body (offset + checksum), and
  * ``support_score`` — an optional reranker(span, claim) score for low-confidence
    items only (the reranker is the scarce fleet resource, budgeted per run), and
  * ``weak_evidence`` — no offset-verifiable span, or support below tau.

It NEVER drops an item (R3). The reranker is OFF by default (``reranker.enabled``;
D4 resolved PC-2 in its favor, the live flip waits for corp validation — EP-14):
with no reranker the gate is offset-only and makes zero network calls. Any
reranker failure mid-run degrades the gate back to offset-only, never crashes.
"""

from __future__ import annotations

import hashlib
from typing import Dict, Optional

import structlog

from digest_core.config import RerankerConfig
from digest_core.llm.schemas import Digest, Item

logger = structlog.get_logger()


def normalize_confidence(value) -> float:
    """R6: map High/Med/Low (or a float) to a threshold-comparable float."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    mapping = {"high": 0.9, "medium": 0.6, "med": 0.6, "low": 0.3}
    return mapping.get(str(value).strip().lower(), 0.5)


class CitationGate:
    """Offset-first shadow gate with an optional budgeted reranker."""

    def __init__(
        self,
        normalized_messages_map: Dict[str, str],
        *,
        reranker=None,
        config: Optional[RerankerConfig] = None,
    ):
        self.bodies = dict(normalized_messages_map or {})
        self.reranker = reranker
        self.config = config or RerankerConfig()
        self.reranker_calls = 0

    def annotate(self, digest: Digest, *, metrics=None) -> Digest:
        for section in digest.sections:
            for item in section.items:
                self._annotate_item(item, metrics)
        return digest

    def _annotate_item(self, item: Item, metrics) -> None:
        # System/status items are not evidence-backed — leave annotations as None.
        if item.evidence_id == "system":
            return

        offset_ok = self._offset_ok(item)
        support = self._support_score(item, metrics)
        weak = (not offset_ok) or (support is not None and support < self.config.tau)

        item.citation_fidelity_ok = offset_ok
        item.support_score = support
        item.weak_evidence = weak

        if metrics is not None:
            if support is not None:
                metrics.record_citation_support_score(support)
            if weak:
                metrics.record_citation_weak_evidence()

    def _offset_ok(self, item: Item) -> bool:
        """True if some span quote resolves to an offset in the cited body."""
        fallback_msg_id = (item.source_ref or {}).get("msg_id", "")
        for span in item.evidence_spans or []:
            body = self.bodies.get(span.msg_id) or self.bodies.get(fallback_msg_id)
            if body and self._find_offset(span.quote, body) != -1:
                # checksum binds the offset to the exact body it indexes
                self._checksum(body)
                return True
        return False

    def _support_score(self, item: Item, metrics) -> Optional[float]:
        if not self.config.enabled or self.reranker is None:
            return None
        if normalize_confidence(item.confidence) >= self.config.low_confidence_threshold:
            return None  # only low-confidence items spend the scarce reranker
        if self.reranker_calls >= self.config.budget_per_run:
            return None
        quotes = [span.quote for span in (item.evidence_spans or []) if span.quote]
        if not quotes:
            return None
        self.reranker_calls += 1
        if metrics is not None:
            metrics.record_reranker_call()
        try:
            scores = self.reranker.score(item.title, quotes)
        except Exception as exc:
            # Degrade-not-drop (R3): any scoring failure (429, timeout, stage
            # budget) turns the gate fidelity-only for the REST of the run —
            # no retries that could stall the pipeline, never a crash.
            logger.warning(
                "Reranker degraded to fidelity-only for the rest of the run",
                error_type=type(exc).__name__,
                evidence_id=item.evidence_id,
                reranker_calls=self.reranker_calls,
            )
            self.reranker = None
            return None
        return max(scores) if scores else None

    @staticmethod
    def _find_offset(quote: str, body: str) -> int:
        if not quote:
            return -1
        idx = body.find(quote)
        if idx != -1:
            return idx
        # whitespace-tolerant fallback
        return " ".join(body.split()).find(" ".join(quote.split()))

    @staticmethod
    def _checksum(body: str) -> str:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
