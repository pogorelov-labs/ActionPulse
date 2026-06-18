"""
Priority ranking for digest items.

Features:
- user_in_to vs in_cc
- action/mention presence (from actions extraction)
- due date presence
- sender importance
- thread length
- recency
- attachments
- project tags (JIRA, etc.)

No external ML dependencies - pure rule-based scoring.
"""

import re
import structlog
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass

from pydantic import BaseModel

logger = structlog.get_logger()


@dataclass
class RankingFeatures:
    """Features extracted for ranking."""

    user_in_to: bool = False
    user_in_cc: bool = False
    has_action: bool = False
    has_mention: bool = False
    has_due_date: bool = False
    sender_importance: float = 0.5  # 0.0-1.0
    thread_length: int = 1
    hours_since_received: float = 24.0
    has_attachments: bool = False
    has_project_tag: bool = False  # [JIRA-123], etc.

    # Computed
    rank_score: float = 0.0


class DigestRanker:
    """Rank digest items by actionability."""

    # Default feature weights
    DEFAULT_WEIGHTS = {
        "user_in_to": 0.15,  # Direct recipient
        "user_in_cc": 0.05,  # CC recipient
        "has_action": 0.20,  # Action extracted
        "has_mention": 0.10,  # User mentioned
        "has_due_date": 0.15,  # Deadline present
        "sender_importance": 0.10,  # Important sender
        "thread_length": 0.05,  # Long conversation
        "recency": 0.10,  # Recent message
        "has_attachments": 0.05,  # Has attachments
        "has_project_tag": 0.05,  # Project tag present
    }

    # Project tag patterns
    PROJECT_TAG_PATTERNS = [
        r"\[JIRA-\d+\]",
        r"\[PROJ-\d+\]",
        r"\[TASK-\d+\]",
        r"\[BUG-\d+\]",
        r"\[TICKET-\d+\]",
        r"\[#\d+\]",
    ]

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        user_aliases: Optional[List[str]] = None,
        important_senders: Optional[List[str]] = None,
    ):
        """
        Initialize DigestRanker.

        Args:
            weights: Feature weights (optional, uses defaults if not provided)
            user_aliases: User email aliases for to/cc detection
            important_senders: List of important sender emails/domains
        """
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self.user_aliases = [alias.lower() for alias in (user_aliases or [])]
        self.important_senders = [s.lower() for s in (important_senders or [])]

        # Compile project tag patterns
        self.project_tag_pattern = re.compile("|".join(self.PROJECT_TAG_PATTERNS))

        # Validate weights
        self._validate_weights()

    def _validate_weights(self):
        """Validate that weights are reasonable."""
        for key, weight in self.weights.items():
            if not 0.0 <= weight <= 1.0:
                logger.warning("Weight out of range", feature=key, weight=weight)
                self.weights[key] = max(0.0, min(1.0, weight))

        # Normalize weights to sum to 1.0
        total = sum(self.weights.values())
        if total > 0:
            for key in self.weights:
                self.weights[key] /= total

    def rank_items(self, items: List[Any], evidence_chunks: List[Any]) -> List[Any]:
        """
        Rank digest items by actionability score.

        Args:
            items: List of digest items (ActionItem, DeadlineMeeting, etc.)
            evidence_chunks: Evidence chunks for feature extraction

        Returns:
            Sorted list of items (highest score first)
        """
        logger.info("Starting item ranking", item_count=len(items))

        ranked_items: List[Any] = []
        for item in items:
            features = self._extract_features(item, evidence_chunks)
            score = self._calculate_score(features)

            if isinstance(item, BaseModel):
                ranked_items.append(item.model_copy(update={"rank_score": score}))
            elif hasattr(item, "rank_score"):
                item.rank_score = score
                ranked_items.append(item)
            else:
                ranked_items.append(item)

            logger.debug(
                "Item ranked",
                evidence_id=getattr(item, "evidence_id", "unknown"),
                score=score,
                features=features.__dict__,
            )

        def _score_key(it: Any) -> float:
            rs = getattr(it, "rank_score", None)
            return float(rs) if rs is not None else 0.0

        sorted_items = sorted(ranked_items, key=_score_key, reverse=True)

        logger.info(
            "Ranking completed",
            item_count=len(sorted_items),
            avg_score=(
                sum(_score_key(i) for i in sorted_items) / len(sorted_items) if sorted_items else 0
            ),
        )

        return sorted_items

    def _extract_features(self, item: Any, evidence_chunks: List[Any]) -> RankingFeatures:
        """
        Extract ranking features from item.

        Args:
            item: Digest item
            evidence_chunks: All evidence chunks

        Returns:
            RankingFeatures
        """
        features = RankingFeatures()

        # Find matching evidence chunk
        evidence_id = getattr(item, "evidence_id", None)
        if not evidence_id:
            return features

        matching_chunks = [c for c in evidence_chunks if c.evidence_id == evidence_id]
        if not matching_chunks:
            return features

        chunk = matching_chunks[0]

        # Feature 1: user_in_to / user_in_cc
        if hasattr(chunk, "message_metadata"):
            metadata = chunk.message_metadata
            to_recipients = metadata.get("to_recipients") or metadata.get("to") or []
            cc_recipients = metadata.get("cc_recipients") or metadata.get("cc") or []

            for alias in self.user_aliases:
                if any(alias in str(r).lower() for r in to_recipients):
                    features.user_in_to = True
                    break

            if not features.user_in_to:
                for alias in self.user_aliases:
                    if any(alias in str(r).lower() for r in cc_recipients):
                        features.user_in_cc = True
                        break

        # Feature 2: action/mention (check if item is ExtractedActionItem or has action markers)
        item_type = type(item).__name__
        if item_type == "Item":
            title = getattr(item, "title", "") or ""
            if self._has_action_markers(title):
                features.has_action = True
        elif item_type == "ExtractedActionItem":
            if getattr(item, "type", "") == "action":
                features.has_action = True
            elif getattr(item, "type", "") == "mention":
                features.has_mention = True
        elif item_type in ("ActionItem", "DeadlineMeeting"):
            # Check if description/quote contains action markers
            text = getattr(item, "description", "") or getattr(item, "quote", "")
            if self._has_action_markers(text):
                features.has_action = True

        # Feature 3: due date
        due_date_fields = ["due", "due_date", "due_date_normalized", "date_time"]
        for field in due_date_fields:
            if hasattr(item, field) and getattr(item, field):
                features.has_due_date = True
                break

        # Feature 4: sender importance
        sender = (
            getattr(chunk, "sender", "")
            or chunk.message_metadata.get("from", "")
            or chunk.message_metadata.get("sender", "")
        )
        if sender:
            features.sender_importance = self._calculate_sender_importance(sender)

        # Feature 5: thread length
        if hasattr(chunk, "thread_id"):
            # Count chunks in same thread
            thread_chunks = [
                c for c in evidence_chunks if getattr(c, "thread_id", None) == chunk.thread_id
            ]
            features.thread_length = len(thread_chunks)

        # Feature 6: recency
        if hasattr(chunk, "timestamp"):
            try:
                timestamp = datetime.fromisoformat(chunk.timestamp.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                hours_diff = (now - timestamp).total_seconds() / 3600
                features.hours_since_received = hours_diff
            except Exception as e:
                logger.debug("Failed to parse timestamp", error=str(e))

        # Feature 7: attachments (check in metadata)
        if hasattr(chunk, "message_metadata"):
            has_attachments = chunk.message_metadata.get("has_attachments", False)
            features.has_attachments = has_attachments

        # Feature 8: project tags
        source_subject = getattr(item, "source_subject", "") or chunk.message_metadata.get(
            "subject", ""
        )
        if source_subject:
            if self.project_tag_pattern.search(source_subject):
                features.has_project_tag = True

        return features

    def _has_action_markers(self, text: str) -> bool:
        """Check if text contains action markers."""
        if not text:
            return False

        text_lower = text.lower()

        # English action markers
        en_markers = [
            "please",
            "need to",
            "must",
            "should",
            "can you",
            "could you",
            "review",
            "approve",
        ]
        # Russian action markers
        ru_markers = [
            "пожалуйста",
            "нужно",
            "необходимо",
            "прошу",
            "сделайте",
            "проверьте",
        ]

        for marker in en_markers + ru_markers:
            if marker in text_lower:
                return True

        return False

    def _calculate_sender_importance(self, sender: str) -> float:
        """
        Calculate sender importance score.

        Args:
            sender: Sender email

        Returns:
            Importance score (0.0-1.0)
        """
        sender_lower = sender.lower()

        # Check exact match
        if sender_lower in self.important_senders:
            return 1.0

        for important in self.important_senders:
            if important.endswith("@") and important in sender_lower:
                return 0.7

        # Check domain match
        if "@" in sender_lower:
            domain = sender_lower.split("@")[1]
            for important in self.important_senders:
                if important.startswith("@"):
                    important_domain = important[1:]
                    if domain == important_domain:
                        return 0.8
                elif "@" not in important and important in domain:  # Domain keyword match
                    return 0.7

        # Default: medium importance
        return 0.5

    def _calculate_score(self, features: RankingFeatures) -> float:
        """
        Calculate final ranking score.

        Args:
            features: Extracted features

        Returns:
            Score (0.0-1.0)
        """
        score = 0.0

        # Binary features (0 or 1)
        if features.user_in_to:
            score += self.weights["user_in_to"]
        if features.user_in_cc:
            score += self.weights["user_in_cc"]
        if features.has_action:
            score += self.weights["has_action"]
        if features.has_mention:
            score += self.weights["has_mention"]
        if features.has_due_date:
            score += self.weights["has_due_date"]
        if features.has_attachments:
            score += self.weights["has_attachments"]
        if features.has_project_tag:
            score += self.weights["has_project_tag"]

        # Continuous features (0-1 normalized)

        # Sender importance (already 0-1)
        score += self.weights["sender_importance"] * features.sender_importance

        # Thread length (normalize: 1-10 messages → 0-1)
        thread_score = min(features.thread_length / 10.0, 1.0)
        score += self.weights["thread_length"] * thread_score

        # Recency (normalize: 0-48 hours → 1-0, exponential decay)
        recency_score = max(0.0, 1.0 - (features.hours_since_received / 48.0))
        score += self.weights["recency"] * recency_score

        # Clamp to [0, 1]
        score = max(0.0, min(1.0, score))

        features.rank_score = score
        return score

    def get_top_n_actions_share(self, items: List[Any], n: int = 10) -> float:
        """
        Calculate percentage of top-N items that have actions.

        Args:
            items: Ranked items list
            n: Number of top items to check

        Returns:
            Share of actionable items in top-N (0.0-1.0)
        """
        if not items or n <= 0:
            return 0.0

        top_n = items[:n]
        action_count = 0

        for item in top_n:
            # Check if item has action
            item_type = type(item).__name__
            if item_type == "ExtractedActionItem":
                if getattr(item, "type", "") in ("action", "question"):
                    action_count += 1
            elif item_type == "ActionItem":
                action_count += 1
            elif hasattr(item, "has_due_date") and item.has_due_date:
                action_count += 1

        return action_count / len(top_n)
