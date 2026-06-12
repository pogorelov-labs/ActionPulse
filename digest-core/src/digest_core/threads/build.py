"""
Conversation thread building from normalized messages.

Enhanced with:
- SubjectNormalizer for robust subject matching
- Semantic similarity fallback
- Anti-duplicator by body checksum
- Message-ID/In-Reply-To/References handling
"""

from collections import defaultdict
from typing import List, NamedTuple, Dict
from datetime import datetime
import hashlib
import structlog

from digest_core.ingest.ews import NormalizedMessage
from digest_core.threads.subject_normalizer import (
    SubjectNormalizer,
    calculate_text_similarity,
)

logger = structlog.get_logger()


class ConversationThread(NamedTuple):
    """A conversation thread containing multiple messages."""

    conversation_id: str
    messages: List[NormalizedMessage]
    latest_message_time: datetime
    participant_count: int
    message_count: int
    # New: track if merged by semantic similarity
    merged_by_semantic: bool = False
    # New: track duplicate sources
    duplicate_sources: List[str] = []


class ThreadBuilder:
    """Build conversation threads from normalized messages."""

    def __init__(self, semantic_similarity_threshold: float = 0.7, embedding_merger=None):
        """
        Initialize ThreadBuilder.

        Args:
            semantic_similarity_threshold: Threshold for semantic merging (0.0-1.0)
            embedding_merger: optional EmbeddingThreadMerger (PR12a cosine tier);
                None (the default) keeps grouping byte-identical to the
                heuristic path — the flag lives in ThreadingConfig.
        """
        self.max_thread_age_hours = 48
        self.max_messages_per_thread = 50
        self.semantic_similarity_threshold = semantic_similarity_threshold
        self.embedding_merger = embedding_merger

        # Initialize subject normalizer
        self.subject_normalizer = SubjectNormalizer()

        # Metrics tracking
        self.stats = {
            "subjects_normalized": 0,
            "threads_merged_by_id": 0,
            "threads_merged_by_subject": 0,
            "threads_merged_by_semantic": 0,
            "threads_merged_by_embedding": 0,
            "duplicates_found": 0,
        }

    def build_threads(self, messages: List[NormalizedMessage]) -> List[ConversationThread]:
        """Build conversation threads from normalized messages."""
        logger.info("Building conversation threads", message_count=len(messages))

        # Reset stats
        self.stats = {k: 0 for k in self.stats}

        # Step 1: Anti-duplicator by checksum
        unique_messages, duplicate_map = self._deduplicate_by_checksum(messages)

        # Step 2: Build Message-ID index for In-Reply-To/References
        msg_id_index = self._build_msg_id_index(unique_messages)

        # Step 3: Group messages into threads
        thread_groups = self._group_messages_into_threads(unique_messages, msg_id_index)

        # Step 4: Merge threads by semantic similarity (fallback)
        thread_groups = self._merge_by_semantic_similarity(thread_groups)

        # Step 4b (PR12a, behind ThreadingConfig.embedding_merge): cosine tier
        # over the heuristic-weak groups — may only merge, never lose messages.
        if self.embedding_merger is not None:
            thread_groups = self.embedding_merger.merge(thread_groups)
            self.stats["threads_merged_by_embedding"] = self.embedding_merger.merges_made

        # Step 5: Build thread objects
        threads = []
        for thread_id, thread_messages in thread_groups.items():
            try:
                thread = self._build_single_thread(
                    thread_id,
                    thread_messages,
                    duplicate_sources=duplicate_map.get(thread_id, []),
                )
                if thread:
                    threads.append(thread)
            except Exception as e:
                logger.warning("Failed to build thread", thread_id=thread_id, error=str(e))
                continue

        # Sort threads by latest message time (most recent first)
        threads.sort(key=lambda t: t.latest_message_time, reverse=True)

        logger.info("Thread building completed", threads_created=len(threads), **self.stats)

        return threads

    def _deduplicate_by_checksum(
        self, messages: List[NormalizedMessage]
    ) -> tuple[List[NormalizedMessage], Dict[str, List[str]]]:
        """
        Deduplicate messages by body checksum.

        Args:
            messages: List of messages

        Returns:
            Tuple of (unique_messages, duplicate_map)
            duplicate_map: {primary_msg_id: [duplicate_msg_ids]}
        """
        checksum_index = {}  # checksum -> primary msg_id
        duplicate_map = defaultdict(list)
        unique_messages = []
        seen_msg_ids = set()

        for msg in messages:
            # Calculate checksum of body
            body_text = msg.text_body or ""
            checksum = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

            # Check if we've seen this exact body before
            if checksum in checksum_index:
                primary_msg_id = checksum_index[checksum]
                duplicate_map[primary_msg_id].append(msg.msg_id)
                self.stats["duplicates_found"] += 1
                logger.debug(
                    "Duplicate message found",
                    primary_msg_id=primary_msg_id,
                    duplicate_msg_id=msg.msg_id,
                    checksum=checksum[:16],
                )
                continue

            # First time seeing this body
            if msg.msg_id not in seen_msg_ids:
                checksum_index[checksum] = msg.msg_id
                seen_msg_ids.add(msg.msg_id)
                unique_messages.append(msg)

        logger.info(
            "Deduplication completed",
            original_count=len(messages),
            unique_count=len(unique_messages),
            duplicates=self.stats["duplicates_found"],
        )

        return unique_messages, dict(duplicate_map)

    def _build_msg_id_index(
        self, messages: List[NormalizedMessage]
    ) -> Dict[str, NormalizedMessage]:
        """Build index of Message-ID -> message for References lookup."""
        index = {}
        for msg in messages:
            if msg.msg_id:
                index[msg.msg_id] = msg
            # Also index Internet-Message-ID if different
            if hasattr(msg, "internet_message_id") and msg.internet_message_id:
                index[msg.internet_message_id] = msg
        return index

    def _group_messages_into_threads(
        self,
        messages: List[NormalizedMessage],
        msg_id_index: Dict[str, NormalizedMessage],
    ) -> Dict[str, List[NormalizedMessage]]:
        """
        Group messages into threads using:
        1. conversation_id (from EWS)
        2. In-Reply-To / References headers
        3. Normalized subject fallback

        Returns:
            Dict of {thread_id: [messages]}
        """
        thread_groups = defaultdict(list)
        thread_id_map = {}  # msg_id -> assigned_thread_id

        for msg in messages:
            assigned_thread_id = None

            # Strategy 1: Use EWS conversation_id if available
            if msg.conversation_id:
                assigned_thread_id = f"conv_{msg.conversation_id}"
                self.stats["threads_merged_by_id"] += 1

            # Strategy 2: Check In-Reply-To / References
            if not assigned_thread_id:
                # Check if this message references another message we've seen
                reply_to_id = getattr(msg, "in_reply_to", None)
                references = getattr(msg, "references", []) or []

                # Look for parent message in our index
                for ref_id in [reply_to_id] + list(references):
                    if ref_id and ref_id in thread_id_map:
                        assigned_thread_id = thread_id_map[ref_id]
                        self.stats["threads_merged_by_id"] += 1
                        break

            # Strategy 3: Normalize subject and use as thread key
            if not assigned_thread_id:
                normalized_subject, _ = self.subject_normalizer.normalize(msg.subject)
                self.stats["subjects_normalized"] += 1

                if normalized_subject:
                    # Check if we have existing thread with this subject.
                    # Content-hash (not builtin hash()) so the id is stable across
                    # runs — PYTHONHASHSEED randomization would otherwise leak into
                    # conversation_id -> source_ref -> evidence_id (PR1). Mirrors :128.
                    subject_thread_id = (
                        "subj_"
                        + hashlib.sha256(normalized_subject.encode("utf-8")).hexdigest()[:16]
                    )

                    # Look for existing thread with same normalized subject
                    found_existing = False
                    for existing_thread_id, existing_messages in thread_groups.items():
                        if existing_thread_id.startswith("subj_"):
                            # Check if any message in this thread has same normalized subject
                            for existing_msg in existing_messages:
                                existing_norm, _ = self.subject_normalizer.normalize(
                                    existing_msg.subject
                                )
                                if existing_norm == normalized_subject:
                                    assigned_thread_id = existing_thread_id
                                    found_existing = True
                                    self.stats["threads_merged_by_subject"] += 1
                                    break
                        if found_existing:
                            break

                    if not assigned_thread_id:
                        assigned_thread_id = subject_thread_id
                else:
                    # No subject, create single-message thread
                    assigned_thread_id = f"single_{msg.msg_id}"

            # Add message to thread
            thread_groups[assigned_thread_id].append(msg)
            thread_id_map[msg.msg_id] = assigned_thread_id

        logger.info(
            "Messages grouped into threads",
            thread_count=len(thread_groups),
            merged_by_id=self.stats["threads_merged_by_id"],
            merged_by_subject=self.stats["threads_merged_by_subject"],
        )

        return thread_groups

    def _merge_by_semantic_similarity(
        self, thread_groups: Dict[str, List[NormalizedMessage]]
    ) -> Dict[str, List[NormalizedMessage]]:
        """
        Merge threads with same normalized subject AND similar content.

        This is a fallback for cases where:
        - conversation_id is missing
        - In-Reply-To/References are missing
        - But content is clearly related

        Args:
            thread_groups: Dict of {thread_id: [messages]}

        Returns:
            Merged thread_groups
        """
        # Group threads by normalized subject
        subject_groups = defaultdict(list)

        for thread_id, messages in thread_groups.items():
            if not messages:
                continue

            # Get normalized subject from first message
            first_msg = messages[0]
            normalized_subject, _ = self.subject_normalizer.normalize(first_msg.subject)

            if normalized_subject:
                subject_groups[normalized_subject].append((thread_id, messages))

        # Merge threads within each subject group if content is similar
        merged_groups = {}
        processed_thread_ids = set()

        for norm_subject, thread_list in subject_groups.items():
            if len(thread_list) <= 1:
                # Only one thread with this subject, no merging needed
                thread_id, messages = thread_list[0]
                merged_groups[thread_id] = messages
                processed_thread_ids.add(thread_id)
                continue

            # Multiple threads with same normalized subject
            # Check if they should be merged based on content similarity
            clusters = []  # List of [thread_ids, messages]

            for thread_id, messages in thread_list:
                if not messages:
                    continue

                # Get first message body for comparison
                first_body = messages[0].text_body or ""

                # Try to find similar cluster
                merged = False
                for cluster_threads, cluster_messages in clusters:
                    cluster_first_body = cluster_messages[0].text_body or ""

                    similarity = calculate_text_similarity(
                        first_body, cluster_first_body, max_chars=200
                    )

                    if similarity >= self.semantic_similarity_threshold:
                        # Merge into this cluster
                        cluster_threads.append(thread_id)
                        cluster_messages.extend(messages)
                        merged = True
                        self.stats["threads_merged_by_semantic"] += 1
                        logger.debug(
                            "Merged thread by semantic similarity",
                            thread_id=thread_id,
                            similarity=similarity,
                        )
                        break

                if not merged:
                    # Create new cluster
                    clusters.append(([thread_id], messages))

            # Add clusters to merged groups
            for cluster_threads, cluster_messages in clusters:
                # Use first thread_id as primary
                primary_thread_id = cluster_threads[0]
                merged_groups[primary_thread_id] = cluster_messages
                for tid in cluster_threads:
                    processed_thread_ids.add(tid)

        # Add threads that weren't part of subject groups
        for thread_id, messages in thread_groups.items():
            if thread_id not in processed_thread_ids:
                merged_groups[thread_id] = messages

        if self.stats["threads_merged_by_semantic"] > 0:
            logger.info(
                "Semantic merging completed",
                merged_count=self.stats["threads_merged_by_semantic"],
                final_thread_count=len(merged_groups),
            )

        return merged_groups

    def _build_single_thread(
        self,
        conversation_id: str,
        messages: List[NormalizedMessage],
        duplicate_sources: List[str] = None,
    ) -> ConversationThread:
        """Build a single conversation thread."""
        if not messages:
            return None

        # Sort messages by datetime_received
        messages.sort(key=lambda m: m.datetime_received)

        # Limit thread size
        if len(messages) > self.max_messages_per_thread:
            logger.warning(
                "Thread too large, truncating",
                conversation_id=conversation_id,
                original_count=len(messages),
                truncated_count=self.max_messages_per_thread,
            )
            messages = messages[-self.max_messages_per_thread :]

        # Get latest message time
        latest_time = max(msg.datetime_received for msg in messages)

        # Count unique participants
        participants = set()
        for msg in messages:
            participants.add(msg.sender_email)
            participants.update(msg.to_recipients)
            participants.update(msg.cc_recipients)

        # Remove empty participants
        participants.discard("")

        # Detect if merged by semantic
        merged_by_semantic = conversation_id.startswith("subj_")

        return ConversationThread(
            conversation_id=conversation_id,
            messages=messages,
            latest_message_time=latest_time,
            participant_count=len(participants),
            message_count=len(messages),
            merged_by_semantic=merged_by_semantic,
            duplicate_sources=duplicate_sources or [],
        )

    def filter_recent_threads(
        self, threads: List[ConversationThread], hours: int = 24
    ) -> List[ConversationThread]:
        """Filter threads to only include recent activity."""
        from datetime import datetime, timezone, timedelta

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        recent_threads = [thread for thread in threads if thread.latest_message_time >= cutoff_time]

        logger.info(
            "Filtered recent threads",
            original_count=len(threads),
            recent_count=len(recent_threads),
            hours=hours,
        )

        return recent_threads

    def prioritize_threads(self, threads: List[ConversationThread]) -> List[ConversationThread]:
        """Prioritize threads based on relevance heuristics."""

        def thread_priority(thread: ConversationThread) -> float:
            """Calculate priority score for a thread."""
            score = 0.0

            # Recent activity gets higher priority
            from datetime import datetime, timezone

            hours_ago = (
                datetime.now(timezone.utc) - thread.latest_message_time
            ).total_seconds() / 3600
            if hours_ago < 1:
                score += 10.0
            elif hours_ago < 6:
                score += 5.0
            elif hours_ago < 24:
                score += 2.0

            # More participants might indicate importance
            if thread.participant_count > 5:
                score += 2.0
            elif thread.participant_count > 2:
                score += 1.0

            # Longer conversations might be more important
            if thread.message_count > 10:
                score += 1.0
            elif thread.message_count > 5:
                score += 0.5

            return score

        # Sort by priority (highest first)
        prioritized_threads = sorted(threads, key=thread_priority, reverse=True)

        logger.info("Threads prioritized", thread_count=len(prioritized_threads))

        return prioritized_threads

    def get_stats(self) -> Dict[str, int]:
        """Get threading statistics."""
        return self.stats.copy()

    def calculate_redundancy_index(self, original_count: int, final_count: int) -> float:
        """
        Calculate redundancy index (how much duplication was reduced).

        Args:
            original_count: Original message count
            final_count: Final unique message count

        Returns:
            Redundancy reduction percentage (0.0-1.0)
        """
        if original_count == 0:
            return 0.0

        reduction = (original_count - final_count) / original_count
        return reduction
