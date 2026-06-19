"""Per-model rate broker for the LLM gateway fleet (PR2).

One broker per run, shared by every fleet client (R1). The corp gateway meters
*requests* (not tokens), with an independent RPM bucket per model, so the broker
meters requests and distributes work across buckets:

  * per-model token buckets (RPM cap, burst, monotonic refill);
  * intra-model serialization / cross-model parallelism (one lock per model);
  * ``Retry-After`` penalties on 429 (floored at 60s);
  * hard per-stage call budgets (e.g. ``extractor <= 2``) per run (R4),
    raising :class:`StageCallBudgetExceeded`.

``acquire`` only sleeps when a bucket is actually empty, so offline/tests run at
full speed (burst covers the first few calls). ``clock``/``sleep`` are injectable
for deterministic unit tests.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Deque, Dict, Optional

if TYPE_CHECKING:
    from digest_core.config import LLMConfig

# Sensible default RPM per known fleet model (see REDESIGN_PLAN.md §0.3). Unknown
# models fall back to ``default_rpm``.
DEFAULT_FLEET_RPM: Dict[str, float] = {
    "qwen35-397b-a17b": 15,
    "qwen3-next-80b-a3b": 45,
    "glm-4.7-flash": 60,
    "qwen35-35b-a3b": 30,
    "bge-m3": 30,
    "qwen3-embedding": 30,
    "bge-reranker-v2-m3": 10,
}

# Hard per-stage call budgets per run. extractor=2 matches ADR-008 (1 primary +
# 1 quality retry); the rest are headroom for fleet stages added in later PRs.
DEFAULT_STAGE_CALL_BUDGETS: Dict[str, int] = {
    "extractor": 2,
    "reranker": 10,
    "embeddings": 30,
    "judge": 8,
    "tokenize": 20,
}

PENALTY_FLOOR_SECONDS = 60.0


class StageCallBudgetExceeded(Exception):
    """Raised when a stage exceeds its hard per-run call budget."""

    def __init__(self, stage: str, budget: int):
        super().__init__(f"Stage '{stage}' exceeded its per-run call budget of {budget}")
        self.stage = stage
        self.budget = budget


@dataclass
class _TokenBucket:
    rate_per_sec: float
    burst: float
    tokens: float
    updated_at: float
    penalty_until: float = 0.0


class RateBroker:
    """Meters requests across per-model RPM buckets with per-stage call budgets."""

    @classmethod
    def from_config(
        cls, llm: "LLMConfig", *, stage_call_budgets: Optional[Dict[str, int]] = None
    ) -> "RateBroker":
        """Build a broker from an ``LLMConfig`` — the shared ``fleet_rpm`` / ``fleet_burst`` /
        ``rate_limit_rpm`` shape every fleet client and the gateway use. Pass
        ``stage_call_budgets`` to override the config's (e.g. ``ask``'s dedicated budget);
        otherwise the config's own ``stage_call_budgets`` are used. This is the single
        construction point — callers used to hand-roll these four args in four places."""
        return cls(
            fleet_rpm=llm.fleet_rpm,
            burst=llm.fleet_burst,
            default_rpm=llm.rate_limit_rpm,
            stage_call_budgets=(
                stage_call_budgets if stage_call_budgets is not None else llm.stage_call_budgets
            ),
        )

    def __init__(
        self,
        fleet_rpm: Optional[Dict[str, float]] = None,
        *,
        burst: int = 3,
        default_rpm: float = 15.0,
        stage_call_budgets: Optional[Dict[str, int]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._fleet_rpm = dict(fleet_rpm if fleet_rpm is not None else DEFAULT_FLEET_RPM)
        self._burst = max(1, int(burst))
        self._default_rpm = float(default_rpm)
        self._stage_call_budgets = dict(
            stage_call_budgets if stage_call_budgets is not None else DEFAULT_STAGE_CALL_BUDGETS
        )
        self._clock = clock
        self._sleep = sleep
        self._buckets: Dict[str, _TokenBucket] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._registry_lock = threading.Lock()
        self._stage_calls: Dict[str, int] = {}
        # Lane telemetry (§4.3): acquire timestamps in the last 60s per model —
        # the honest "RPM n/cap" the live display shows. Passive: renderers
        # pull snapshots; the broker never knows about sinks.
        self._recent_acquires: Dict[str, Deque[float]] = {}

    # -- model rate buckets --------------------------------------------------

    def _rpm_for(self, model: str) -> float:
        return float(self._fleet_rpm.get(model, self._default_rpm))

    def _lock_for(self, model: str) -> threading.Lock:
        lock = self._locks.get(model)
        if lock is None:
            with self._registry_lock:
                lock = self._locks.setdefault(model, threading.Lock())
        return lock

    def _bucket_for(self, model: str) -> _TokenBucket:
        bucket = self._buckets.get(model)
        if bucket is None:
            with self._registry_lock:
                bucket = self._buckets.get(model)
                if bucket is None:
                    bucket = _TokenBucket(
                        rate_per_sec=self._rpm_for(model) / 60.0,
                        burst=float(self._burst),
                        tokens=float(self._burst),
                        updated_at=self._clock(),
                    )
                    self._buckets[model] = bucket
        return bucket

    def acquire(self, model: str) -> float:
        """Block until a request slot for ``model`` is free; return seconds waited.

        Holding the per-model lock serializes same-model callers (intra-model
        serial) while different models proceed in parallel (cross-model).
        """
        with self._lock_for(model):
            bucket = self._bucket_for(model)
            waited = 0.0
            now = self._clock()

            # Honor an active 429 penalty window first.
            if bucket.penalty_until > now:
                delay = bucket.penalty_until - now
                self._sleep(delay)
                waited += delay
                now = bucket.penalty_until
                bucket.tokens = 0.0
                bucket.updated_at = now

            # Monotonic refill.
            bucket.tokens = min(
                bucket.burst, bucket.tokens + (now - bucket.updated_at) * bucket.rate_per_sec
            )
            bucket.updated_at = now

            # Consume one token, waiting for it to accrue if the bucket is empty.
            if bucket.tokens < 1.0:
                deficit = 1.0 - bucket.tokens
                delay = deficit / bucket.rate_per_sec if bucket.rate_per_sec > 0 else 0.0
                if delay > 0:
                    self._sleep(delay)
                    waited += delay
                bucket.updated_at = now + delay
                bucket.tokens = 0.0
            else:
                bucket.tokens -= 1.0
            self._note_acquire(model)
            return waited

    # -- lane telemetry (§4.3) -------------------------------------------------

    def _note_acquire(self, model: str) -> None:
        with self._registry_lock:
            window = self._recent_acquires.setdefault(model, deque())
            now = self._clock()
            window.append(now)
            while window and now - window[0] > 60.0:
                window.popleft()

    def usage_snapshot(self, model: str) -> Dict[str, Any]:
        """Requests in the trailing 60s vs the model's cap (for lane rendering)."""
        now = self._clock()
        with self._registry_lock:
            window = self._recent_acquires.get(model)
            used = 0
            if window:
                while window and now - window[0] > 60.0:
                    window.popleft()
                used = len(window)
        bucket = self._buckets.get(model)
        penalty = max(0.0, bucket.penalty_until - now) if bucket else 0.0
        return {
            "rpm_used": used,
            "rpm_cap": int(self._rpm_for(model)),
            "penalty_remaining_s": round(penalty, 1),
        }

    def penalize(self, model: str, retry_after: float) -> None:
        """Apply a 429 cool-down to ``model`` (floored at 60s) and drain its bucket."""
        delay = max(float(retry_after), PENALTY_FLOOR_SECONDS)
        with self._lock_for(model):
            bucket = self._bucket_for(model)
            bucket.penalty_until = self._clock() + delay
            bucket.tokens = 0.0

    # -- per-stage call budgets ----------------------------------------------

    def note_call(self, stage: str) -> int:
        """Count one logical call against ``stage``'s budget; raise if exceeded."""
        with self._registry_lock:
            count = self._stage_calls.get(stage, 0) + 1
            self._stage_calls[stage] = count
            budget = self._stage_call_budgets.get(stage)
        if budget is not None and count > budget:
            raise StageCallBudgetExceeded(stage, budget)
        return count

    def calls_made(self, stage: str) -> int:
        """Logical calls charged to ``stage`` so far this run."""
        with self._registry_lock:
            return self._stage_calls.get(stage, 0)
