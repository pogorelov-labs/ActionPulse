"""Fleet clients: endpoint derivation + record/replay (zero network in replay) (PR2).

The fleet clients are not wired into the live pipeline yet; these tests pin their
offline contract so PR8/PR9/PR12 can consume them safely.
"""

import json
from unittest.mock import Mock

import httpx
import pytest

from digest_core.config import LLMConfig
from digest_core.llm.fleet import (
    EmbeddingsClient,
    RerankerClient,
    TokenizerClient,
    derive_fleet_endpoint,
)
from digest_core.llm.rate_broker import RateBroker


def _config():
    return LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b", timeout_s=30)


def _resp(payload, *, status_code=200):
    response = Mock()
    response.status_code = status_code
    response.raise_for_status = Mock()
    response.json.return_value = payload
    return response


def test_derive_fleet_endpoint():
    assert (
        derive_fleet_endpoint("https://gw.corp/api/v1/chat", "embeddings")
        == "https://gw.corp/api/v1/embeddings"
    )
    assert (
        derive_fleet_endpoint("https://gw.corp/v1/chat/completions", "score")
        == "https://gw.corp/v1/score"
    )
    # Leading slash = absolute under the gateway host (LiteLLM mounts /rerank at root).
    assert (
        derive_fleet_endpoint("https://gw.corp/v1/chat/completions", "/rerank")
        == "https://gw.corp/rerank"
    )
    assert derive_fleet_endpoint("https://gw.corp/chat", "/rerank") == "https://gw.corp/rerank"
    assert derive_fleet_endpoint("", "tokenize") == ""


