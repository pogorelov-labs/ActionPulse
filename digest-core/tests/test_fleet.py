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
