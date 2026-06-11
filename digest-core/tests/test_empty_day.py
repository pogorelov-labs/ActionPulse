"""
Test for empty day validation (DoD criterion H).
"""

import pytest
import json
import tempfile
from pathlib import Path

from digest_core.llm.schemas import Digest, Section, Item
from digest_core.assemble.jsonout import JSONAssembler
from digest_core.assemble.markdown import MarkdownAssembler


class TestEmptyDayValidation:
    """Test empty day handling according to DoD criterion H."""

    def test_empty_digest_json_structure(self):
        """Test that empty digest generates valid JSON with sections=[]."""
        # Create empty digest
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[],
        )

        # Validate structure
        assert digest_data.schema_version == "1.0"
        assert digest_data.prompt_version == "extract_actions.v1"
        assert digest_data.digest_date == "2024-01-15"
        assert digest_data.trace_id == "test-empty-trace"
        assert len(digest_data.sections) == 0

        # Test JSON serialization
        json_data = digest_data.model_dump()
        assert json_data["sections"] == []

        # Test JSON validation
        validated_digest = Digest(**json_data)
        assert len(validated_digest.sections) == 0

    def test_empty_digest_json_output(self):
        """Test JSON assembler with empty digest."""
        assembler = JSONAssembler()

        # Create empty digest
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[],
        )

        # Write to temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)

        try:
            # Write digest
            assembler.write_digest(digest_data, temp_path)

            # Verify file exists and is valid JSON
            assert temp_path.exists()

            with open(temp_path, "r", encoding="utf-8") as f:
                json_content = json.load(f)

            # Validate structure
            assert json_content["schema_version"] == "1.0"
            assert json_content["prompt_version"] == "extract_actions.v1"
            assert json_content["digest_date"] == "2024-01-15"
            assert json_content["trace_id"] == "test-empty-trace"
            assert json_content["sections"] == []

            # Validate it can be loaded back
            validated_digest = Digest(**json_content)
            assert len(validated_digest.sections) == 0

        finally:
            # Cleanup
            if temp_path.exists():
                temp_path.unlink()

    def test_empty_digest_markdown_output(self):
        """Test Markdown assembler with empty digest."""
        assembler = MarkdownAssembler()

        # Create empty digest
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[],
        )

        # Write to temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            temp_path = Path(f.name)

        try:
            # Write digest
            assembler.write_digest(digest_data, temp_path)

            # Verify file exists
            assert temp_path.exists()

            with open(temp_path, "r", encoding="utf-8") as f:
                markdown_content = f.read()

            # Validate content
            assert "# Action digest - 2024-01-15" in markdown_content
            assert "*Trace ID: test-empty-trace*" in markdown_content
            assert "No relevant actions found for the period" in markdown_content

            # Validate word count is reasonable
            word_count = assembler.get_word_count(markdown_content)
            assert word_count <= 400  # Should be well under limit

        finally:
            # Cleanup
            if temp_path.exists():
                temp_path.unlink()

    def test_empty_digest_not_error(self):
        """Test that empty digest is not considered an error."""
        # Create empty digest
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[],
        )

        # This should not raise any exceptions
        json_data = digest_data.model_dump()
        markdown_content = MarkdownAssembler()._generate_markdown(digest_data)

        # Validate both outputs are valid
        assert json_data["sections"] == []
        assert "No relevant actions found for the period" in markdown_content

    def test_empty_digest_with_non_actionable_content(self):
        """Test empty digest when only non-actionable content is present."""
        # Create digest with non-actionable sections (should be filtered out)
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[
                Section(
                    title="Информационные сообщения",
                    items=[
                        Item(
                            title="Newsletter",
                            due=None,
                            evidence_id="ev-newsletter-001",
                            confidence=0.1,
                            source_ref={
                                "type": "email",
                                "msg_id": "msg-newsletter-001",
                            },
                        )
                    ],
                )
            ],
        )

        # Even with items, if they're all low confidence/non-actionable,
        # the system should handle it gracefully
        json_data = digest_data.model_dump()
        assert len(json_data["sections"]) == 1
        assert len(json_data["sections"][0]["items"]) == 1

        # The item should have low confidence (indicating non-actionable)
        item = json_data["sections"][0]["items"][0]
        assert item["confidence"] == 0.1
        assert item["title"] == "Newsletter"

    def test_empty_digest_idempotency(self):
        """Test that empty digest generation is idempotent."""
        # Create empty digest
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[],
        )

        # Generate outputs multiple times
        json_outputs = []
        markdown_outputs = []

        for i in range(3):
            json_data = digest_data.model_dump()
            markdown_content = MarkdownAssembler()._generate_markdown(digest_data)

            json_outputs.append(json_data)
            markdown_outputs.append(markdown_content)

        # All outputs should be identical
        assert all(output == json_outputs[0] for output in json_outputs)
        assert all(output == markdown_outputs[0] for output in markdown_outputs)

    def test_empty_digest_validation(self):
        """Test validation of empty digest structure."""
        # Valid empty digest
        valid_empty = {
            "schema_version": "1.0",
            "prompt_version": "extract_actions.v1",
            "digest_date": "2024-01-15",
            "trace_id": "test-empty-trace",
            "sections": [],
        }

        # Should validate successfully
        digest = Digest(**valid_empty)
        assert len(digest.sections) == 0

        # Test with minimal required fields
        minimal_empty = {
            "prompt_version": "extract_actions.v1",
            "digest_date": "2024-01-15",
            "trace_id": "test-empty-trace",
            "sections": [],
        }

        # Should validate successfully (schema_version has default)
        digest = Digest(**minimal_empty)
        assert len(digest.sections) == 0
        assert digest.schema_version == "1.0"  # Default value

    def test_empty_digest_edge_cases(self):
        """Test edge cases for empty digest."""
        # Test with empty sections list (valid case)
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[],
        )

        # Should handle empty list gracefully
        json_data = digest_data.model_dump()
        assert json_data["sections"] == []

        # Test with empty string date
        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="",
            trace_id="test-empty-trace",
            sections=[],
        )

        json_data = digest_data.model_dump()
        assert json_data["digest_date"] == ""
        assert json_data["sections"] == []

    def test_empty_digest_performance(self):
        """Test that empty digest generation is fast."""
        import time

        digest_data = Digest(
            schema_version="1.0",
            prompt_version="extract_actions.v1",
            digest_date="2024-01-15",
            trace_id="test-empty-trace",
            sections=[],
        )

        # Time the generation
        start_time = time.time()

        json_data = digest_data.model_dump()
        markdown_content = MarkdownAssembler()._generate_markdown(digest_data)

        end_time = time.time()
        generation_time = end_time - start_time

        # Should be very fast (less than 0.1 seconds)
        assert generation_time < 0.1

        # Validate outputs
        assert json_data["sections"] == []
        assert "No relevant actions found for the period" in markdown_content


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
