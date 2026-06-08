"""
Context selection for relevant evidence chunks using balanced bucket strategy.
"""

import re
from typing import List, Dict
from datetime import datetime, timezone
from collections import defaultdict
import structlog

from digest_core.evidence.split import EvidenceChunk
from digest_core.config import (
    SelectionBucketsConfig,
    SelectionWeightsConfig,
    ContextBudgetConfig,
    ShrinkConfig,
)

try:
    import dateutil.parser
except ImportError:
    dateutil = None

logger = structlog.get_logger()


class SelectionMetrics:
    """Metrics for evidence selection process."""

    def __init__(self):
        self.covered_threads = set()
        self.selected_by_bucket = defaultdict(
            int,
            {
                "threads_top": 0,
                "addressed_to_me": 0,
                "dates_deadlines": 0,
                "critical_senders": 0,
                "remainder": 0,
            },
        )
        self.discarded_action_like = 0
        self.token_budget_used = 0
        self.total_chunks_considered = 0
        # NEW: Auto-shrink metrics
        self.budget_requested = 0
        self.budget_applied = 0
        self.shrinks_count = 0
        self.shrink_percentage = 0.0

    def to_dict(self) -> Dict:
        """Convert metrics to dictionary."""
        return {
            "covered_threads": len(self.covered_threads),
            "selected_by_bucket": dict(self.selected_by_bucket),
            "discarded_action_like": self.discarded_action_like,
            "token_budget_used": self.token_budget_used,
            "total_chunks_considered": self.total_chunks_considered,
            "budget_requested": self.budget_requested,
            "budget_applied": self.budget_applied,
            "shrinks_count": self.shrinks_count,
            "shrink_percentage": self.shrink_percentage,
        }