def test_embeddings_posts_to_derived_endpoint_and_parses(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    client = EmbeddingsClient(_config())
    client._client = Mock()
    client._client.post = Mock(
        return_value=_resp({"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3]}]})
    )

    assert client.embed(["a", "b"]) == [[0.1, 0.2], [0.3]]
    url = client._client.post.call_args[0][0]
    assert url.endswith("/v1/embeddings")


def test_reranker_reorders_results_by_index(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    client = RerankerClient(_config())
    client._client = Mock()
    client._client.post = Mock(
        return_value=_resp({"results": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}]})
    )
    assert client.score("q", ["d0", "d1"]) == [0.2, 0.9]
    # Default path is the probe-verified /rerank at the gateway host root (D4).
    assert client._client.post.call_args[0][0] == "https://gw.corp/api/rerank"


def test_reranker_endpoint_path_is_configurable(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    client = RerankerClient(_config(), endpoint_path="/v1/score")
    client._client = Mock()
    client._client.post = Mock(return_value=_resp({"scores": [0.5]}))
    assert client.score("q", ["d0"]) == [0.5]
    assert client._client.post.call_args[0][0] == "https://gw.corp/api/v1/score"


def test_stage_call_budget_enforced_via_broker(monkeypatch):
    from digest_core.llm.rate_broker import StageCallBudgetExceeded

    monkeypatch.setenv("LLM_TOKEN", "t")
    broker = RateBroker(
        fleet_rpm={"bge-reranker-v2-m3": 10},
        stage_call_budgets={"reranker": 2},
        sleep=lambda d: None,
    )
    client = RerankerClient(_config(), rate_broker=broker, stage="reranker")
    client._client = Mock()
    client._client.post = Mock(return_value=_resp({"scores": [0.5]}))

    assert client.score("q", ["d"]) == [0.5]
    assert client.score("q2", ["d"]) == [0.5]
    with pytest.raises(StageCallBudgetExceeded):
        client.score("q3", ["d"])
    assert broker.calls_made("reranker") == 3  # the rejected attempt is counted


def test_empty_inputs_skip_network(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    emb = EmbeddingsClient(_config())
    emb._client = Mock()
    emb._client.post = Mock(side_effect=AssertionError("no network for empty input"))
    assert emb.embed([]) == []

    rer = RerankerClient(_config())
    rer._client = Mock()
    rer._client.post = Mock(side_effect=AssertionError("no network for empty input"))
    assert rer.score("q", []) == []


def test_record_then_replay_makes_no_network_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_TOKEN", "t")
    recording = tmp_path / "fleet-rec.json"

    recorder = EmbeddingsClient(_config(), record=str(recording))
    recorder._client = Mock()
    recorder._client.post = Mock(return_value=_resp({"data": [{"embedding": [1.0, 2.0]}]}))
    assert recorder.embed(["hello"]) == [[1.0, 2.0]]

    saved = json.loads(recording.read_text())
    assert saved["endpoints"]["embeddings"][0]["request_hash"]

    replayer = EmbeddingsClient(_config(), replay=str(recording))
    replayer._client = Mock()
    replayer._client.post = Mock(side_effect=AssertionError("no network in replay"))
    assert replayer.embed(["hello"]) == [[1.0, 2.0]]
    replayer._client.post.assert_not_called()


def test_replay_is_request_keyed_not_positional(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_TOKEN", "t")
    recording = tmp_path / "fleet-rec.json"

    recorder = TokenizerClient(_config(), record=str(recording))
    recorder._client = Mock()
    recorder._client.post = Mock(side_effect=[_resp({"count": 3}), _resp({"count": 7})])
    assert recorder.tokenize("abc") == 3
    assert recorder.tokenize("abcdefg") == 7

    replayer = TokenizerClient(_config(), replay=str(recording))
    replayer._client = Mock()
    replayer._client.post = Mock(side_effect=AssertionError("no network in replay"))
    # Request out of recorded order -> matched by request hash, not position.
    assert replayer.tokenize("abcdefg") == 7
    assert replayer.tokenize("abc") == 3


def test_429_penalizes_shared_broker(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    broker = RateBroker(fleet_rpm={"bge-m3": 30}, sleep=lambda d: None)
    client = EmbeddingsClient(_config(), rate_broker=broker)
    client._client = Mock()

    err_response = Mock(status_code=429, headers={"Retry-After": "30"})
    http_error = httpx.HTTPStatusError("429", request=Mock(), response=err_response)
    bad = Mock()
    bad.raise_for_status = Mock(side_effect=http_error)
    client._client.post = Mock(return_value=bad)

    with pytest.raises(httpx.HTTPStatusError):
        client.embed(["x"])

    # The bge-m3 bucket is now cooling down (>= 60s floor).
    assert broker.acquire("bge-m3") >= 60.0


class _LaneSink:
    def __init__(self):
        self.events = []

    def on_lane_update(self, lane, state):
        self.events.append((lane, dict(state)))


def test_live_call_emits_lane_updates(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "t")
    sink = _LaneSink()
    http = Mock()
    http.post.return_value = _resp({"results": [{"index": 0, "relevance_score": 0.9}]})
    client = RerankerClient(
        _config(),
        rate_broker=RateBroker({"bge-reranker-v2-m3": 10.0}),
        http_client=http,
        stage="reranker",
        sink=sink,
    )
    client.score("q", ["d"])
    assert [state["in_flight"] for _, state in sink.events] == [1, 0]
    lane, state = sink.events[0]
    assert lane == state["model"] == "bge-reranker-v2-m3"
    assert state["stage"] == "reranker"
    assert state["calls"] == 1
    assert state["rpm_cap"] == 10 and state["rpm_used"] == 1


def test_replay_emits_no_lane_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_TOKEN", "t")
    recording = tmp_path / "fleet.json"
    http = Mock()
    http.post.return_value = _resp({"results": [{"index": 0, "relevance_score": 0.9}]})
    recorder = RerankerClient(_config(), http_client=http, record=str(recording))
    recorder.score("q", ["d"])

    sink = _LaneSink()
    replayer = RerankerClient(_config(), replay=str(recording), stage="reranker", sink=sink)
    replayer.score("q", ["d"])
    assert sink.events == []  # lanes are never theater: no network, no lane
