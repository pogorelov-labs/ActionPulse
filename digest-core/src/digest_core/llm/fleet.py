"""Shared client for the corp gateway *fleet* endpoints (PR2, R1).

One boundary, one Bearer, tokens unmetered — only the per-model RPM bucket caps
throughput. This module adds the non-chat endpoints used by later PRs:

  * :class:`EmbeddingsClient` -> ``/v1/embeddings`` (relevance/threading, PR9/PR12)
  * :class:`RerankerClient`   -> ``/rerank``        (cross-encoder support; path
    per D4/PC-2, configurable via ``reranker.endpoint_path``)
  * :class:`TokenizerClient`  -> ``/v1/tokenize``   (exact token counts)

All endpoints are derived from ``LLMConfig.endpoint`` and every request is paced
through the shared :class:`~digest_core.llm.rate_broker.RateBroker` (per-model
RPM bucket + optional per-stage call budget). The clients are
**record/replay-aware and namespaced per endpoint**; in replay mode they make
**zero network calls**. The reranker is consumed by the P2 citation gate behind
``reranker.enabled`` (default OFF — EP-12); the rest stay unwired until their PRs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx
import structlog

from digest_core.config import LLMConfig
from digest_core.llm.rate_broker import RateBroker
from digest_core.progress import NullSink, ProgressSink, emit

logger = structlog.get_logger()


def derive_fleet_endpoint(chat_endpoint: str, name: str) -> str:
    """Map the chat endpoint to a sibling fleet endpoint.

    A plain ``name`` maps to ``/v1/<name>``; a leading-slash name (``/rerank``)
    is absolute under the gateway host (the LiteLLM front mounts rerank at the
    root, not under ``/v1`` — see ENDPOINT-FACTS §1).
    """
    if not chat_endpoint:
        return ""
    if "/v1/" in chat_endpoint:
        base = chat_endpoint.split("/v1/")[0]
        return f"{base}{name}" if name.startswith("/") else f"{base}/v1/{name}"
    # Fallback: swap the last path segment (e.g. .../chat -> .../<name>).
    base = chat_endpoint.rsplit("/", 1)[0]
    return f"{base}{name}" if name.startswith("/") else f"{base}/{name}"


def _request_hash(endpoint_name: str, model: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps(
        {"endpoint": endpoint_name, "model": model, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class FleetClient:
    """Base for fleet endpoints: shared broker + per-endpoint record/replay.

    Record/replay files are namespaced ``{"endpoints": {name: [{request_hash,
    response}]}}`` so several endpoints share one channel. In replay mode no HTTP
    client is ever touched.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        rate_broker: Optional[RateBroker] = None,
        record: Optional[str] = None,
        replay: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        stage: Optional[str] = None,
        sink: Optional[ProgressSink] = None,
    ):
        self.config = config
        self._broker = rate_broker
        self._stage = stage
        self._sink = sink or NullSink()
        self._calls_made = 0
        self._record_path = Path(record) if record else None
        self._replay_data: Optional[Dict[str, Any]] = None
        self._replay_consumed: Dict[str, set] = {}
        if replay:
            self._replay_data = json.loads(Path(replay).read_text(encoding="utf-8"))
        self._client = http_client
        self._owns_client = http_client is None

    def _emit_lane(self, model: str, in_flight: int) -> None:
        """Lane telemetry (design §4.3) — real network calls only, never replay.

        Telemetry is never load-bearing: any broker oddity (incl. test doubles
        without ``usage_snapshot``) degrades to zeroed usage, never raises.
        """
        usage = {"rpm_used": 0, "rpm_cap": 0, "penalty_remaining_s": 0.0}
        if self._broker is not None:
            try:
                candidate = self._broker.usage_snapshot(model)
                if isinstance(candidate, dict):
                    usage = candidate
            except Exception:  # noqa: BLE001 - telemetry must not break calls
                pass
        emit(
            self._sink,
            "on_lane_update",
            model,
            {
                "model": model,
                "stage": self._stage or "fleet",
                "in_flight": in_flight,
                "calls": self._calls_made,
                **usage,
            },
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.config.timeout_s), headers=self.config.headers
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _post(self, endpoint_name: str, model: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req_hash = _request_hash(endpoint_name, model, payload)

        # Per-stage call budget holds in replay too (parity with the gateway),
        # so deterministic runs prove the same ceilings as live ones.
        if self._broker is not None and self._stage is not None:
            self._broker.note_call(self._stage)

        # ── REPLAY MODE (no network) ─────────────────────────────────
        if self._replay_data is not None:
            return self._replay_lookup(endpoint_name, req_hash)

        # ── LIVE / RECORD ────────────────────────────────────────────
        if self._broker is not None:
            self._broker.acquire(model)
        self._calls_made += 1
        self._emit_lane(model, in_flight=1)
        headers = dict(self.config.headers)
        headers["Authorization"] = f"Bearer {self.config.get_token()}"
        try:
            response = self._http().post(
                self._endpoint(endpoint_name), json=payload, headers=headers
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and self._broker is not None:
                retry_after = exc.response.headers.get("Retry-After")
                self._broker.penalize(model, float(retry_after) if retry_after else 60.0)
            raise
        finally:
            self._emit_lane(model, in_flight=0)
        data = response.json()
        if self._record_path is not None:
            self._record(endpoint_name, req_hash, data)
        return data

    def _endpoint(self, name: str) -> str:
        return derive_fleet_endpoint(self.config.endpoint, name)

    def _replay_lookup(self, endpoint_name: str, req_hash: str) -> Dict[str, Any]:
        entries = (self._replay_data.get("endpoints", {}) or {}).get(endpoint_name, [])
        consumed = self._replay_consumed.setdefault(endpoint_name, set())
        # Request-keyed first.
        for idx, entry in enumerate(entries):
            if idx in consumed:
                continue
            if entry.get("request_hash") == req_hash:
                consumed.add(idx)
                return entry["response"]
        # Positional fallback (legacy recordings without request_hash).
        for idx, entry in enumerate(entries):
            if idx not in consumed:
                consumed.add(idx)
                return entry["response"]
        raise RuntimeError(f"Fleet replay exhausted for endpoint '{endpoint_name}'")

    def _record(self, endpoint_name: str, req_hash: str, response: Dict[str, Any]) -> None:
        if self._record_path.exists():
            existing = json.loads(self._record_path.read_text(encoding="utf-8"))
        else:
            existing = {"endpoints": {}}
        existing.setdefault("endpoints", {}).setdefault(endpoint_name, []).append(
            {"request_hash": req_hash, "response": response}
        )
        self._record_path.parent.mkdir(parents=True, exist_ok=True)
        self._record_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
        )


class EmbeddingsClient(FleetClient):
    """``/v1/embeddings`` — dense vectors for cosine relevance/threading."""

    def __init__(self, config: LLMConfig, *, model: str = "bge-m3", **kwargs: Any):
        super().__init__(config, **kwargs)
        self.model = model

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": list(texts)}
        data = self._post("embeddings", self.model, payload)
        return [item["embedding"] for item in data.get("data", [])]


class RerankerClient(FleetClient):
    """``/rerank`` cross-encoder — the scarce, non-batchable fleet resource.

    Payload is the Cohere/LiteLLM rerank shape (``query`` + ``documents``);
    the response parser tolerates both ``scores`` lists and ``results`` rows.
    The exact corp path is configurable (``endpoint_path``) because only
    ``/rerank`` was probe-verified — flipping to ``/v1/score`` is a config
    change, not a code change (EP-14 confirms).
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        model: str = "bge-reranker-v2-m3",
        endpoint_path: str = "/rerank",
        **kwargs: Any,
    ):
        super().__init__(config, **kwargs)
        self.model = model
        self.endpoint_path = endpoint_path

    def _endpoint(self, name: str) -> str:
        return derive_fleet_endpoint(self.config.endpoint, self.endpoint_path or name)

    def score(self, query: str, docs: Sequence[str]) -> List[float]:
        if not docs:
            return []
        payload = {"model": self.model, "query": query, "documents": list(docs)}
        data = self._post("rerank", self.model, payload)
        if "scores" in data:
            return [float(s) for s in data["scores"]]
        results = sorted(data.get("results", []), key=lambda r: r.get("index", 0))
        return [float(r.get("score", r.get("relevance_score", 0.0))) for r in results]


class TokenizerClient(FleetClient):
    """``/v1/tokenize`` — exact token counts (vs the words*1.3 estimate)."""

    def __init__(self, config: LLMConfig, *, model: str = "qwen35-397b-a17b", **kwargs: Any):
        super().__init__(config, **kwargs)
        self.model = model

    def tokenize(self, text: str) -> int:
        payload = {"model": self.model, "input": text}
        data = self._post("tokenize", self.model, payload)
        if "count" in data:
            return int(data["count"])
        tokens = data.get("tokens", [])
        return len(tokens) if isinstance(tokens, list) else int(tokens)
