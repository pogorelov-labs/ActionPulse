"""
Tests for enhanced markdown assembler with v2 and v3 schemas.
"""

from pathlib import Path
import tempfile
from digest_core.llm.schemas import (
    EnhancedDigest,
    ActionItem,
    DeadlineMeeting,
    EnhancedDigestV3,
    ActionItemV3,
    RiskBlockerV3,
)
from digest_core.assemble.markdown import MarkdownAssembler


class TestEnhancedMarkdownAssembler:
    """Test markdown assembler with EnhancedDigest v2."""

    def test_write_enhanced_digest_with_actions(self):
        """Test writing enhanced digest with actions to markdown."""
        # Create test digest
        action1 = ActionItem(
            title="Review PR",
            description="Review pull request #123",
            evidence_id="ev_1",
            quote="Please review pull request #123 by end of day.",
            due_date="2024-12-15",
            due_date_normalized="2024-12-15T17:00:00-03:00",
            due_date_label="tomorrow",
            actors=["user"],
            confidence="High",
            response_channel="slack",
        )

        digest = EnhancedDigest(
            prompt_version="v2",
            digest_date="2024-12-14",
            trace_id="test_123",
            timezone="America/Sao_Paulo",
            my_actions=[action1],
            others_actions=[],
            deadlines_meetings=[],
            risks_blockers=[],
            fyi=[],
        )

        # Write to temp file
        assembler = MarkdownAssembler()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = Path(f.name)

        try:
            assembler.write_enhanced_digest(digest, output_path)

            # Read and verify
            content = output_path.read_text(encoding="utf-8")

            assert "# Action digest - 2024-12-14" in content
            assert "Trace ID: test_123" in content
            assert "Schema version: 2.0" in content
            assert "## My actions" in content
            assert "Review PR" in content
            assert "Evidence ev_1" in content
            assert "Please review pull request" in content
            assert "(tomorrow)" in content

        finally:
            if output_path.exists():
                output_path.unlink()

    def test_write_enhanced_digest_with_deadlines(self):
        """Test writing enhanced digest with deadlines."""
        deadline1 = DeadlineMeeting(
            title="Team standup",
            evidence_id="ev_2",
            quote="Daily standup scheduled for 10 AM tomorrow.",
            date_time="2024-12-15T10:00:00-03:00",
            date_label="tomorrow",
            location="Room 201",
            participants=["Alice", "Bob", "Charlie"],
        )

        digest = EnhancedDigest(
            prompt_version="v2",
            digest_date="2024-12-14",
            trace_id="test_456",
            deadlines_meetings=[deadline1],
            my_actions=[],
            others_actions=[],
            risks_blockers=[],
            fyi=[],
        )

        assembler = MarkdownAssembler()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = Path(f.name)

        try:
            assembler.write_enhanced_digest(digest, output_path)

            content = output_path.read_text(encoding="utf-8")

            assert "## Deadlines and meetings" in content
            assert "Team standup" in content
            assert "Room 201" in content
            assert "Alice, Bob, Charlie" in content

        finally:
            if output_path.exists():
                output_path.unlink()

    def test_write_empty_enhanced_digest(self):
        """Test writing empty enhanced digest."""
        digest = EnhancedDigest(
            prompt_version="v2",
            digest_date="2024-12-14",
            trace_id="test_789",
            my_actions=[],
            others_actions=[],
            deadlines_meetings=[],
            risks_blockers=[],
            fyi=[],
            markdown_summary="## Краткое резюме\n\nДействий не найдено.",
        )

        assembler = MarkdownAssembler()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = Path(f.name)

        try:
            assembler.write_enhanced_digest(digest, output_path)

            content = output_path.read_text(encoding="utf-8")

            assert "No relevant actions found for the period" in content
            assert "Краткое резюме" in content

        finally:
            if output_path.exists():
                output_path.unlink()

    def test_enhanced_digest_contains_quotes(self):
        """Test that all items in enhanced digest contain quotes."""
        action1 = ActionItem(
            title="Update docs",
            description="Update documentation",
            evidence_id="ev_10",
            quote="Please update the documentation to reflect recent changes.",
            confidence="Medium",
        )

        digest = EnhancedDigest(
            prompt_version="v2",
            digest_date="2024-12-14",
            trace_id="test_quotes",
            my_actions=[action1],
        )

        assembler = MarkdownAssembler()
        content = assembler._generate_enhanced_markdown(digest)

        # Check that quote is present
        assert "**Quote:**" in content
        assert "Please update the documentation" in content


class TestEnhancedMarkdownV3:
    """Test markdown assembler with EnhancedDigestV3 (plain names, no masking)."""

    def test_write_v3_digest_with_plain_owners(self):
        """Test V3 digest renders owners as plain strings."""
        action1 = ActionItemV3(
            title="Review budget",
            description="Review Q4 budget proposal",
            evidence_id="ev_v3_1",
            quote="Please review the Q4 budget proposal by Friday.",
            due_date="2024-12-20",
            owners=["John Smith", "Jane Doe"],
            confidence="High",
        )

        digest = EnhancedDigestV3(
            digest_date="2024-12-14", trace_id="test_v3_123", my_actions=[action1]
        )

        assembler = MarkdownAssembler()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = Path(f.name)

        try:
            assembler.write_enhanced_digest(digest, output_path)

            content = output_path.read_text(encoding="utf-8")

            # Verify plain name rendering (no [[REDACT:*]] patterns)
            assert "John Smith, Jane Doe" in content
            assert "**Owners:** John Smith, Jane Doe" in content
            assert "[[REDACT" not in content
            assert "Schema version: 3.0" in content

        finally:
            if output_path.exists():
                output_path.unlink()

    def test_write_v3_digest_with_risk_owners(self):
        """Test V3 digest renders risk owners as plain strings."""
        risk1 = RiskBlockerV3(
            title="Budget overrun risk",
            evidence_id="ev_v3_2",
            quote="Project is 20% over budget due to unexpected costs.",
            severity="High",
            impact="Project may be delayed by 2 weeks",
            owners=["Project Manager", "Finance Team"],
        )

        digest = EnhancedDigestV3(
            digest_date="2024-12-14", trace_id="test_v3_456", risks_blockers=[risk1]
        )

        assembler = MarkdownAssembler()
        content = assembler._generate_enhanced_markdown(digest)

        # Verify plain name rendering
        assert "Project Manager, Finance Team" in content
        assert "**Owners:** Project Manager, Finance Team" in content
        assert "[[REDACT" not in content

    def test_v3_backward_compatible_with_v2(self):
        """Test that V2 digest still works (actors field)."""
        action_v2 = ActionItem(
            title="V2 action",
            description="Testing V2 compatibility",
            evidence_id="ev_v2_1",
            quote="This is a V2 action item.",
            actors=["Alice", "Bob"],
            confidence="Medium",
        )

        digest_v2 = EnhancedDigest(
            prompt_version="v2",
            digest_date="2024-12-14",
            trace_id="test_v2_compat",
            my_actions=[action_v2],
        )

        assembler = MarkdownAssembler()
        content = assembler._generate_enhanced_markdown(digest_v2)

        # V2 uses actors, should still render as Ответственные
        assert "Alice, Bob" in content
        assert "**Owners:** Alice, Bob" in content
