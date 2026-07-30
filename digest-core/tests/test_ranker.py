"""
Tests for digest item ranking functionality.

Coverage:
- Feature extraction: user_in_to, user_in_cc, actions, mentions, due dates, sender importance,
  thread length, recency, attachments, project tags
- Score calculation and normalization
- Integration: actionable items should rank higher
"""

import pytest
from datetime import datetime, timedelta, timezone
from digest_core.select.ranker import DigestRanker, RankingFeatures
from digest_core.llm.schemas import Item
from digest_core.evidence.split import EvidenceChunk


def _item(title="Action", evidence_id="ev1", *, due=None, source_subject=None):
    """A v1 ``Item`` carrier.

    These tests used to build ActionItem/DeadlineMeeting/ExtractedActionItem, but
    ``run.py`` only ever ranks ``Item`` (run.py passes ``section.items``), so the
    v2 types exercised branches production could not reach. They went with the v2
    digest surface; the assertions below are unchanged and now run against the
    type the ranker actually sees.
    """
    return Item(
        title=title,
        evidence_id=evidence_id,
        confidence=0.9,
        source_ref={"type": "email", "msg_id": evidence_id.replace("ev", "msg")},
        due=due,
        source_subject=source_subject,
    )


class TestRankingFeatures:
    """Test feature extraction and scoring."""

    def test_feature_extraction_user_in_to(self):
        """Test that user in To recipients increases score."""
        ranker = DigestRanker(user_aliases=["user@example.com"])

        # Create mock evidence chunk with user in To
        chunk = EvidenceChunk(
            evidence_id="ev1",
            msg_id="msg1",
            text="Test content",
            sender="sender@example.com",
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_metadata={
                "to_recipients": ["user@example.com", "other@example.com"],
                "cc_recipients": [],
                "subject": "Test",
            },
        )

        item = _item(evidence_id="ev1")

        features = ranker._extract_features(item, [chunk])
        assert features.user_in_to is True
        assert features.user_in_cc is False

    def test_feature_extraction_user_in_cc(self):
        """Test that user in CC recipients increases score (but less than To)."""
        ranker = DigestRanker(user_aliases=["user@example.com"])

        chunk = EvidenceChunk(
            evidence_id="ev1",
            msg_id="msg1",
            text="Test content",
            sender="sender@example.com",
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_metadata={
                "to_recipients": ["other@example.com"],
                "cc_recipients": ["user@example.com"],
                "subject": "Test",
            },
        )

        item = _item(evidence_id="ev1")

        features = ranker._extract_features(item, [chunk])
        assert features.user_in_to is False
        assert features.user_in_cc is True

    def test_feature_extraction_has_action(self):
        """Test detection of action markers."""
        ranker = DigestRanker()

        # Test English markers
        assert ranker._has_action_markers("Please review this document") is True
        assert ranker._has_action_markers("Can you check this?") is True
        assert ranker._has_action_markers("You must approve by Friday") is True

        # Test Russian markers
        assert ranker._has_action_markers("Пожалуйста проверьте") is True
        assert ranker._has_action_markers("Нужно согласовать") is True
        assert ranker._has_action_markers("Прошу подтвердить") is True

        # Test no markers
        assert ranker._has_action_markers("Just FYI, project is going well") is False

    def test_feature_extraction_due_date(self):
        """Test detection of due dates."""
        ranker = DigestRanker()

        chunk = EvidenceChunk(
            evidence_id="ev1",
            msg_id="msg1",
            text="Test content",
            sender="sender@example.com",
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_metadata={"subject": "Test"},
        )

        # Item with due date
        item_with_due = _item("Meeting", due="2024-12-31T15:00:00-03:00")

        features = ranker._extract_features(item_with_due, [chunk])
        assert features.has_due_date is True

        # Item without due date
        item_without_due = _item()

        features = ranker._extract_features(item_without_due, [chunk])
        # Item with `due` unset
        assert features.has_due_date is False

    def test_feature_extraction_sender_importance(self):
        """Test sender importance scoring."""
        ranker = DigestRanker(important_senders=["ceo@example.com", "manager@"])

        # Exact match
        assert ranker._calculate_sender_importance("ceo@example.com") == 1.0

        # Partial domain match
        assert ranker._calculate_sender_importance("john@manager@company.com") >= 0.7

        # No match (default)
        assert ranker._calculate_sender_importance("random@example.com") == 0.5

    def test_feature_extraction_thread_length(self):
        """Test thread length scoring."""
        ranker = DigestRanker()

        # Create multiple chunks in same thread
        chunks = [
            EvidenceChunk(
                evidence_id=f"ev{i}",
                msg_id=f"msg{i}",
                text="Content",
                sender="sender@example.com",
                timestamp=datetime.now(timezone.utc).isoformat(),
                message_metadata={"subject": "Test"},
                thread_id="thread1",
            )
            for i in range(5)
        ]

        item = _item(evidence_id="ev0")

        features = ranker._extract_features(item, chunks)
        assert features.thread_length == 5

    def test_feature_extraction_recency(self):
        """Test recency scoring."""
        ranker = DigestRanker()

        # Recent message (1 hour ago)
        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        chunk_recent = EvidenceChunk(
            evidence_id="ev1",
            msg_id="msg1",
            text="Content",
            sender="sender@example.com",
            timestamp=recent_time.isoformat(),
            message_metadata={"subject": "Test"},
        )

        item = _item(evidence_id="ev1")

        features = ranker._extract_features(item, [chunk_recent])
        assert features.hours_since_received < 2.0

    def test_feature_extraction_attachments(self):
        """Test attachment detection."""
        ranker = DigestRanker()

        chunk_with_attachments = EvidenceChunk(
            evidence_id="ev1",
            msg_id="msg1",
            text="Content",
            sender="sender@example.com",
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_metadata={"subject": "Test", "has_attachments": True},
        )

        item = _item(evidence_id="ev1")

        features = ranker._extract_features(item, [chunk_with_attachments])
        assert features.has_attachments is True

    def test_feature_extraction_project_tags(self):
        """Test project tag detection (JIRA, etc.)."""
        ranker = DigestRanker()

        # Test various project tag formats
        test_subjects = [
            ("[JIRA-123] Fix critical bug", True),
            ("[PROJ-456] New feature", True),
            ("[TASK-789] Update docs", True),
            ("[BUG-111] Memory leak", True),
            ("[#999] Quick fix", True),
            ("Regular email subject", False),
        ]

        for subject, expected in test_subjects:
            # Empty metadata so the tag must come from the live Item.source_subject
            # field (renamed from email_subject), not the message-metadata fallback.
            chunk = EvidenceChunk(
                evidence_id="ev1",
                msg_id="msg1",
                text="Content",
                sender="sender@example.com",
                timestamp=datetime.now(timezone.utc).isoformat(),
                message_metadata={},
            )

            item = Item(
                title="Action",
                evidence_id="ev1",
                confidence=0.9,
                source_ref={"type": "email", "msg_id": "msg1"},
                source_subject=subject,
            )

            features = ranker._extract_features(item, [chunk])
            assert features.has_project_tag == expected, f"Failed for subject: {subject}"

    def test_score_calculation(self):
        """Test score calculation from features."""
        weights = {
            "user_in_to": 0.2,
            "user_in_cc": 0.1,
            "has_action": 0.2,
            "has_mention": 0.1,
            "has_due_date": 0.1,
            "sender_importance": 0.1,
            "thread_length": 0.1,
            "recency": 0.1,
            "has_attachments": 0.05,
            "has_project_tag": 0.05,
        }
        ranker = DigestRanker(weights=weights)

        # All features enabled
        features = RankingFeatures(
            user_in_to=True,
            has_action=True,
            has_due_date=True,
            sender_importance=1.0,
            thread_length=5,
            hours_since_received=1.0,
            has_attachments=True,
            has_project_tag=True,
        )

        score = ranker._calculate_score(features)
        assert 0.0 <= score <= 1.0
        assert score > 0.5  # Should be high with many features

        # Minimal features
        features_min = RankingFeatures()
        score_min = ranker._calculate_score(features_min)
        assert 0.0 <= score_min <= 1.0
        assert score_min < score  # Should be lower than full features