class ContextSelector:
    """Select relevant evidence chunks using balanced bucket strategy."""

    def __init__(
        self,
        buckets_config: SelectionBucketsConfig = None,
        weights_config: SelectionWeightsConfig = None,
        context_budget_config: ContextBudgetConfig = None,
        shrink_config: ShrinkConfig = None,
        relevance_scorer=None,
    ):
        self.buckets_config = buckets_config or SelectionBucketsConfig()
        self.weights_config = weights_config or SelectionWeightsConfig()
        self.context_budget_config = context_budget_config or ContextBudgetConfig()
        self.shrink_config = shrink_config or ShrinkConfig()
        # Optional fleet-backed relevance scorer (PR9). None offline/tests -> the
        # fused score falls back to metadata-only. Used only when enable_relevance.
        self.relevance_scorer = relevance_scorer

        # Negative patterns (noreply, unsubscribe, etc.)
        self.negative_patterns = [
            r"\b(noreply@|no-reply@|donotreply@)\b",
            r"\b(unsubscribe|отписаться)\b",
            r"\b(auto-submitted|автоответ)\b",
            r"\b(postmaster@)\b",
            r"\b(delivery status|статус доставки)\b",
            r"\b(out of office|auto-reply|automatic reply)\b",
        ]
        self.negative_regex = re.compile("|".join(self.negative_patterns), re.IGNORECASE)

        # Document attachment types
        self.doc_attachment_types = {"pdf", "doc", "docx", "xlsx", "xls", "ppt", "pptx"}

        # Metrics
        self.metrics = SelectionMetrics()

    def select_context(
        self,
        evidence_chunks: List[EvidenceChunk],
        legacy_evidence_chunks: List[EvidenceChunk] | None = None,
        max_tokens: int | None = None,
    ) -> List[EvidenceChunk]:
        """
        Select relevant evidence chunks using balanced bucket strategy.

        Buckets:
        - threads_top: ≥10 threads by recency/volume (1 chunk each)
        - addressed_to_me: ≥8 chunks with AddressedToMe=true
        - dates_deadlines: ≥6 chunks with dates/deadlines
        - critical_senders: ≥4 chunks from sender_rank>=2
        - remainder: general scoring
        """
        if legacy_evidence_chunks is not None:
            evidence_chunks = legacy_evidence_chunks

        token_budget = max_tokens or self.context_budget_config.max_total_tokens
        logger.info(
            "Starting balanced context selection",
            total_chunks=len(evidence_chunks),
            token_budget=token_budget,
        )

        self.metrics = SelectionMetrics()
        self.metrics.total_chunks_considered = len(evidence_chunks)

        # Step 1: Enhanced scoring for all chunks
        scored_chunks = self._calculate_enhanced_scores(evidence_chunks)

        # Step 2: Balanced bucket selection
        selected_chunks = self._select_with_buckets(scored_chunks, token_budget)

        # Step 3: Auto-shrink if enabled and over budget
        if self.shrink_config.enable_auto_shrink:
            selected_chunks = self._ensure_token_budget(selected_chunks, token_budget)

            # Calculate shrink percentage
            if self.metrics.budget_requested > 0:
                self.metrics.shrink_percentage = (
                    (self.metrics.budget_requested - self.metrics.budget_applied)
                    / self.metrics.budget_requested
                    * 100.0
                )

        # Update final token budget used
        self.metrics.token_budget_used = sum(c.token_count for c in selected_chunks)

        # Log metrics
        logger.info(
            "Context selection completed",
            **self.metrics.to_dict(),
            selected_chunks=len(selected_chunks),
        )

        return selected_chunks

    def _calculate_enhanced_scores(self, chunks: List[EvidenceChunk]) -> List[EvidenceChunk]:
        """Score all chunks.

        Default (``enable_relevance=False``): the legacy enhanced score, byte-identical
        to pre-PR9. When enabled: a fused ``w_meta*metadata + w_rerank*relevance`` score,
        falling back to metadata-only when no relevance scorer/query is available.
        """
        relevance: Dict[str, float] = {}
        if self.weights_config.enable_relevance and self.relevance_scorer is not None:
            try:
                relevance = self.relevance_scorer.score_chunks(chunks) or {}
            except Exception as exc:  # offline / fleet error -> metadata-only
                logger.warning("Relevance scoring failed; metadata-only fallback", error=str(exc))
                relevance = {}

        scored_chunks = []
        for chunk in chunks:
            if self.weights_config.enable_relevance:
                score = self.weights_config.w_meta * self._metadata_score(chunk) + (
                    self.weights_config.w_rerank * float(relevance.get(chunk.evidence_id, 0.0))
                )
            else:
                score = self._legacy_enhanced_score(chunk)

            if callable(getattr(chunk, "_replace", None)) and not hasattr(chunk, "_mock_methods"):
                updated_chunk = chunk._replace(priority_score=score)
            else:
                chunk.priority_score = score
                updated_chunk = chunk
            scored_chunks.append(updated_chunk)

        return scored_chunks

    def _legacy_enhanced_score(self, chunk: EvidenceChunk) -> float:
        """Pre-PR9 enhanced score (all 11 terms). Active when enable_relevance is off."""
        score = 0.0
        signals = getattr(chunk, "signals", {}) or {}
        metadata = getattr(chunk, "message_metadata", {}) or {}

        score += self._calculate_recency_score(chunk) * self.weights_config.recency
        if getattr(chunk, "addressed_to_me", False):
            score += self.weights_config.addressed_to_me
        score += len(signals.get("action_verbs", [])) * self.weights_config.action_verbs
        if signals.get("contains_question", False):
            score += self.weights_config.question_mark
        score += len(signals.get("dates", [])) * self.weights_config.dates_found
        if metadata.get("importance", "Normal") == "High":
            score += self.weights_config.importance_high
        if metadata.get("is_flagged", False):
            score += self.weights_config.is_flagged
        if self._has_doc_attachments(chunk):
            score += self.weights_config.has_doc_attachments
        score += signals.get("sender_rank", 1) * self.weights_config.sender_rank
        base_priority = getattr(chunk, "priority_score", 0.0)
        if not isinstance(base_priority, (int, float)):
            base_priority = 0.0
        score += base_priority * 0.1
        if self._has_negative_prior(chunk):
            score += self.weights_config.negative_prior
        return score

    def _metadata_score(self, chunk: EvidenceChunk) -> float:
        """Non-textual metadata component of the fused score (PR9).

        recency + addressed_to_me + importance + flagged + sender_rank + negative_prior.
        The textual terms (action verbs, dates, questions, attachments) are intentionally
        dropped here — the relevance component subsumes them when enabled.
        """
        score = 0.0
        signals = getattr(chunk, "signals", {}) or {}
        metadata = getattr(chunk, "message_metadata", {}) or {}

        score += self._calculate_recency_score(chunk) * self.weights_config.recency
        if getattr(chunk, "addressed_to_me", False):
            score += self.weights_config.addressed_to_me
        if metadata.get("importance", "Normal") == "High":
            score += self.weights_config.importance_high
        if metadata.get("is_flagged", False):
            score += self.weights_config.is_flagged
        score += signals.get("sender_rank", 1) * self.weights_config.sender_rank
        if self._has_negative_prior(chunk):
            score += self.weights_config.negative_prior
        return score

    def _calculate_recency_score(self, chunk: EvidenceChunk) -> float:
        """
        Calculate recency score with exponential decay.

        Score decreases as message gets older:
        - < 1 hour: 1.0
        - 1-6 hours: 0.8
        - 6-24 hours: 0.5
        - > 24 hours: 0.2
        """
        metadata = getattr(chunk, "message_metadata", {}) or {}
        received_at = metadata.get("received_at", "")
        if not received_at:
            return 0.2

        try:
            # Parse ISO datetime
            if dateutil:
                msg_time = dateutil.parser.isoparse(received_at)
            else:
                # Fallback to standard datetime parsing
                msg_time = datetime.fromisoformat(received_at.replace("Z", "+00:00"))

            # Calculate hours ago
            now = datetime.now(timezone.utc)
            hours_ago = (now - msg_time.astimezone(timezone.utc)).total_seconds() / 3600

            if hours_ago < 1:
                return 1.0
            elif hours_ago < 6:
                return 0.8
            elif hours_ago < 24:
                return 0.5
            else:
                return 0.2
        except Exception:
            return 0.2

    def _has_doc_attachments(self, chunk: EvidenceChunk) -> bool:
        """Check if chunk has document attachments (pdf, doc, xlsx, etc.)."""
        metadata = getattr(chunk, "message_metadata", {}) or {}
        attachment_types = metadata.get("attachment_types", [])
        return any(ext.lower() in self.doc_attachment_types for ext in attachment_types)

    def _has_negative_prior(self, chunk: EvidenceChunk) -> bool:
        """Check for negative priors (noreply, unsubscribe, etc.)."""
        # Check sender email
        metadata = getattr(chunk, "message_metadata", {}) or {}
        sender = metadata.get("from", "")
        if self.negative_regex.search(sender):
            return True

        # Check content
        if self.negative_regex.search(chunk.content):
            return True

        return False

    def _get_dedup_key(self, chunk: EvidenceChunk) -> tuple:
        """Get deduplication key (msg_id, start, end) for chunk."""
        source_ref = getattr(chunk, "source_ref", {}) or {}
        msg_id = source_ref.get("msg_id", "")
        if "start" in source_ref and "end" in source_ref:
            return (msg_id, source_ref.get("start", 0), source_ref.get("end", 0))
        return (chunk.evidence_id,)

    def _select_with_buckets(
        self, scored_chunks: List[EvidenceChunk], token_budget: int
    ) -> List[EvidenceChunk]:
        """
        Select chunks using balanced bucket strategy with token budget protection.

        Ensures minimum quotas:
        - At least 1 from dates_deadlines (if available)
        - At least 1 from addressed_to_me (if available)

        Returns list of selected chunks.
        """
        selected = []
        seen_chunks = set()  # Track by (msg_id, start, end) for deduplication
        thread_chunk_counts = defaultdict(int)
        remaining_budget = token_budget

        # Sort all chunks by score (highest first)
        all_sorted = sorted(scored_chunks, key=lambda c: c.priority_score, reverse=True)

        # Bucket 1: threads_top - cover different threads (1 chunk each by default)
        threads_covered = set()
        bucket_name = "threads_top"
        bucket_kept = 0
        bucket_dropped = 0

        for chunk in all_sorted:
            if len(threads_covered) >= self.buckets_config.threads_top:
                bucket_dropped += 1
                continue

            conv_id = getattr(chunk, "conversation_id", getattr(chunk, "thread_id", ""))
            if conv_id in threads_covered:
                bucket_dropped += 1
                continue

            # Deduplication by (msg_id, start, end)
            dedup_key = self._get_dedup_key(chunk)
            if dedup_key in seen_chunks:
                bucket_dropped += 1
                continue

            if remaining_budget >= chunk.token_count:
                selected.append(chunk)
                seen_chunks.add(dedup_key)
                threads_covered.add(conv_id)
                thread_chunk_counts[conv_id] += 1
                remaining_budget -= chunk.token_count
                self.metrics.covered_threads.add(conv_id)
                self.metrics.selected_by_bucket[bucket_name] += 1
                bucket_kept += 1
            else:
                bucket_dropped += 1

        logger.info(f"Bucket {bucket_name}: kept={bucket_kept}, dropped={bucket_dropped}")

        # Bucket 2: addressed_to_me - chunks addressed to user (min 1 if available)
        bucket_name = "addressed_to_me"
        bucket_kept = 0
        bucket_dropped = 0
        min_required = 1  # Ensure at least 1 if available

        addressed_chunks = [c for c in all_sorted if getattr(c, "addressed_to_me", False)]
        addressed_chunks = sorted(addressed_chunks, key=lambda c: c.priority_score, reverse=True)

        for chunk in addressed_chunks:
            # Skip if already selected
            dedup_key = self._get_dedup_key(chunk)
            if dedup_key in seen_chunks:
                if (
                    self.metrics.selected_by_bucket[bucket_name]
                    < self.buckets_config.addressed_to_me
                ):
                    self.metrics.selected_by_bucket[bucket_name] += 1
                    bucket_kept += 1
                bucket_dropped += 1
                continue

            # Check bucket limit (but ensure min 1)
            if self.metrics.selected_by_bucket[bucket_name] >= self.buckets_config.addressed_to_me:
                bucket_dropped += 1
                break

            conv_id = getattr(chunk, "conversation_id", getattr(chunk, "thread_id", ""))
            if thread_chunk_counts[conv_id] >= self.buckets_config.per_thread_max:
                bucket_dropped += 1
                continue

            # If this is first item and we need at least 1, relax budget constraint
            if bucket_kept < min_required or remaining_budget >= chunk.token_count:
                selected.append(chunk)
                seen_chunks.add(dedup_key)
                thread_chunk_counts[conv_id] += 1
                if remaining_budget >= chunk.token_count:
                    remaining_budget -= chunk.token_count
                self.metrics.covered_threads.add(conv_id)
                self.metrics.selected_by_bucket[bucket_name] += 1
                bucket_kept += 1
            else:
                bucket_dropped += 1

        logger.info(f"Bucket {bucket_name}: kept={bucket_kept}, dropped={bucket_dropped}")

        # Bucket 3: dates_deadlines - chunks with dates/deadlines (min 1 if available)
        bucket_name = "dates_deadlines"
        bucket_kept = 0
        bucket_dropped = 0
        min_required = 1  # Ensure at least 1 if available

        date_chunks = [
            c for c in all_sorted if len((getattr(c, "signals", {}) or {}).get("dates", [])) > 0
        ]
        date_chunks = sorted(date_chunks, key=lambda c: c.priority_score, reverse=True)

        for chunk in date_chunks:
            # Skip if already selected
            dedup_key = self._get_dedup_key(chunk)
            if dedup_key in seen_chunks:
                if (
                    self.metrics.selected_by_bucket[bucket_name]
                    < self.buckets_config.dates_deadlines
                ):
                    self.metrics.selected_by_bucket[bucket_name] += 1
                    bucket_kept += 1
                bucket_dropped += 1
                continue

            # Check bucket limit (but ensure min 1)
            if self.metrics.selected_by_bucket[bucket_name] >= self.buckets_config.dates_deadlines:
                bucket_dropped += 1
                break

            conv_id = getattr(chunk, "conversation_id", getattr(chunk, "thread_id", ""))
            if thread_chunk_counts[conv_id] >= self.buckets_config.per_thread_max:
                bucket_dropped += 1
                continue

            # If this is first item and we need at least 1, relax budget constraint
            if bucket_kept < min_required or remaining_budget >= chunk.token_count:
                selected.append(chunk)
                seen_chunks.add(dedup_key)
                thread_chunk_counts[conv_id] += 1
                if remaining_budget >= chunk.token_count:
                    remaining_budget -= chunk.token_count
                self.metrics.covered_threads.add(conv_id)
                self.metrics.selected_by_bucket[bucket_name] += 1
                bucket_kept += 1
            else:
                bucket_dropped += 1

        logger.info(f"Bucket {bucket_name}: kept={bucket_kept}, dropped={bucket_dropped}")

        # Bucket 4: critical_senders - chunks from important senders (rank >= 2)
        bucket_name = "critical_senders"
        bucket_kept = 0
        bucket_dropped = 0

        critical_chunks = [
            c for c in all_sorted if (getattr(c, "signals", {}) or {}).get("sender_rank", 1) >= 2
        ]
        critical_chunks = sorted(critical_chunks, key=lambda c: c.priority_score, reverse=True)

        for chunk in critical_chunks:
            # Skip if already selected
            dedup_key = self._get_dedup_key(chunk)
            if dedup_key in seen_chunks:
                if (
                    self.metrics.selected_by_bucket[bucket_name]
                    < self.buckets_config.critical_senders
                ):
                    self.metrics.selected_by_bucket[bucket_name] += 1
                    bucket_kept += 1
                bucket_dropped += 1
                continue

            if self.metrics.selected_by_bucket[bucket_name] >= self.buckets_config.critical_senders:
                bucket_dropped += 1
                break

            conv_id = getattr(chunk, "conversation_id", getattr(chunk, "thread_id", ""))
            if thread_chunk_counts[conv_id] >= self.buckets_config.per_thread_max:
                bucket_dropped += 1
                continue

            if remaining_budget >= chunk.token_count:
                selected.append(chunk)
                seen_chunks.add(dedup_key)
                thread_chunk_counts[conv_id] += 1
                remaining_budget -= chunk.token_count
                self.metrics.covered_threads.add(conv_id)
                self.metrics.selected_by_bucket[bucket_name] += 1
                bucket_kept += 1
            else:
                bucket_dropped += 1

        logger.info(f"Bucket {bucket_name}: kept={bucket_kept}, dropped={bucket_dropped}")

        # Bucket 5: remainder - fill up to max_total_chunks with general scoring
        bucket_name = "remainder"
        bucket_kept = 0
        bucket_dropped = 0

        remainder_chunks = [c for c in all_sorted]
        remainder_chunks = sorted(remainder_chunks, key=lambda c: c.priority_score, reverse=True)

        for chunk in remainder_chunks:
            # Skip if already selected
            dedup_key = self._get_dedup_key(chunk)
            if dedup_key in seen_chunks:
                bucket_dropped += 1
                continue

            if len(selected) >= self.buckets_config.max_total_chunks:
                bucket_dropped += 1
                break

            conv_id = getattr(chunk, "conversation_id", getattr(chunk, "thread_id", ""))
            if thread_chunk_counts[conv_id] >= self.buckets_config.per_thread_max:
                bucket_dropped += 1
                continue

            if remaining_budget >= chunk.token_count:
                selected.append(chunk)
                seen_chunks.add(dedup_key)
                thread_chunk_counts[conv_id] += 1
                remaining_budget -= chunk.token_count
                self.metrics.covered_threads.add(conv_id)
                self.metrics.selected_by_bucket[bucket_name] += 1
                bucket_kept += 1
            else:
                bucket_dropped += 1

        logger.info(f"Bucket {bucket_name}: kept={bucket_kept}, dropped={bucket_dropped}")

        # Track discarded action-like chunks
        for chunk in scored_chunks:
            if chunk not in selected:
                signals = getattr(chunk, "signals", {}) or {}
                action_verbs = signals.get("action_verbs", [])
                dates = signals.get("dates", [])
                if (
                    len(action_verbs) > 0
                    or len(dates) > 0
                    or getattr(chunk, "addressed_to_me", False)
                ):
                    self.metrics.discarded_action_like += 1

        # Track token budget used
        self.metrics.token_budget_used = token_budget - remaining_budget

        return selected

    def get_metrics(self) -> Dict:
        """Get selection metrics."""
        return self.metrics.to_dict()

    def _calculate_positive_signals(self, subject: str, sender_email: str) -> float:
        """Legacy scoring helper retained for older tests and tooling."""
        score = 0.0
        text = f"{subject} {sender_email}".lower()
        positive_markers = [
            "urgent",
            "важно",
            "meeting",
            "встреч",
            "review",
            "approve",
            "action",
            "please",
        ]
        for marker in positive_markers:
            if marker in text:
                score += 1.0
        return score

    def _calculate_negative_signals(self, subject: str, sender_email: str) -> float:
        """Legacy negative scoring helper retained for compatibility."""
        return 1.0 if self._is_service_email(subject, sender_email) else 0.0

    def _is_service_email(self, subject: str, sender_email: str) -> bool:
        """Legacy service-mail detection helper."""
        return bool(self.negative_regex.search(f"{subject} {sender_email}"))

    def _calculate_sender_weight(self, sender_email: str) -> float:
        """Legacy sender weighting helper."""
        sender = sender_email.lower()
        if "ceo@" in sender or "director@" in sender:
            return 2.0
        if "manager@" in sender or "lead@" in sender:
            return 1.0
        return 0.0

    def _calculate_thread_activity(self, message_count: int, _latest_message_time) -> float:
        """Legacy thread activity helper."""
        return 1.0 if message_count > 1 else 0.0

    def _ensure_token_budget(
        self, selected: List[EvidenceChunk], max_tokens: int
    ) -> List[EvidenceChunk]:
        """
        Auto-shrink selected chunks to fit budget while preserving minimum quotas.

        Shrink order:
        1. Remove remainder (non-bucket) chunks with low score
        2. Deduplicate chunks exceeding per_thread_max
        3. Remove chunks from buckets exceeding minimum quotas (low priority first):
           - critical_senders
           - dates_deadlines
           - addressed_to_me
           - threads_top (highest priority, protected)
        4. If still over budget, remove globally lowest scored while keeping min quotas
        """
        self.metrics.budget_requested = sum(c.token_count for c in selected)

        if self.metrics.budget_requested <= max_tokens:
            self.metrics.budget_applied = self.metrics.budget_requested
            return selected

        logger.info(
            "Token budget exceeded, applying auto-shrink",
            requested=self.metrics.budget_requested,
            max_tokens=max_tokens,
        )

        # Track which chunks belong to which bucket
        chunk_to_bucket = {}
        for chunk in selected:
            bucket = self._get_chunk_bucket(chunk)
            chunk_to_bucket[id(chunk)] = bucket

        # Step 1: Remove remainder chunks with low score
        remainder_chunks = [c for c in selected if chunk_to_bucket[id(c)] == "remainder"]
        remainder_chunks.sort(key=lambda c: c.priority_score)

        kept = [c for c in selected if chunk_to_bucket[id(c)] != "remainder"]
        current_tokens = sum(c.token_count for c in kept)

        # Add back remainder chunks that fit
        for chunk in reversed(remainder_chunks):
            if current_tokens + chunk.token_count <= max_tokens:
                kept.append(chunk)
                current_tokens += chunk.token_count
            else:
                self.metrics.shrinks_count += 1

        if current_tokens <= max_tokens:
            self.metrics.budget_applied = current_tokens
            return kept

        # Step 2: Deduplicate over per_thread_max
        kept = self._deduplicate_over_thread_cap(kept, max_tokens)
        current_tokens = sum(c.token_count for c in kept)

        if current_tokens <= max_tokens:
            self.metrics.budget_applied = current_tokens
            return kept

        # Step 3: Shrink buckets over min quotas (priority order)
        if self.shrink_config.preserve_min_quotas:
            bucket_order = ["critical_senders", "dates_deadlines", "addressed_to_me"]

            for bucket_name in bucket_order:
                min_quota = getattr(self.buckets_config, bucket_name)
                bucket_chunks = [c for c in kept if chunk_to_bucket[id(c)] == bucket_name]

                if len(bucket_chunks) > min_quota and current_tokens > max_tokens:
                    # Sort by score, keep min_quota best
                    bucket_chunks.sort(key=lambda c: c.priority_score, reverse=True)
                    to_remove = bucket_chunks[min_quota:]

                    # Remove lowest scored over-quota chunks
                    to_remove.sort(key=lambda c: c.priority_score)
                    for chunk in to_remove:
                        if current_tokens > max_tokens:
                            kept.remove(chunk)
                            current_tokens -= chunk.token_count
                            self.metrics.shrinks_count += 1
                        else:
                            break

        # Step 4: Global low-score removal (preserve min quotas)
        if current_tokens > max_tokens:
            kept = self._global_shrink_preserve_quotas(kept, max_tokens, chunk_to_bucket)
            current_tokens = sum(c.token_count for c in kept)

        self.metrics.budget_applied = current_tokens
        logger.info(
            "Auto-shrink completed",
            removed=self.metrics.shrinks_count,
            final_tokens=current_tokens,
        )

        return kept

    def _get_chunk_bucket(self, chunk: EvidenceChunk) -> str:
        """Determine which bucket a chunk belongs to."""
        signals = getattr(chunk, "signals", {}) or {}
        # Check critical_senders
        if signals.get("sender_rank", 1) >= 2:
            return "critical_senders"

        # Check dates_deadlines
        if len(signals.get("dates", [])) > 0:
            return "dates_deadlines"

        # Check addressed_to_me
        if getattr(chunk, "addressed_to_me", False):
            return "addressed_to_me"

        # Default to remainder
        return "remainder"

    def _deduplicate_over_thread_cap(
        self, chunks: List[EvidenceChunk], max_tokens: int
    ) -> List[EvidenceChunk]:
        """Remove chunks exceeding per_thread_max, keeping highest scored."""
        thread_chunks = defaultdict(list)
        for chunk in chunks:
            conv_id = getattr(chunk, "conversation_id", getattr(chunk, "thread_id", ""))
            thread_chunks[conv_id].append(chunk)

        kept = []
        for conv_id, conv_chunks in thread_chunks.items():
            # Sort by score and keep up to per_thread_max
            conv_chunks.sort(key=lambda c: c.priority_score, reverse=True)
            max_per_thread = self.context_budget_config.per_thread_max

            for i, chunk in enumerate(conv_chunks):
                if i < max_per_thread:
                    kept.append(chunk)
                else:
                    self.metrics.shrinks_count += 1

        return kept

    def _global_shrink_preserve_quotas(
        self,
        chunks: List[EvidenceChunk],
        max_tokens: int,
        chunk_to_bucket: Dict[int, str],
    ) -> List[EvidenceChunk]:
        """Final shrink by removing lowest scored chunks while preserving min quotas."""
        # Count current bucket sizes
        bucket_counts = defaultdict(int)
        for chunk in chunks:
            bucket = chunk_to_bucket[id(chunk)]
            bucket_counts[bucket] += 1

        # Sort all chunks by score (lowest first for removal)
        chunks_sorted = sorted(chunks, key=lambda c: c.priority_score)

        current_tokens = sum(c.token_count for c in chunks)
        kept = list(chunks)

        for chunk in chunks_sorted:
            if current_tokens <= max_tokens:
                break

            bucket = chunk_to_bucket[id(chunk)]

            # Check if we can remove this chunk without violating min quota
            if bucket in [
                "threads_top",
                "addressed_to_me",
                "dates_deadlines",
                "critical_senders",
            ]:
                min_quota = getattr(self.buckets_config, bucket)
                if bucket_counts[bucket] > min_quota:
                    kept.remove(chunk)
                    current_tokens -= chunk.token_count
                    bucket_counts[bucket] -= 1
                    self.metrics.shrinks_count += 1
            else:
                # Remainder bucket, can remove freely
                kept.remove(chunk)
                current_tokens -= chunk.token_count
                self.metrics.shrinks_count += 1

        return kept
