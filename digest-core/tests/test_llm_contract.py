from digest_core.llm.schemas import Digest
import json
import pathlib


def test_contract_example():
    """Test that example digest conforms to schema."""
    p = pathlib.Path("examples/digest-2024-01-15.json")
    if not p.exists():
        return

    data = json.loads(p.read_text())
    digest = Digest(**data)

    # Verify required fields
    assert digest.schema_version == "1.0"
    # English example (en is the default report language → en.v2 prompt).
    assert digest.prompt_version == "extract_actions.en.v2"
    assert digest.digest_date == "2024-01-15"
    assert digest.trace_id == "example-trace-id"
    assert len(digest.sections) > 0


def test_schema_version_required():
    """Test that schema_version is required."""
    data = {
        "prompt_version": "extract_actions.v1",
        "digest_date": "2024-01-15",
        "trace_id": "test",
        "sections": [],
    }

    # Should use default schema_version
    digest = Digest(**data)
    assert digest.schema_version == "1.0"


def test_item_validation():
    """Test that items are properly validated."""
    data = {
        "schema_version": "1.0",
        "prompt_version": "extract_actions.v1",
        "digest_date": "2024-01-15",
        "trace_id": "test",
        "sections": [
            {
                "title": "Test Section",
                "items": [
                    {
                        "title": "Test Item",
                        "evidence_id": "ev-001",
                        "confidence": 0.85,
                        "source_ref": {"type": "email", "msg_id": "msg-001"},
                    }
                ],
            }
        ],
    }

    digest = Digest(**data)
    assert len(digest.sections) == 1
    assert len(digest.sections[0].items) == 1
    assert digest.sections[0].items[0].confidence == 0.85