class TestRankerIntegration:
    """Integration tests: actionable items should rank higher."""

    def test_rank_items_basic(self):
        """Test basic ranking of items."""
        ranker = DigestRanker(user_aliases=["user@example.com"])

        now = datetime.now(timezone.utc)

        # Create chunks
        chunks = [
            EvidenceChunk(
                evidence_id="ev1",
                msg_id="msg1",
                text="Please review this urgently",
                sender="ceo@example.com",
                timestamp=now.isoformat(),
                message_metadata={
                    "to_recipients": ["user@example.com"],
                    "cc_recipients": [],
                    "subject": "[JIRA-123] Critical review needed",
                },
            ),
            EvidenceChunk(
                evidence_id="ev2",
                msg_id="msg2",
                text="Just FYI, project status update",
                sender="other@example.com",
                timestamp=(now - timedelta(days=2)).isoformat(),
                message_metadata={
                    "to_recipients": ["team@example.com"],
                    "cc_recipients": ["user@example.com"],
                    "subject": "Status update",
                },
            ),
        ]

        # Create items
        items = [
            _item("FYI update", "ev2"),
            _item("Please review this urgently", "ev1", due="2024-12-31"),
        ]

        # Rank
        ranked = ranker.rank_items(items, chunks)

        # Check that urgent item is ranked higher
        assert ranked[0].title == "Please review this urgently"
        assert ranked[1].title == "FYI update"

        # Check scores
        assert ranked[0].rank_score > ranked[1].rank_score

    def test_rank_items_action_addressed_to_user_scores_high(self):
        """An actionable item addressed to the user, with a due date, ranks high."""
        ranker = DigestRanker(user_aliases=["user@example.com"])

        now = datetime.now(timezone.utc)

        chunk = EvidenceChunk(
            evidence_id="ev1",
            msg_id="msg1",
            text="User, can you send me the report by Friday?",
            sender="manager@example.com",
            timestamp=now.isoformat(),
            message_metadata={
                "to_recipients": ["user@example.com"],
                "subject": "Report needed",
            },
        )

        item = _item("Can you send me the report", "ev1", due="Friday")

        ranked = ranker.rank_items([item], [chunk])

        # Should have high score (action, direct mention, due date, direct recipient)
        assert ranked[0].rank_score > 0.5

    def test_top_n_actions_share(self):
        """Test calculation of top-N actions share."""
        ranker = DigestRanker()

        now = datetime.now(timezone.utc)

        # Create chunks
        chunks = [
            EvidenceChunk(
                evidence_id=f"ev{i}",
                msg_id=f"msg{i}",
                text="Content",
                sender="sender@example.com",
                timestamp=now.isoformat(),
                message_metadata={"subject": "Test"},
            )
            for i in range(10)
        ]

        # 7 actionable items (a due date is what get_top_n_actions_share counts on a
        # v1 Item), 3 FYI items with neither a due date nor an action marker.
        items = [_item("Please do the thing", f"ev{i}", due="2024-12-31") for i in range(7)]
        items += [_item("fyi", f"ev{i}") for i in range(7, 10)]

        # Rank and calculate share
        ranked = ranker.rank_items(items, chunks)
        share = ranker.get_top_n_actions_share(ranked, n=10)

        # Should be 0.7 (7 out of 10)
        assert 0.6 <= share <= 0.8

    def test_rank_with_custom_weights(self):
        """Test ranking with custom weights."""
        # Emphasize recency
        weights = {
            "user_in_to": 0.1,
            "user_in_cc": 0.05,
            "has_action": 0.1,
            "has_mention": 0.05,
            "has_due_date": 0.1,
            "sender_importance": 0.1,
            "thread_length": 0.05,
            "recency": 0.40,  # High weight on recency
            "has_attachments": 0.025,
            "has_project_tag": 0.025,
        }
        ranker = DigestRanker(weights=weights)

        now = datetime.now(timezone.utc)

        chunks = [
            EvidenceChunk(
                evidence_id="ev_old",
                msg_id="msg_old",
                text="Old message",
                sender="sender@example.com",
                timestamp=(now - timedelta(days=5)).isoformat(),
                message_metadata={"subject": "Old"},
            ),
            EvidenceChunk(
                evidence_id="ev_new",
                msg_id="msg_new",
                text="Recent message",
                sender="sender@example.com",
                timestamp=now.isoformat(),
                message_metadata={"subject": "New"},
            ),
        ]

        items = [
            _item("Old", "ev_old"),
            _item("New", "ev_new"),
        ]

        ranked = ranker.rank_items(items, chunks)

        # Recent item should be ranked higher due to high recency weight
        assert ranked[0].title == "New"
        assert ranked[1].title == "Old"

    def test_rank_empty_items(self):
        """Test ranking with empty items list."""
        ranker = DigestRanker()
        ranked = ranker.rank_items([], [])
        assert ranked == []

    def test_rank_items_no_matching_evidence(self):
        """Test ranking when evidence_id doesn't match any chunks."""
        ranker = DigestRanker()

        item = _item("Orphan", "nonexistent")

        chunk = EvidenceChunk(
            evidence_id="ev1",
            msg_id="msg1",
            text="Content",
            sender="sender@example.com",
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_metadata={"subject": "Test"},
        )

        ranked = ranker.rank_items([item], [chunk])

        # Should still work, but with default/low score
        assert len(ranked) == 1
        assert hasattr(ranked[0], "rank_score")
        assert ranked[0].rank_score is not None


