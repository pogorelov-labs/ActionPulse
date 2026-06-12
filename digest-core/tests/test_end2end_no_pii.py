"""
End-to-End tests for pipeline without PII handling.

Tests verify that:
1. Valid LLM JSON produces correct output
2. Invalid LLM JSON triggers fallback but still produces files
3. LLM output containing phone/email is processed normally (no sanitization)
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from digest_core.llm.gateway import LLMGateway
from digest_core.llm.schemas import EnhancedDigest, EnhancedDigestV3
from digest_core.evidence.split import EvidenceChunk
from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.config import LLMConfig


class TestEndToEndNoPII:
    """End-to-end tests verifying pipeline works without PII handling."""

    def setup_method(self):
        """Setup for each test."""
        self.llm_config = LLMConfig(
            endpoint="http://mock-llm/api/v1/chat",
            model="mock-model",
            timeout_s=30,
            strict_json=True,
        )

    def test_valid_llm_json_produces_output(self):
        """Test: Valid LLM JSON → OK, files produced."""
        # Create mock evidence
        evidence = [
            EvidenceChunk(
                evidence_id="ev_001",
                conversation_id="conv_001",
                content="Please review the budget by Friday. Contact John Smith at john.smith@company.com or +1-555-0123.",
                source_ref={"type": "email", "msg_id": "msg_001"},
                token_count=30,
                priority_score=2.0,
                message_metadata={
                    "from": "sender@example.com",
                    "subject": "Budget Review",
                },
                addressed_to_me=True,
                user_aliases_matched=["user"],
                signals={"action_verbs": ["review"], "dates": ["Friday"]},
            )
        ]

        # Mock valid LLM response with contact info
        valid_response = {
            "schema_version": "3.0",
            "prompt_version": "mvp.5",
            "digest_date": "2024-12-14",
            "trace_id": "test_valid_001",
            "timezone": "America/Sao_Paulo",
            "my_actions": [
                {
                    "title": "Review budget",
                    "description": "Review the budget proposal",
                    "evidence_id": "ev_001",
                    "quote": "Please review the budget by Friday.",
                    "owners": ["John Smith"],
                    "confidence": "High",
                }
            ],
            "others_actions": [],
            "deadlines_meetings": [],
            "risks_blockers": [],
            "fyi": [],
        }

        # Mock LLM Gateway
        with patch.object(LLMGateway, "_make_request_with_retry") as mock_request:
            mock_request.return_value = {
                "trace_id": "test_valid_001",
                "latency_ms": 100,
                "data": valid_response,
                "meta": {"tokens_in": 50, "tokens_out": 100},
            }

            gateway = LLMGateway(self.llm_config, metrics=None)

            # Process digest
            result = gateway.process_digest(
                evidence=evidence,
                digest_date="2024-12-14",
                trace_id="test_valid_001",
                prompt_version="mvp.5",
            )

            # Verify result
            assert result["trace_id"] == "test_valid_001"
            assert "digest" in result
            assert isinstance(result["digest"], EnhancedDigestV3)

            digest = result["digest"]
            assert len(digest.my_actions) == 1
            assert digest.my_actions[0].title == "Review budget"
            assert digest.my_actions[0].owners == ["John Smith"]

            # Write to files
            with tempfile.TemporaryDirectory() as tmpdir:
                json_path = Path(tmpdir) / "digest.json"
                md_path = Path(tmpdir) / "digest.md"

                # Write JSON (V3 uses same assembler for now)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(digest.model_dump(), f, indent=2, ensure_ascii=False)

                # Write Markdown
                assembler = MarkdownAssembler()
                assembler.write_enhanced_digest(digest, md_path)

                # Verify files exist
                assert json_path.exists()
                assert md_path.exists()

                # Verify content - names are NOT masked
                md_content = md_path.read_text(encoding="utf-8")
                assert "John Smith" in md_content
                assert "[[REDACT" not in md_content

                # JSON should also have plain names
                json_content = json_path.read_text(encoding="utf-8")
                assert "John Smith" in json_content
                assert "[[REDACT" not in json_content

    def test_invalid_llm_json_triggers_fallback(self):
        """Test: Invalid LLM JSON → fallback partial, but files produced."""
        # Create evidence
        evidence = [
            EvidenceChunk(
                evidence_id="ev_002",
                conversation_id="conv_002",
                content="Action required: Update documentation by EOD.",
                source_ref={"type": "email", "msg_id": "msg_002"},
                token_count=25,
                priority_score=1.8,
                message_metadata={
                    "from": "manager@example.com",
                    "subject": "Documentation",
                },
                addressed_to_me=True,
                user_aliases_matched=["user"],
                signals={"action_verbs": ["update"], "dates": ["EOD"]},
            )
        ]

        # Mock invalid JSON response (missing required field)
        # invalid JSON: '{"schema_version": "3.0", "incomplete": true'

        with patch.object(LLMGateway, "_make_request_with_retry") as mock_request:
            # First attempt returns invalid JSON
            mock_request.side_effect = ValueError("Invalid JSON from LLM: Expecting value")

            gateway = LLMGateway(self.llm_config, enable_degrade=True, metrics=None)

            # Process digest - should use fallback
            result = gateway.process_digest(
                evidence=evidence,
                digest_date="2024-12-14",
                trace_id="test_invalid_001",
                prompt_version="mvp.5",
            )

            # Verify fallback was used
            assert "partial" in result
            assert result["partial"] is True
            assert "reason" in result

            digest = result["digest"]
            # Fallback uses V2 schema, which is expected
            assert isinstance(digest, EnhancedDigest)

            # Fallback should still produce content
            assert len(digest.my_actions) > 0 or len(digest.others_actions) > 0

            # Write to files - should work even with partial digest
            with tempfile.TemporaryDirectory() as tmpdir:
                json_path = Path(tmpdir) / "digest-partial.json"
                md_path = Path(tmpdir) / "digest-partial.md"

                # Write JSON
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(digest.model_dump(), f, indent=2, ensure_ascii=False)

                # Write Markdown with partial flag
                assembler = MarkdownAssembler()
                assembler.write_enhanced_digest(
                    digest,
                    md_path,
                    is_partial=True,
                    partial_reason=result.get("reason"),
                )

                # Verify files exist despite fallback
                assert json_path.exists()
                assert md_path.exists()

                # Verify partial marker in markdown
                md_content = md_path.read_text(encoding="utf-8")
                assert "PARTIAL REPORT" in md_content or "fallback mode" in md_content

    def test_llm_returns_phone_email_no_sanitization(self):
        """Test: LLM returns phone/email in text → pipeline OK (no sanitizer)."""
        # Evidence with contact info
        evidence = [
            EvidenceChunk(
                evidence_id="ev_003",
                conversation_id="conv_003",
                content="Contact Jane Doe at jane.doe@example.com or call +1-555-9876 for details.",
                source_ref={"type": "email", "msg_id": "msg_003"},
                token_count=35,
                priority_score=1.5,
                message_metadata={
                    "from": "support@example.com",
                    "subject": "Contact Info",
                },
                addressed_to_me=False,
                user_aliases_matched=[],
                signals={"action_verbs": ["contact", "call"], "dates": []},
            )
        ]

        # LLM response includes phone/email in various fields
        response_with_contact_info = {
            "schema_version": "3.0",
            "prompt_version": "mvp.5",
            "digest_date": "2024-12-14",
            "trace_id": "test_contact_001",
            "timezone": "America/Sao_Paulo",
            "my_actions": [
                {
                    "title": "Contact Jane Doe",
                    "description": "Call Jane Doe at +1-555-9876 or email jane.doe@example.com",
                    "evidence_id": "ev_003",
                    "quote": "Contact Jane Doe at jane.doe@example.com or call +1-555-9876 for details.",
                    "owners": ["Jane Doe <jane.doe@example.com>"],
                    "confidence": "High",
                }
            ],
            "others_actions": [],
            "deadlines_meetings": [],
            "risks_blockers": [],
            "fyi": [],
        }

        with patch.object(LLMGateway, "_make_request_with_retry") as mock_request:
            mock_request.return_value = {
                "trace_id": "test_contact_001",
                "latency_ms": 120,
                "data": response_with_contact_info,
                "meta": {"tokens_in": 60, "tokens_out": 150},
            }

            gateway = LLMGateway(self.llm_config, metrics=None)

            # Process digest - should NOT sanitize phone/email
            result = gateway.process_digest(
                evidence=evidence,
                digest_date="2024-12-14",
                trace_id="test_contact_001",
                prompt_version="mvp.5",
            )

            digest = result["digest"]

            # Verify contact info is preserved (NOT sanitized)
            action = digest.my_actions[0]
            assert "jane.doe@example.com" in action.description
            assert "+1-555-9876" in action.description
            assert "jane.doe@example.com" in action.quote
            assert "+1-555-9876" in action.quote

            # Owners can include email
            assert "jane.doe@example.com" in action.owners[0]

            # Write to markdown
            with tempfile.TemporaryDirectory() as tmpdir:
                md_path = Path(tmpdir) / "digest-contact.md"

                assembler = MarkdownAssembler()
                assembler.write_enhanced_digest(digest, md_path)

                md_content = md_path.read_text(encoding="utf-8")

                # Verify contact info is in output (NOT masked)
                assert "jane.doe@example.com" in md_content
                assert "+1-555-9876" in md_content
                assert "Jane Doe" in md_content

                # Verify NO masking patterns
                assert "[[REDACT:EMAIL]]" not in md_content
                assert "[[REDACT:PHONE]]" not in md_content
                assert "[[REDACT:NAME]]" not in md_content
                assert "[[REDACT" not in md_content

    def test_multiple_owners_with_contact_info(self):
        """Test: Multiple owners with various contact formats are preserved."""
        evidence = [
            EvidenceChunk(
                evidence_id="ev_004",
                conversation_id="conv_004",
                content="Team meeting scheduled. Participants: alice@company.com, bob@company.com, +1-555-1111",
                source_ref={"type": "email", "msg_id": "msg_004"},
                token_count=40,
                priority_score=1.0,
                message_metadata={
                    "from": "team@example.com",
                    "subject": "Team Meeting",
                },
                addressed_to_me=True,
                user_aliases_matched=["user"],
                signals={"action_verbs": ["scheduled"], "dates": []},
            )
        ]

        response = {
            "schema_version": "3.0",
            "prompt_version": "mvp.5",
            "digest_date": "2024-12-14",
            "trace_id": "test_multi_001",
            "timezone": "America/Sao_Paulo",
            "my_actions": [],
            "others_actions": [],
            "deadlines_meetings": [
                {
                    "title": "Team meeting",
                    "evidence_id": "ev_004",
                    "quote": "Team meeting scheduled.",
                    "date_time": "2024-12-15T10:00:00-03:00",
                    "participants": [
                        "Alice <alice@company.com>",
                        "Bob <bob@company.com>",
                        "Charlie (+1-555-1111)",
                    ],
                }
            ],
            "risks_blockers": [],
            "fyi": [],
        }

        with patch.object(LLMGateway, "_make_request_with_retry") as mock_request:
            mock_request.return_value = {
                "trace_id": "test_multi_001",
                "latency_ms": 90,
                "data": response,
                "meta": {"tokens_in": 40, "tokens_out": 80},
            }

            gateway = LLMGateway(self.llm_config, metrics=None)
            result = gateway.process_digest(
                evidence=evidence,
                digest_date="2024-12-14",
                trace_id="test_multi_001",
                prompt_version="mvp.5",
            )

            digest = result["digest"]
            meeting = digest.deadlines_meetings[0]

            # Verify all participants with contact info
            assert len(meeting.participants) == 3
            assert any("alice@company.com" in p for p in meeting.participants)
            assert any("bob@company.com" in p for p in meeting.participants)
            assert any("+1-555-1111" in p for p in meeting.participants)

            # Write and verify markdown
            with tempfile.TemporaryDirectory() as tmpdir:
                md_path = Path(tmpdir) / "digest-meeting.md"
                assembler = MarkdownAssembler()
                assembler.write_enhanced_digest(digest, md_path)

                md_content = md_path.read_text(encoding="utf-8")

                # All contact info should be visible
                assert "alice@company.com" in md_content
                assert "bob@company.com" in md_content
                assert "+1-555-1111" in md_content
                assert "[[REDACT" not in md_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
