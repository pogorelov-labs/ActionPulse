"""
Test markdown and JSON assembly with schema validation.
"""

import pytest
from pathlib import Path
from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.assemble.jsonout import JSONAssembler
from digest_core.llm.schemas import Digest, Section, Item


class MarkdownOutputWriter(MarkdownAssembler):
    """Compatibility wrapper returning rendered content."""

    def write_digest(self, digest_data, output_path):
        super().write_digest(digest_data, Path(output_path))
        return Path(output_path).read_text(encoding="utf-8")


class JSONOutputWriter(JSONAssembler):
    """Compatibility wrapper returning rendered content."""

    def write_digest(self, digest_data, output_path):
        super().write_digest(digest_data, Path(output_path))
        return Path(output_path).read_text(encoding="utf-8")


@pytest.fixture
def sample_digest():
    """Sample digest for testing."""
    items = [
        Item(
            title="Check Q4 Report",
            due=None,
            confidence=0.9,
            evidence_id="ev-001",
            source_ref={
                "type": "email",
                "msg_id": "msg-001",
                "thread_id": "thread-001",
            },
        ),
        Item(
            title="Schedule Team Meeting",
            due=None,
            confidence=0.8,
            evidence_id="ev-002",
            source_ref={
                "type": "email",
                "msg_id": "msg-002",
                "thread_id": "thread-002",
            },
        ),
    ]

    sections = [Section(title="My Actions", items=items)]

    return Digest(
        digest_date="2024-01-15",
        trace_id="test-trace-id",
        sections=sections,
        schema_version="1.0",
        prompt_version="v1",
    )


@pytest.fixture
def empty_digest():
    """Empty digest for testing."""
    return Digest(
        digest_date="2024-01-15",
        trace_id="test-trace-id",
        sections=[],
        schema_version="1.0",
        prompt_version="v1",
    )


@pytest.fixture
def markdown_writer():
    """Markdown output writer."""
    return MarkdownOutputWriter()


@pytest.fixture
def json_writer():
    """JSON output writer."""
    return JSONOutputWriter()


def test_markdown_word_limit(markdown_writer, sample_digest):
    """Test that markdown output is within 400 words."""
    output = markdown_writer.write_digest(sample_digest, "/tmp/test.md")

    # Count words in the output
    word_count = len(output.split())
    assert word_count <= 400


def test_markdown_source_citations(markdown_writer, sample_digest):
    """Test that each line cites evidence and source."""
    output = markdown_writer.write_digest(sample_digest, "/tmp/test.md")

    # Check for source citations
    assert "Source:" in output
    assert "evidence ev-001" in output
    assert "evidence ev-002" in output


def test_markdown_empty_day(markdown_writer, empty_digest):
    """Test markdown output for empty day."""
    output = markdown_writer.write_digest(empty_digest, "/tmp/test.md")

    assert "No relevant actions found for the period" in output


def test_json_schema_validation(json_writer, sample_digest):
    """Test that JSON output matches Digest schema."""
    output = json_writer.write_digest(sample_digest, "/tmp/test.json")

    # Parse JSON to validate structure
    import json

    parsed = json.loads(output)

    # Check required fields
    assert "digest_date" in parsed
    assert "trace_id" in parsed
    assert "sections" in parsed
    assert "schema_version" in parsed
    assert "prompt_version" in parsed

    # Check schema version
    assert parsed["schema_version"] == "1.0"
    assert parsed["prompt_version"] == "v1"


def test_json_empty_day(json_writer, empty_digest):
    """Test JSON output for empty day."""
    output = json_writer.write_digest(empty_digest, "/tmp/test.json")

    import json

    parsed = json.loads(output)

    assert parsed["sections"] == []
    assert parsed["schema_version"] == "1.0"
    assert parsed["prompt_version"] == "v1"


def test_json_item_structure(json_writer, sample_digest):
    """Test that items have correct structure."""
    output = json_writer.write_digest(sample_digest, "/tmp/test.json")

    import json

    parsed = json.loads(output)

    # Check first item structure
    first_item = parsed["sections"][0]["items"][0]
    assert "title" in first_item
    assert "confidence" in first_item
    assert "evidence_id" in first_item
    assert "source_ref" in first_item

    # Check source_ref structure
    source_ref = first_item["source_ref"]
    assert "msg_id" in source_ref
    assert "thread_id" in source_ref


def test_markdown_file_creation(markdown_writer, sample_digest, tmp_path):
    """Test that markdown file is created."""
    output_file = tmp_path / "test.md"
    markdown_writer.write_digest(sample_digest, str(output_file))

    assert output_file.exists()
    assert output_file.read_text().startswith("# Action digest")


def test_json_file_creation(json_writer, sample_digest, tmp_path):
    """Test that JSON file is created."""
    output_file = tmp_path / "test.json"
    json_writer.write_digest(sample_digest, str(output_file))

    assert output_file.exists()

    # Validate JSON content
    import json

    content = json.loads(output_file.read_text())
    assert content["digest_date"] == "2024-01-15"


def test_markdown_multiple_sections(markdown_writer):
    """Test markdown output with multiple sections."""
    items1 = [
        Item(
            title="Action 1",
            due=None,
            confidence=0.9,
            evidence_id="ev-001",
            source_ref={
                "type": "email",
                "msg_id": "msg-001",
                "thread_id": "thread-001",
            },
        )
    ]

    items2 = [
        Item(
            title="Action 2",
            due=None,
            confidence=0.8,
            evidence_id="ev-002",
            source_ref={
                "type": "email",
                "msg_id": "msg-002",
                "thread_id": "thread-002",
            },
        )
    ]

    sections = [
        Section(title="Section 1", items=items1),
        Section(title="Section 2", items=items2),
    ]

    digest = Digest(
        digest_date="2024-01-15",
        trace_id="test-trace-id",
        sections=sections,
        schema_version="1.0",
        prompt_version="v1",
    )

    output = markdown_writer.write_digest(digest, "/tmp/test.md")

    assert "## Section 1" in output
    assert "## Section 2" in output
    assert "Action 1" in output
    assert "Action 2" in output


def test_json_multiple_sections(json_writer):
    """Test JSON output with multiple sections."""
    items1 = [
        Item(
            title="Action 1",
            due=None,
            confidence=0.9,
            evidence_id="ev-001",
            source_ref={
                "type": "email",
                "msg_id": "msg-001",
                "thread_id": "thread-001",
            },
        )
    ]

    items2 = [
        Item(
            title="Action 2",
            due=None,
            confidence=0.8,
            evidence_id="ev-002",
            source_ref={
                "type": "email",
                "msg_id": "msg-002",
                "thread_id": "thread-002",
            },
        )
    ]

    sections = [
        Section(title="Section 1", items=items1),
        Section(title="Section 2", items=items2),
    ]

    digest = Digest(
        digest_date="2024-01-15",
        trace_id="test-trace-id",
        sections=sections,
        schema_version="1.0",
        prompt_version="v1",
    )

    output = json_writer.write_digest(digest, "/tmp/test.json")

    import json

    parsed = json.loads(output)

    assert len(parsed["sections"]) == 2
    assert parsed["sections"][0]["title"] == "Section 1"
    assert parsed["sections"][1]["title"] == "Section 2"