class TestWeightValidation:
    """Test weight validation and normalization."""

    def test_weight_normalization(self):
        """Test that weights are normalized to sum to 1.0."""
        weights = {
            "user_in_to": 2.0,
            "user_in_cc": 1.0,
            "has_action": 2.0,
            "has_mention": 1.0,
            "has_due_date": 1.0,
            "sender_importance": 1.0,
            "thread_length": 1.0,
            "recency": 1.0,
            "has_attachments": 0.5,
            "has_project_tag": 0.5,
        }

        ranker = DigestRanker(weights=weights)

        # Weights should be normalized
        total = sum(ranker.weights.values())
        assert abs(total - 1.0) < 0.01  # Allow small floating point error

    def test_weight_out_of_range(self):
        """Test handling of weights outside [0, 1] range."""
        weights = {
            "user_in_to": -0.5,  # Negative
            "user_in_cc": 2.0,  # > 1.0
            "has_action": 0.5,
            "has_mention": 0.3,
            "has_due_date": 0.3,
            "sender_importance": 0.3,
            "thread_length": 0.2,
            "recency": 0.2,
            "has_attachments": 0.1,
            "has_project_tag": 0.1,
        }

        ranker = DigestRanker(weights=weights)

        # Should clamp to [0, 1] before normalization
        for weight in ranker.weights.values():
            assert 0.0 <= weight <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
