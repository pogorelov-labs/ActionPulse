"""
Tests for configurable LLM output cap / temperature and truncation handling.

Covers CORP_VALIDATION_FINDINGS_2026-06.md B-1:
- payload carries config-driven `temperature` / `max_tokens` (defaults 0.0 / 6000)
- `max_output_tokens` is clamped to the gateway ceiling (16384)
- `finish_reason=length` + unparseable JSON fails fast (no futile retry)
- `finish_reason=length` + parseable JSON proceeds with a meta marker
- quality retry is skipped (with a log) when the run token budget cannot fit it
"""

import json
import os
from unittest.mock import patch

import httpx
import pytest

from digest_core.config import GATEWAY_MAX_OUTPUT_TOKENS, LLMConfig
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.gateway import LLMGateway, LLMTruncationError

PROMPT = "Extract actions and return strict JSON."


def _evidence(priority_score: float = 2.0) -> list:
    return [
        EvidenceChunk(
            evidence_id="ev-cap-001",
            content="Please review the report by Friday.",
            source_ref={"type": "email", "msg_id": "msg-cap-001"},
            priority_score=priority_score,
        )
    ]


def _response_json(content: str, finish_reason: str = "stop", usage: dict | None = None) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _gateway_with_transport(config: LLMConfig, handler) -> LLMGateway:
    gateway = LLMGateway(config)
    gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
    return gateway


@pytest.fixture(autouse=True)
def _llm_token_env():
    with patch.dict(os.environ, {"LLM_TOKEN": "mock-token"}):
        yield


class TestLLMConfigParams:
    def test_defaults(self):
        config = LLMConfig()
        assert config.temperature == 0.0
        assert config.max_output_tokens == 6000

    def test_clamped_to_gateway_ceiling(self):
        config = LLMConfig(max_output_tokens=999_999)
        assert config.max_output_tokens == GATEWAY_MAX_OUTPUT_TOKENS

    def test_explicit_values_kept(self):
        config = LLMConfig(temperature=0.3, max_output_tokens=8000)
        assert config.temperature == 0.3
        assert config.max_output_tokens == 8000


class TestPayloadFromConfig:
    def test_payload_carries_config_values(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content.decode("utf-8")))
            body = json.dumps({"sections": []})
            return httpx.Response(200, json=_response_json(body))

        config = LLMConfig(endpoint="http://test/v1/chat/completions", max_output_tokens=7000)
        gateway = _gateway_with_transport(config, handler)
        gateway.extract_actions(_evidence(priority_score=0.5), PROMPT, "trace-payload")

        assert captured["temperature"] == 0.0
        assert captured["max_tokens"] == 7000


class TestTruncationHandling:
    def test_truncated_invalid_json_fails_without_retry(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            truncated = '{"sections": [{"title": "Мои действия", "items": [{"ti'
            return httpx.Response(200, json=_response_json(truncated, finish_reason="length"))

        config = LLMConfig(endpoint="http://test/v1/chat/completions")
        gateway = _gateway_with_transport(config, handler)

        with pytest.raises(LLMTruncationError, match="max_output_tokens"):
            gateway.extract_actions(_evidence(), PROMPT, "trace-trunc")

        # Deterministic truncation: exactly one network call, no JSON-hint retry.
        assert len(calls) == 1

    def test_truncated_but_parseable_json_proceeds_with_marker(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.dumps(
                {
                    "sections": [
                        {
                            "title": "Мои действия",
                            "items": [
                                {
                                    "title": "Review the report",
                                    "due": None,
                                    "evidence_id": "ev-cap-001",
                                    "confidence": 0.9,
                                    "source_ref": {"type": "email", "msg_id": "msg-cap-001"},
                                }
                            ],
                        }
                    ]
                }
            )
            return httpx.Response(200, json=_response_json(body, finish_reason="length"))

        config = LLMConfig(endpoint="http://test/v1/chat/completions")
        gateway = _gateway_with_transport(config, handler)
        result = gateway.extract_actions(_evidence(), PROMPT, "trace-trunc-ok")

        assert result["sections"], "parsed-but-truncated output must still be returned"
        assert result["_meta"]["finish_reason"] == "length"


class TestQualityRetryBudgetGuard:
    def _empty_sections_handler(self, calls, prompt_tokens: int):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            body = json.dumps({"sections": []})
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 100,
                "total_tokens": prompt_tokens + 100,
            }
            return httpx.Response(200, json=_response_json(body, usage=usage))

        return handler

    def test_retry_skipped_when_budget_too_tight(self):
        calls = []
        # Call 1 consumes 900 of 1000; estimated retry = 800 in + 6000 cap >> remaining 100.
        config = LLMConfig(endpoint="http://test/v1/chat/completions", max_tokens_per_run=1000)
        gateway = _gateway_with_transport(config, self._empty_sections_handler(calls, 800))

        result = gateway.extract_actions(_evidence(priority_score=2.0), PROMPT, "trace-budget")

        assert len(calls) == 1, "quality retry must be skipped, not attempted"
        assert result["sections"] == []

    def test_retry_fires_when_budget_allows(self):
        calls = []
        config = LLMConfig(endpoint="http://test/v1/chat/completions", max_tokens_per_run=30000)
        gateway = _gateway_with_transport(config, self._empty_sections_handler(calls, 800))

        gateway.extract_actions(_evidence(priority_score=2.0), PROMPT, "trace-budget-ok")

        assert len(calls) == 2, "quality retry should fire with ample budget"
