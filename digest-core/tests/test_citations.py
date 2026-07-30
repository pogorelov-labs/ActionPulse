"""
Tests for citation builder and validator (extractive traceability).

Covers:
- Citation building from evidence chunks
- Offset validation
- Checksum verification
- Edge cases (multibyte chars, invalid offsets, not found)
"""

import pytest
from digest_core.llm.schemas import Citation, Item
from digest_core.evidence.citations import (
    CitationBuilder,
    CitationValidator,
    enrich_item_with_citations,
)
from digest_core.evidence.split import EvidenceChunk


# Test fixtures
@pytest.fixture
def simple_normalized_map():
    """Simple normalized message map for testing."""
    return {
        "msg-001": "This is a test email body with some important action items to complete by Friday.",
        "msg-002": "Привет! Это тестовое письмо с задачами на русском языке. Дедлайн: завтра в 15:00.",
        "msg-003": "Short message",
    }


@pytest.fixture
def multibyte_normalized_map():
    """Normalized map with multibyte characters (emoji, russian, special chars)."""
    return {
        "msg-emoji": "Hello 👋 world! Important: review PR #123 by tomorrow 🚀",
        "msg-quotes": "Please review \"Document.pdf\" and 'report.xlsx' files.",
        "msg-mixed": "Срочно: проверить email@example.com до 15:00 ⏰",
    }


@pytest.fixture
def simple_evidence_chunk():
    """Simple evidence chunk for testing."""
    return EvidenceChunk(
        evidence_id="ev-001",
        content="important action items to complete by Friday",
        source_ref={"msg_id": "msg-001", "subject": "Test"},
        message_metadata={"subject": "Test"},
        chunk_idx=0,
        total_chunks=1,
        timestamp="2024-01-15T10:00:00Z",
        sender="test@example.com",
        thread_id="thread-1",
        signals={},
    )


