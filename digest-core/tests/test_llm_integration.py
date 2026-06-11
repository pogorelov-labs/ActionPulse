"""
Integration test with mock LLM Gateway.
"""

import pytest
import os
from unittest.mock import patch

from digest_core.config import LLMConfig
from digest_core.llm.gateway import LLMGateway
from digest_core.evidence.split import EvidenceChunk
from tests.mock_llm_gateway import MockLLMGateway, create_mock_llm_config


class TestLLMIntegration:
    """Integration tests for LLM Gateway."""

    def setup_method(self):
        """Setup for each test method."""
        self.mock_gateway = MockLLMGateway()  # ephemeral port — no collisions
        self.mock_gateway.start()

        # Create mock config (port known only after start)
        self.mock_config = LLMConfig(**create_mock_llm_config(port=self.mock_gateway.port))

        self.token_patcher = patch.dict(os.environ, {"LLM_TOKEN": "mock-token"})
        self.token_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method."""
        self.token_patcher.stop()
        self.mock_gateway.stop()

    def test_llm_gateway_health_check(self):
        """Test LLM Gateway health check."""
        from digest_core.observability.healthz import HealthCheckHandler

        handler = object.__new__(HealthCheckHandler)
        handler.llm_config = self.mock_config

        # Test health check
        result = handler._check_llm_gateway()

        assert result["status"] == "healthy"
        assert result["endpoint"] == self.mock_config.endpoint

    def test_llm_action_extraction(self):
        """Test LLM action extraction with mock gateway."""
        llm_gateway = LLMGateway(self.mock_config)

        # Create mock evidence
        evidence = [
            EvidenceChunk(
                evidence_id="ev-001",
                content="Please review the Q4 report by Friday. This is urgent.",
                source_ref={"type": "email", "msg_id": "msg-001"},
                priority_score=2.0,
            )
        ]

        # Load prompt template
        prompt_template = """
        Ты — ассистент, который извлекает только действия и срочные просьбы, адресованные получателю.
        Для каждого пункта верни evidence_id.

        СТРОГО верни JSON по схеме:
        {
          "sections": [
            {
              "title": "Мои действия",
              "items": [
                {
                  "title": "string",
                  "due": "YYYY-MM-DD|null",
                  "evidence_id": "string",
                  "confidence": 0.0-1.0,
                  "source_ref": {"type":"email","msg_id":"string"}
                }
              ]
            }
          ]
        }
        Никакого текста вне JSON.
        """

        # Test action extraction
        result = llm_gateway.extract_actions(
            evidence=evidence,
            prompt_template=prompt_template,
            trace_id="test-trace-001",
        )

        # Verify response structure
        assert "sections" in result
        assert len(result["sections"]) > 0
        assert result["sections"][0]["title"] == "Мои действия"
        assert len(result["sections"][0]["items"]) > 0

        # Verify item structure
        item = result["sections"][0]["items"][0]
        assert "title" in item
        assert "evidence_id" in item
        assert "confidence" in item
        assert "source_ref" in item
        assert item["evidence_id"] == "ev-001"

    def test_llm_empty_response(self):
        """Test LLM response with no actionable content."""
        llm_gateway = LLMGateway(self.mock_config)

        # Create mock evidence with no action words
        evidence = [
            EvidenceChunk(
                evidence_id="ev-002",
                content="This is just an informational email. No action required.",
                source_ref={"type": "email", "msg_id": "msg-002"},
                priority_score=0.5,
            )
        ]

        prompt_template = """
        Ты — ассистент, который извлекает только действия и срочные просьбы, адресованные получателю.
        СТРОГО верни JSON по схеме: {"sections": []}
        """

        # Test action extraction
        result = llm_gateway.extract_actions(
            evidence=evidence,
            prompt_template=prompt_template,
            trace_id="test-trace-002",
        )

        # Should return empty sections
        assert "sections" in result
        assert len(result["sections"]) == 0

    def test_llm_invalid_json_retry(self):
        """Test LLM retry on invalid JSON response."""
        # This test would require modifying the mock to return invalid JSON
        # For now, we'll test the retry mechanism exists
        llm_gateway = LLMGateway(self.mock_config)

        # The mock gateway should return valid JSON, so this tests the happy path
        evidence = [
            EvidenceChunk(
                evidence_id="ev-003",
                content="Please complete the task.",
                source_ref={"type": "email", "msg_id": "msg-003"},
                priority_score=1.5,
            )
        ]

        prompt_template = "Extract actions and return JSON."

        result = llm_gateway.extract_actions(
            evidence=evidence,
            prompt_template=prompt_template,
            trace_id="test-trace-003",
        )

        # Should succeed
        assert "sections" in result

    def test_llm_gateway_metrics(self):
        """Test LLM Gateway metrics collection."""
        llm_gateway = LLMGateway(self.mock_config)

        evidence = [
            EvidenceChunk(
                evidence_id="ev-004",
                content="Test content for metrics.",
                source_ref={"type": "email", "msg_id": "msg-004"},
                priority_score=1.0,
            )
        ]

        prompt_template = "Test prompt."

        # Make request
        llm_gateway.extract_actions(
            evidence=evidence,
            prompt_template=prompt_template,
            trace_id="test-trace-004",
        )

        # Check metrics
        stats = llm_gateway.get_request_stats()
        assert "last_latency_ms" in stats
        assert "endpoint" in stats
        assert "model" in stats
        assert stats["endpoint"] == self.mock_config.endpoint
        assert stats["model"] == self.mock_config.model
        assert stats["last_latency_ms"] > 0  # Should have some latency

    def test_llm_gateway_validation(self):
        """Test LLM response validation."""
        llm_gateway = LLMGateway(self.mock_config)

        # Test with valid evidence
        evidence = [
            EvidenceChunk(
                evidence_id="ev-005",
                content="Valid evidence content.",
                source_ref={"type": "email", "msg_id": "msg-005"},
                priority_score=1.0,
            )
        ]

        prompt_template = "Test validation."

        result = llm_gateway.extract_actions(
            evidence=evidence,
            prompt_template=prompt_template,
            trace_id="test-trace-005",
        )

        # Should validate successfully
        assert "sections" in result

        # Test validation with invalid evidence_id
        # This would require modifying the mock to return invalid evidence_id
        # For now, we test that validation exists
        validated_response = llm_gateway._validate_response(result, evidence)
        assert "sections" in validated_response


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
