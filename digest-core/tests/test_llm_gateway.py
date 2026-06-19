"""
Test LLM gateway against the current retry and response contract.
"""

import json
from unittest.mock import Mock

import httpx
import pytest

from digest_core.config import LLMConfig
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.gateway import LLMGateway, RetryableLLMError, TokenBudgetExceeded
from digest_core.llm.rate_broker import RateBroker, StageCallBudgetExceeded


def _mock_response(
    content: str,
    *,
    status_code: int = 200,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    headers: dict | None = None,
) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.raise_for_status = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }
    return response


@pytest.fixture
def gateway(monkeypatch):
    """LLM gateway with a current LLMConfig fixture."""
    monkeypatch.setenv("LLM_TOKEN", "test-token")
    config = LLMConfig(
        endpoint="https://api.openai.com/v1/chat/completions",
        model="qwen35-397b-a17b",
        timeout_s=30,
    )
    return LLMGateway(config)


def test_transport_error_is_retried(gateway):
    """A transport-level error (read timeout / connection reset) is wrapped as retryable
    and retried — ADR-008's '1 internal retry for transient errors'. A bare httpx error
    would otherwise escape unretried (the loop only retries RetryableLLMError)."""
    valid = _mock_response('{"sections": [{"title": "T", "items": []}]}')
    gateway.client.post = Mock(side_effect=[httpx.ReadTimeout("timeout"), valid])

    result = gateway.extract_actions([], "Return strict JSON", "trace")

    assert result["sections"] == [{"title": "T", "items": []}]
    assert result["_meta"]["retry_count"] == 1
    assert gateway.client.post.call_count == 2


def test_invalid_json_retry(gateway):
    """Invalid JSON should trigger one retry and then return parsed sections."""
    invalid_response = _mock_response("{invalid json")
    valid_response = _mock_response('{"sections": [{"title": "Test", "items": []}]}')
    gateway.client.post = Mock(side_effect=[invalid_response, valid_response])

    result = gateway.extract_actions([], "Return strict JSON", "test-trace-id")

    assert result["sections"] == [{"title": "Test", "items": []}]
    assert result["_meta"]["retry_count"] == 1
    assert gateway.client.post.call_count == 2


def test_quality_retry_empty_sections(gateway):
    """Empty sections with positive evidence should trigger one quality retry."""
    empty_response = _mock_response('{"sections": []}')
    content_response = _mock_response('{"sections": [{"title": "Test", "items": []}]}')
    gateway.client.post = Mock(side_effect=[empty_response, content_response])

    evidence = [
        EvidenceChunk(evidence_id="ev-1", content="Important action item", priority_score=2.0)
    ]
    result = gateway.extract_actions(evidence, "Return strict JSON", "test-trace-id")

    assert result["sections"] == [{"title": "Test", "items": []}]
    assert gateway.client.post.call_count == 2


def test_token_usage_extraction(gateway):
    """Usage metadata should be exposed via the _meta envelope."""
    gateway.client.post = Mock(
        return_value=_mock_response('{"sections": [{"title": "Test", "items": []}]}')
    )

    result = gateway.extract_actions([], "Return strict JSON", "test-trace-id")

    assert result["_meta"]["tokens_in"] == 100
    assert result["_meta"]["tokens_out"] == 50
    assert result["_meta"]["http_status"] == 200


def test_network_error_propagation(gateway):
    """Unexpected transport errors should propagate to the caller."""
    gateway.client.post = Mock(side_effect=Exception("Network error"))

    with pytest.raises(Exception, match="Network error"):
        gateway.extract_actions([], "Return strict JSON", "test-trace-id")


