"""A1 slice 3: the gateway can constrain generation to a JSON schema.

``build_json_schema_response_format`` turns any pydantic model into an OpenAI/vLLM
``response_format``, and ``_make_request_*`` thread it into the request payload. The
default stays ``{"type": "json_object"}``, so the live extract path is byte-identical
until A1.4 flips it on. This proves the mechanism (which removes the malformed/
off-schema class of failure the quality-retry exists to recover from) without
touching the live pipeline.
"""

import json
from unittest.mock import Mock

import pytest

from digest_core.config import LLMConfig
from digest_core.llm.gateway import LLMGateway, build_json_schema_response_format
from digest_core.llm.schemas import EnhancedDigestV3


def _mock_response(content: str, *, prompt_tokens: int = 100, completion_tokens: int = 50) -> Mock:
    r = Mock()
    r.status_code = 200
    r.headers = {}
    r.raise_for_status = Mock()
    r.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }
    return r


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "test-token")
    return LLMGateway(
        LLMConfig(
            endpoint="https://example.invalid/v1/chat/completions",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
    )


def test_helper_builds_json_schema_block_from_pydantic_model():
    rf = build_json_schema_response_format(EnhancedDigestV3, name="digest_v3")
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "digest_v3"
    assert rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    # v3's typed sections survive into the constrained schema...
    assert "my_actions" in schema["properties"]
    # ...and so does the P2 traceability backbone grafted in A1.1 (evidence_spans).
    blob = json.dumps(schema)
    assert "ActionItemV3" in blob
    assert "evidence_spans" in blob


def test_default_request_uses_json_object(gateway):
    """The live path is unchanged: no response_format threaded -> json_object mode."""
    gateway.client.post = Mock(return_value=_mock_response('{"sections": []}'))
    gateway.extract_actions([], "Return strict JSON", "trace")
    payload = gateway.client.post.call_args.kwargs["json"]
    assert payload["response_format"] == {"type": "json_object"}


def test_threaded_response_format_reaches_the_payload(gateway):
    """A schema response_format threaded into the request path lands in the payload."""
    gateway.client.post = Mock(return_value=_mock_response('{"sections": []}'))
    rf = build_json_schema_response_format(EnhancedDigestV3)
    gateway._make_request_with_retry(
        [{"role": "user", "content": "x"}], "trace", response_format=rf
    )
    payload = gateway.client.post.call_args.kwargs["json"]
    assert payload["response_format"]["type"] == "json_schema"
    assert "my_actions" in payload["response_format"]["json_schema"]["schema"]["properties"]