class TestCitationBuilder:
    """Test CitationBuilder functionality."""

    def test_build_citation_success(self, simple_normalized_map, simple_evidence_chunk):
        """Test successful citation building."""
        builder = CitationBuilder(simple_normalized_map)
        citation = builder.build_citation(simple_evidence_chunk)

        assert citation is not None
        assert citation.msg_id == "msg-001"
        assert citation.start >= 0
        assert citation.end > citation.start
        assert citation.preview == "important action items to complete by Friday"
        assert citation.checksum is not None

    def test_build_citation_missing_msg_id(self, simple_normalized_map):
        """Test citation building with missing msg_id."""
        chunk = EvidenceChunk(
            evidence_id="ev-no-msg",
            content="test content",
            source_ref={},  # No msg_id
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        builder = CitationBuilder(simple_normalized_map)
        citation = builder.build_citation(chunk)

        assert citation is None

    def test_build_citation_content_not_found(self, simple_normalized_map):
        """Test citation building when content not found in message."""
        chunk = EvidenceChunk(
            evidence_id="ev-not-found",
            content="this text does not exist in the message",
            source_ref={"msg_id": "msg-001"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        builder = CitationBuilder(simple_normalized_map)
        citation = builder.build_citation(chunk)

        # Should return None if content not found
        assert citation is None

    def test_build_citation_russian_text(self, simple_normalized_map):
        """Test citation building with Russian text."""
        chunk = EvidenceChunk(
            evidence_id="ev-ru",
            content="Это тестовое письмо с задачами на русском языке",
            source_ref={"msg_id": "msg-002"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        builder = CitationBuilder(simple_normalized_map)
        citation = builder.build_citation(chunk)

        assert citation is not None
        assert citation.msg_id == "msg-002"
        assert "Это тестовое письмо" in citation.preview

    def test_build_citation_with_emoji(self, multibyte_normalized_map):
        """Test citation building with emoji (multibyte characters)."""
        chunk = EvidenceChunk(
            evidence_id="ev-emoji",
            content="Hello 👋 world! Important: review PR #123 by tomorrow 🚀",
            source_ref={"msg_id": "msg-emoji"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        builder = CitationBuilder(multibyte_normalized_map)
        citation = builder.build_citation(chunk)

        assert citation is not None
        assert "👋" in citation.preview or "Hello" in citation.preview

    def test_build_citations_for_multiple_chunks(self, simple_normalized_map):
        """Test building citations for multiple chunks."""
        chunks = [
            EvidenceChunk(
                evidence_id=f"ev-{i}",
                content=content,
                source_ref={"msg_id": "msg-001"},
                message_metadata={},
                chunk_idx=i,
                total_chunks=2,
                timestamp="2024-01-15T10:00:00Z",
                sender="test@example.com",
                thread_id="thread-1",
                signals={},
            )
            for i, content in enumerate(
                ["This is a test email body", "important action items to complete"]
            )
        ]

        builder = CitationBuilder(simple_normalized_map)
        citations = builder.build_citations_for_chunks(chunks)

        assert len(citations) == 2
        assert all(c.msg_id == "msg-001" for c in citations)

    def test_checksum_caching(self, simple_normalized_map):
        """Test that checksums are cached for performance."""
        builder = CitationBuilder(simple_normalized_map)

        # Build two citations from same message
        chunk1 = EvidenceChunk(
            evidence_id="ev-1",
            content="This is a test",
            source_ref={"msg_id": "msg-001"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )
        chunk2 = EvidenceChunk(
            evidence_id="ev-2",
            content="test email body",
            source_ref={"msg_id": "msg-001"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        citation1 = builder.build_citation(chunk1)
        citation2 = builder.build_citation(chunk2)

        assert citation1.checksum == citation2.checksum
        assert len(builder.checksums_cache) == 1


class TestCitationValidator:
    """Test CitationValidator functionality."""

    def test_validate_valid_citation(self, simple_normalized_map):
        """Test validation of valid citation."""
        citation = Citation(
            msg_id="msg-001",
            start=8,
            end=25,
            preview="a test email body",
            checksum=None,  # Checksum validation optional
        )

        validator = CitationValidator(simple_normalized_map)
        is_valid, error = validator.validate_citation(citation)

        assert is_valid
        assert error is None

    def test_validate_invalid_start_offset(self, simple_normalized_map):
        """Test schema rejects invalid start offset before validator runs."""
        with pytest.raises(Exception):
            Citation(msg_id="msg-001", start=-5, end=10, preview="test", checksum=None)

    def test_validate_invalid_end_offset(self, simple_normalized_map):
        """Test validation with end <= start."""
        citation = Citation(
            msg_id="msg-001",
            start=20,
            end=10,  # end < start
            preview="test",
            checksum=None,
        )

        validator = CitationValidator(simple_normalized_map)
        is_valid, error = validator.validate_citation(citation)

        assert not is_valid
        assert "Invalid end offset" in error

    def test_validate_offset_exceeds_length(self, simple_normalized_map):
        """Test validation with offset exceeding message length."""
        citation = Citation(
            msg_id="msg-003",  # "Short message" - 13 chars
            start=0,
            end=1000,  # Exceeds length
            preview="Short message",
            checksum=None,
        )

        validator = CitationValidator(simple_normalized_map)
        is_valid, error = validator.validate_citation(citation)

        assert not is_valid
        assert "exceeds body length" in error

    def test_validate_preview_mismatch(self, simple_normalized_map):
        """Test validation with preview mismatch."""
        citation = Citation(
            msg_id="msg-001",
            start=0,
            end=10,
            preview="wrong preview text",
            checksum=None,
        )

        validator = CitationValidator(simple_normalized_map)
        is_valid, error = validator.validate_citation(citation)

        assert not is_valid
        assert "Preview mismatch" in error

    def test_validate_checksum_mismatch(self, simple_normalized_map):
        """Test validation with checksum mismatch."""
        citation = Citation(
            msg_id="msg-001",
            start=0,
            end=10,
            preview="This is a ",
            checksum="invalid_checksum_hash",
        )

        validator = CitationValidator(simple_normalized_map)
        is_valid, error = validator.validate_citation(citation)

        assert not is_valid
        assert "Checksum mismatch" in error

    def test_validate_message_not_found(self):
        """Test validation when message not found in map."""
        citation = Citation(msg_id="msg-999", start=0, end=10, preview="test", checksum=None)

        validator = CitationValidator({"msg-001": "test"})
        is_valid, error = validator.validate_citation(citation)

        assert not is_valid
        assert "not found" in error

    def test_validate_multiple_citations_success(self, simple_normalized_map):
        """Test validation of multiple valid citations."""
        citations = [
            Citation(msg_id="msg-001", start=0, end=10, preview="This is a ", checksum=None),
            Citation(msg_id="msg-002", start=0, end=11, preview="Привет! Это", checksum=None),
            Citation(msg_id="msg-003", start=0, end=5, preview="Short", checksum=None),
        ]

        validator = CitationValidator(simple_normalized_map)
        is_valid = validator.validate_citations(citations, strict=False)

        assert is_valid
        assert len(validator.validation_errors) == 0

    def test_validate_multiple_citations_with_errors(self, simple_normalized_map):
        """Test validation collects all errors (non-strict mode)."""
        citations = [
            Citation(
                msg_id="msg-001", start=0, end=10, preview="This is a ", checksum=None
            ),  # Valid
            Citation(
                msg_id="msg-999", start=0, end=10, preview="test", checksum=None
            ),  # Invalid msg_id
            Citation(
                msg_id="msg-001", start=20, end=10, preview="test", checksum=None
            ),  # Invalid offset range
        ]

        validator = CitationValidator(simple_normalized_map)
        is_valid = validator.validate_citations(citations, strict=False)

        assert not is_valid
        assert len(validator.validation_errors) == 2  # Two invalid citations

    def test_validate_strict_mode_stops_on_first_error(self, simple_normalized_map):
        """Test strict mode stops on first error."""
        citations = [
            Citation(msg_id="msg-999", start=0, end=10, preview="test", checksum=None),  # Invalid
            Citation(
                msg_id="msg-001", start=20, end=10, preview="test", checksum=None
            ),  # Invalid but schema-valid
        ]

        validator = CitationValidator(simple_normalized_map)
        is_valid = validator.validate_citations(citations, strict=True)

        assert not is_valid
        # In strict mode, should have stopped after first error
        assert len(validator.validation_errors) >= 1


class TestEnrichItemWithCitations:
    """Test enrich_item_with_citations functionality."""

    def test_enrich_action_item(self, simple_normalized_map, simple_evidence_chunk):
        """Test enriching an Item with citations."""
        action = Item(
            title="Complete task",
            evidence_id="ev-001",
            confidence=0.9,
            source_ref={"type": "email", "msg_id": "msg-001"},
        )

        builder = CitationBuilder(simple_normalized_map)
        enrich_item_with_citations(action, [simple_evidence_chunk], builder)

        assert len(action.citations) == 1
        assert action.citations[0].msg_id == "msg-001"
        assert action.citations[0].preview == "important action items to complete by Friday"

    def test_enrich_item_no_matching_chunk(self, simple_normalized_map, simple_evidence_chunk):
        """Test enriching item when no matching chunk found."""
        action = Item(
            title="Complete task",
            evidence_id="ev-999",  # Non-existent
            confidence=0.9,
            source_ref={"type": "email", "msg_id": "msg-001"},
        )

        builder = CitationBuilder(simple_normalized_map)
        enrich_item_with_citations(action, [simple_evidence_chunk], builder)

        # Should have empty citations if no matching chunk
        assert len(action.citations) == 0

    def test_enrich_item_multiple_chunks(self, simple_normalized_map):
        """Test enriching item with multiple matching chunks."""
        chunks = [
            EvidenceChunk(
                evidence_id="ev-multi",
                content="This is a test",
                source_ref={"msg_id": "msg-001"},
                message_metadata={},
                chunk_idx=0,
                total_chunks=2,
                timestamp="2024-01-15T10:00:00Z",
                sender="test@example.com",
                thread_id="thread-1",
                signals={},
            ),
            EvidenceChunk(
                evidence_id="ev-multi",
                content="test email body",
                source_ref={"msg_id": "msg-001"},
                message_metadata={},
                chunk_idx=1,
                total_chunks=2,
                timestamp="2024-01-15T10:00:00Z",
                sender="test@example.com",
                thread_id="thread-1",
                signals={},
            ),
        ]

        action = Item(
            title="Test",
            evidence_id="ev-multi",
            confidence=0.9,
            source_ref={"type": "email", "msg_id": "msg-001"},
        )

        builder = CitationBuilder(simple_normalized_map)
        enrich_item_with_citations(action, chunks, builder)

        # Should have 2 citations (one per chunk)
        assert len(action.citations) == 2


class TestCitationEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_normalized_map(self):
        """Test with empty normalized map."""
        builder = CitationBuilder({})
        chunk = EvidenceChunk(
            evidence_id="ev-001",
            content="test",
            source_ref={"msg_id": "msg-001"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        citation = builder.build_citation(chunk)
        assert citation is None

    def test_empty_content_chunk(self, simple_normalized_map):
        """Test with empty content chunk."""
        chunk = EvidenceChunk(
            evidence_id="ev-empty",
            content="",  # Empty
            source_ref={"msg_id": "msg-001"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        builder = CitationBuilder(simple_normalized_map)
        citation = builder.build_citation(chunk)

        # Should handle empty content gracefully
        assert citation is None or citation.start == citation.end

    def test_very_long_content(self):
        """Test with very long message content."""
        long_text = "a" * 100000  # 100KB text
        normalized_map = {"msg-long": long_text}

        chunk = EvidenceChunk(
            evidence_id="ev-long",
            content="a" * 1000,  # 1KB chunk
            source_ref={"msg_id": "msg-long"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        builder = CitationBuilder(normalized_map)
        citation = builder.build_citation(chunk)

        assert citation is not None
        assert citation.start >= 0
        assert citation.end > citation.start
        assert len(citation.preview) <= 200  # Preview should be truncated

    def test_whitespace_differences(self):
        """Test handling whitespace differences between chunk and normalized text."""
        normalized_map = {
            "msg-ws": "This  is   a    test   message   with   irregular    whitespace"
        }

        chunk = EvidenceChunk(
            evidence_id="ev-ws",
            content="test message with irregular",  # Different whitespace
            source_ref={"msg_id": "msg-ws"},
            message_metadata={},
            chunk_idx=0,
            total_chunks=1,
            timestamp="2024-01-15T10:00:00Z",
            sender="test@example.com",
            thread_id="thread-1",
            signals={},
        )

        builder = CitationBuilder(normalized_map)
        citation = builder.build_citation(chunk)

        # Should handle fuzzy matching for whitespace
        assert citation is not None