def test_evidence_formatting(gateway):
    """Formatted request payload should include both system and user messages."""
    gateway.client.post = Mock(return_value=_mock_response('{"sections": []}'))
    evidence = [
        EvidenceChunk(
            evidence_id="ev-1",
            content="First evidence chunk",
            message_metadata={"from": "sender@example.com", "subject": "Subject"},
            source_ref={"msg_id": "msg-1"},
            msg_id="msg-1",
        ),
        EvidenceChunk(
            evidence_id="ev-2",
            content="Second evidence chunk",
            message_metadata={"from": "sender@example.com", "subject": "Subject"},
            source_ref={"msg_id": "msg-2"},
            msg_id="msg-2",
        ),
    ]

    gateway.extract_actions(evidence, "Return strict JSON", "test-trace-id")

    call_args = gateway.client.post.call_args
    messages = call_args.kwargs["json"]["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


class TestTokenBudgetEnforcement:
    """Verify max_tokens_per_run enforcement (COMMON-17 / TD-006)."""

    def test_budget_exceeded_raises(self, monkeypatch):
        """Gateway raises TokenBudgetExceeded when usage exceeds max_tokens_per_run."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
            max_tokens_per_run=100,  # very low budget
        )
        gw = LLMGateway(config)

        # Simulate a response whose token usage exceeds the budget
        resp = _mock_response(
            '{"sections":[]}',
            prompt_tokens=80,
            completion_tokens=30,  # 110 total > 100 limit
        )
        gw.client.post = Mock(return_value=resp)

        evidence = [
            EvidenceChunk(
                evidence_id="ev-1",
                content="test",
                message_metadata={"from": "a@b", "subject": "X"},
                source_ref={"msg_id": "m-1"},
                msg_id="m-1",
            ),
        ]

        with pytest.raises(TokenBudgetExceeded):
            gw.extract_actions(evidence, "Return strict JSON", "trace-budget")

    def test_budget_not_exceeded_passes(self, monkeypatch):
        """Gateway succeeds when usage stays within max_tokens_per_run."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
            max_tokens_per_run=500,
        )
        gw = LLMGateway(config)

        resp = _mock_response(
            '{"sections":[]}',
            prompt_tokens=100,
            completion_tokens=50,  # 150 total < 500 limit
        )
        gw.client.post = Mock(return_value=resp)

        evidence = [
            EvidenceChunk(
                evidence_id="ev-1",
                content="test",
                message_metadata={"from": "a@b", "subject": "X"},
                source_ref={"msg_id": "m-1"},
                msg_id="m-1",
            ),
        ]

        result = gw.extract_actions(evidence, "Return strict JSON", "trace-ok")
        assert "sections" in result
        assert gw._run_tokens_used == 150

    def test_cumulative_tracking(self, monkeypatch):
        """Token usage accumulates across multiple calls in a single gateway instance."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
            max_tokens_per_run=300,
        )
        gw = LLMGateway(config)

        # First call: 150 tokens (within budget)
        resp1 = _mock_response(
            '{"sections":[]}',
            prompt_tokens=100,
            completion_tokens=50,
        )
        gw.client.post = Mock(return_value=resp1)

        evidence = [
            EvidenceChunk(
                evidence_id="ev-1",
                content="test",
                message_metadata={"from": "a@b", "subject": "X"},
                source_ref={"msg_id": "m-1"},
                msg_id="m-1",
            ),
        ]

        gw.extract_actions(evidence, "Return strict JSON", "trace-1")
        assert gw._run_tokens_used == 150

        # Second call: another 200 tokens (cumulative 350 > 300)
        resp2 = _mock_response(
            '{"sections":[]}',
            prompt_tokens=150,
            completion_tokens=50,
        )
        gw.client.post = Mock(return_value=resp2)

        with pytest.raises(TokenBudgetExceeded):
            gw.extract_actions(evidence, "Return strict JSON", "trace-2")


class TestLLMReplayMode:
    """Verify --record-llm / --replay-llm (COMMON-34)."""

    @staticmethod
    def _make_evidence():
        return [
            EvidenceChunk(
                evidence_id="ev-1",
                content="test",
                message_metadata={"from": "a@b", "subject": "X"},
                source_ref={"msg_id": "m-1"},
                msg_id="m-1",
            ),
        ]

    def test_record_creates_file(self, monkeypatch, tmp_path):
        """--record-llm writes responses to a JSON file."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        record_file = tmp_path / "llm-recording.json"
        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
        gw = LLMGateway(config, record_llm=str(record_file))

        resp = _mock_response('{"sections":[]}', prompt_tokens=100, completion_tokens=50)
        gw.client.post = Mock(return_value=resp)

        gw.extract_actions(self._make_evidence(), "Return strict JSON", "trace-rec")

        assert record_file.exists()
        recording = json.loads(record_file.read_text())
        assert recording["meta"]["model"] == "qwen35-397b-a17b"
        assert len(recording["responses"]) == 1
        assert recording["responses"][0]["data"] == {"sections": []}

    def test_replay_returns_recorded_response(self, monkeypatch, tmp_path):
        """--replay-llm returns previously recorded LLM responses."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        replay_file = tmp_path / "llm-recording.json"
        recorded = {
            "meta": {"model": "qwen35-397b-a17b"},
            "responses": [
                {
                    "trace_id": "trace-orig",
                    "latency_ms": 42,
                    "data": {"sections": [{"title": "Мои действия", "items": []}]},
                    "meta": {
                        "tokens_in": 80,
                        "tokens_out": 20,
                        "http_status": 200,
                        "latency_ms": 42,
                        "validation_errors": 0,
                    },
                }
            ],
        }
        replay_file.write_text(json.dumps(recorded))

        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
        gw = LLMGateway(config, replay_llm=str(replay_file))

        # Should NOT make an HTTP call
        gw.client.post = Mock(side_effect=RuntimeError("should not be called"))
        result = gw.extract_actions(self._make_evidence(), "Return strict JSON", "trace-replay")
        assert "sections" in result
        gw.client.post.assert_not_called()

    def test_replay_exhausted_raises(self, monkeypatch, tmp_path):
        """Replay raises RuntimeError when all recorded responses are consumed."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        replay_file = tmp_path / "llm-recording.json"
        replay_file.write_text(json.dumps({"meta": {}, "responses": []}))

        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
        gw = LLMGateway(config, replay_llm=str(replay_file))

        with pytest.raises(RuntimeError, match="replay exhausted"):
            gw.extract_actions(self._make_evidence(), "Return strict JSON", "trace-empty")

    def test_record_stores_request_hash(self, monkeypatch, tmp_path):
        """Recorded entries carry a request_hash for request-keyed replay (PR1)."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        record_file = tmp_path / "llm-recording.json"
        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
        gw = LLMGateway(config, record_llm=str(record_file))
        gw.client.post = Mock(return_value=_mock_response('{"sections":[]}'))

        gw.extract_actions(self._make_evidence(), "Return strict JSON", "trace-rec")

        entry = json.loads(record_file.read_text())["responses"][0]
        assert len(entry["request_hash"]) == 16
        assert entry["data"] == {"sections": []}  # original payload still present

    def test_replay_matches_request_hash_over_position(self, monkeypatch, tmp_path):
        """Replay returns the entry matching the request, regardless of order."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
        msgs_a = [{"role": "user", "content": "AAA"}]
        msgs_b = [{"role": "user", "content": "BBB"}]
        recorded = {
            "meta": {},
            "responses": [
                {"request_hash": LLMGateway._request_hash(msgs_a), "data": {"k": "A"}, "meta": {}},
                {"request_hash": LLMGateway._request_hash(msgs_b), "data": {"k": "B"}, "meta": {}},
            ],
        }
        replay_file = tmp_path / "rec.json"
        replay_file.write_text(json.dumps(recorded))
        gw = LLMGateway(config, replay_llm=str(replay_file))

        # Request B first (stored at position 1) — must match by hash, not position.
        assert gw._replay_by_request(msgs_b, "t")["data"]["k"] == "B"
        assert gw._replay_by_request(msgs_a, "t")["data"]["k"] == "A"

    def test_replay_legacy_entries_fall_back_to_position(self, monkeypatch, tmp_path):
        """Legacy recordings without request_hash replay positionally (back-compat)."""
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        config = LLMConfig(
            endpoint="https://api.example.com/v1/chat",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
        recorded = {"meta": {}, "responses": [{"data": {"k": "legacy"}, "meta": {}}]}
        replay_file = tmp_path / "rec.json"
        replay_file.write_text(json.dumps(recorded))
        gw = LLMGateway(config, replay_llm=str(replay_file))

        result = gw._replay_by_request([{"role": "user", "content": "anything"}], "t")
        assert result["data"]["k"] == "legacy"


class TestGatewayRateBroker:
    """Gateway integrates with the shared RateBroker (PR2)."""

    @staticmethod
    def _evidence():
        return [
            EvidenceChunk(
                evidence_id="ev-1",
                content="test",
                message_metadata={"from": "a@b", "subject": "X"},
                source_ref={"msg_id": "m-1"},
                msg_id="m-1",
            ),
        ]

    @staticmethod
    def _config():
        return LLMConfig(
            endpoint="https://api.example.com/v1/chat", model="qwen35-397b-a17b", timeout_s=30
        )

    def test_acquire_called_on_network_attempt(self, monkeypatch):
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        broker = Mock()
        broker.acquire = Mock(return_value=0.0)
        broker.note_call = Mock(return_value=1)
        gw = LLMGateway(self._config(), rate_broker=broker)
        gw.client.post = Mock(return_value=_mock_response('{"sections":[]}'))

        gw.extract_actions(self._evidence(), "prompt", "t1")

        assert broker.acquire.call_count == 1  # one HTTP attempt -> one permit
        assert broker.note_call.call_count == 1  # one logical extractor call
        broker.note_call.assert_called_with("extractor")

    def test_acquire_not_called_in_replay(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        replay_file = tmp_path / "rec.json"
        replay_file.write_text(
            json.dumps({"meta": {}, "responses": [{"data": {"sections": []}, "meta": {}}]})
        )
        broker = Mock()
        broker.acquire = Mock()
        broker.note_call = Mock(return_value=1)
        gw = LLMGateway(self._config(), replay_llm=str(replay_file), rate_broker=broker)
        gw.client.post = Mock(side_effect=RuntimeError("no network in replay"))

        gw.extract_actions(self._evidence(), "prompt", "t2")

        broker.acquire.assert_not_called()  # replay path never reaches the network

    def test_429_penalizes_broker(self, monkeypatch):
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        broker = Mock()
        broker.acquire = Mock(return_value=0.0)
        gw = LLMGateway(self._config(), rate_broker=broker)

        err_response = Mock(status_code=429, headers={"Retry-After": "42"})
        response = Mock()
        response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError("429", request=Mock(), response=err_response)
        )
        gw.client.post = Mock(return_value=response)

        # _make_request_once raises RetryableLLMError after penalizing (no retry loop).
        with pytest.raises(RetryableLLMError):
            gw._make_request_once([{"role": "user", "content": "x"}], "t3")

        broker.penalize.assert_called_once_with("qwen35-397b-a17b", 42.0)

    def test_stage_budget_exceeded_propagates(self, monkeypatch):
        monkeypatch.setenv("LLM_TOKEN", "test-token")
        broker = RateBroker(fleet_rpm={}, stage_call_budgets={"extractor": 0})
        gw = LLMGateway(self._config(), rate_broker=broker)
        gw.client.post = Mock(return_value=_mock_response('{"sections":[]}'))

        with pytest.raises(StageCallBudgetExceeded):
            gw.extract_actions(self._evidence(), "prompt", "t4")


class _RecordingSink:
    """Duck-typed ProgressSink capturing retry/attempt events (U2)."""

    def __init__(self):
        self.events = []

    def on_stage_retry(self, stage, attempt, max_attempts, reason):
        self.events.append(("retry", stage, attempt, max_attempts, reason))

    def on_llm_attempt(self, model, attempt, max_attempts):
        self.events.append(("attempt", model, attempt, max_attempts))


def _sinked_gateway(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "test-token")
    sink = _RecordingSink()
    config = LLMConfig(
        endpoint="https://api.openai.com/v1/chat/completions",
        model="qwen35-397b-a17b",
        timeout_s=30,
    )
    return LLMGateway(config, sink=sink), sink


def test_transient_retry_emits_stage_retry_and_counts(monkeypatch):
    """U2: the internal transient retry surfaces as on_stage_retry('llm', …)."""
    gateway, sink = _sinked_gateway(monkeypatch)
    gateway.client.post = Mock(
        side_effect=[
            _mock_response("{invalid json"),
            _mock_response('{"sections": []}'),
        ]
    )

    gateway.extract_actions([], "Return strict JSON", "test-trace-id")

    retries = [e for e in sink.events if e[0] == "retry"]
    assert len(retries) == 1
    _, stage, attempt, max_attempts, _reason = retries[0]
    assert (stage, attempt, max_attempts) == ("llm", 2, 2)
    assert gateway.get_request_stats()["run_retries"] == 1


def test_quality_retry_emits_attempt_2_of_2(monkeypatch):
    """U2: the quality retry (second logical call) shows as attempt 2/2."""
    gateway, sink = _sinked_gateway(monkeypatch)
    gateway.client.post = Mock(
        side_effect=[
            _mock_response('{"sections": []}'),
            _mock_response('{"sections": [{"title": "Test", "items": []}]}'),
        ]
    )
    evidence = [
        EvidenceChunk(evidence_id="ev-1", content="Important action item", priority_score=2.0)
    ]

    gateway.extract_actions(evidence, "Return strict JSON", "test-trace-id")

    assert ("attempt", "qwen35-397b-a17b", 2, 2) in sink.events
    assert gateway.get_request_stats()["run_retries"] == 1


def test_network_call_emits_lane_updates(monkeypatch):
    """§4.3: the gateway reports its lane (in-flight + RPM) around real calls."""
    monkeypatch.setenv("LLM_TOKEN", "test-token")

    class LaneSink:
        def __init__(self):
            self.events = []

        def on_lane_update(self, lane, state):
            self.events.append((lane, dict(state)))

    sink = LaneSink()
    config = LLMConfig(
        endpoint="https://api.openai.com/v1/chat/completions",
        model="qwen35-397b-a17b",
        timeout_s=30,
    )
    gateway = LLMGateway(config, sink=sink, rate_broker=RateBroker({"qwen35-397b-a17b": 15.0}))
    gateway.client.post = Mock(
        return_value=_mock_response('{"sections": [{"title": "Test", "items": []}]}')
    )
    gateway.extract_actions([], "Return strict JSON", "trace")

    assert [state["in_flight"] for _, state in sink.events] == [1, 0]
    _, first = sink.events[0]
    assert first["model"] == "qwen35-397b-a17b"
    assert first["stage"] == "extractor"
    assert first["rpm_cap"] == 15 and first["rpm_used"] == 1
