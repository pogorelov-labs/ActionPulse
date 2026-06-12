"""RateBroker: per-model token buckets, 429 penalties, per-stage budgets (PR2).

A deterministic fake clock + recorded sleeps make the timing assertions exact
without any real waiting.
"""

import pytest

from digest_core.llm.rate_broker import RateBroker, StageCallBudgetExceeded


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _broker(clock, sleeps, **kwargs):
    return RateBroker(clock=clock, sleep=lambda d: sleeps.append(d), **kwargs)


# -- token bucket: burst + refill -------------------------------------------


def test_burst_allows_immediate_then_paces():
    clock, sleeps = FakeClock(), []
    broker = _broker(clock, sleeps, fleet_rpm={"m": 60}, burst=3)  # 1 token/sec

    assert broker.acquire("m") == 0.0
    assert broker.acquire("m") == 0.0
    assert broker.acquire("m") == 0.0
    assert sleeps == []  # burst absorbed the first three

    waited = broker.acquire("m")  # bucket empty, no time elapsed -> wait one token
    assert waited == pytest.approx(1.0, abs=0.01)
    assert sleeps[-1] == pytest.approx(1.0, abs=0.01)


def test_refill_is_monotonic_and_capped():
    clock, sleeps = FakeClock(), []
    broker = _broker(clock, sleeps, fleet_rpm={"m": 60}, burst=2)
    broker.acquire("m")
    broker.acquire("m")  # drain

    clock.advance(2.0)  # 2 tokens refill
    assert broker.acquire("m") == 0.0
    assert broker.acquire("m") == 0.0
    assert sleeps == []

    clock.advance(100.0)  # refill is capped at burst (2), not 100
    broker.acquire("m")
    broker.acquire("m")
    assert broker.acquire("m") > 0  # third needs to wait


def test_cross_model_buckets_are_independent():
    clock, sleeps = FakeClock(), []
    broker = _broker(clock, sleeps, fleet_rpm={"a": 60, "b": 60}, burst=1)
    assert broker.acquire("a") == 0.0
    assert broker.acquire("b") == 0.0  # separate bucket keeps its own burst
    assert sleeps == []


def test_unknown_model_falls_back_to_default_rpm():
    clock, sleeps = FakeClock(), []
    broker = _broker(clock, sleeps, fleet_rpm={}, burst=1, default_rpm=60)
    assert broker.acquire("mystery") == 0.0
    assert broker.acquire("mystery") == pytest.approx(1.0, abs=0.01)


# -- 429 penalties -----------------------------------------------------------


def test_penalize_floors_at_60_seconds():
    clock, sleeps = FakeClock(), []
    broker = _broker(clock, sleeps, fleet_rpm={"m": 60}, burst=3)
    broker.penalize("m", 5)  # below the 60s floor
    assert broker.acquire("m") >= 60.0


def test_penalize_honors_larger_retry_after():
    clock, sleeps = FakeClock(), []
    broker = _broker(clock, sleeps, fleet_rpm={"m": 60}, burst=3)
    broker.penalize("m", 120)
    assert broker.acquire("m") >= 120.0


# -- per-stage call budgets --------------------------------------------------


def test_stage_call_budget_enforced():
    broker = RateBroker(fleet_rpm={}, stage_call_budgets={"extractor": 2})
    assert broker.note_call("extractor") == 1
    assert broker.note_call("extractor") == 2
    with pytest.raises(StageCallBudgetExceeded) as exc_info:
        broker.note_call("extractor")
    assert exc_info.value.stage == "extractor"
    assert exc_info.value.budget == 2
    assert broker.calls_made("extractor") == 3  # counted even though it raised


def test_stage_without_a_budget_is_unlimited():
    broker = RateBroker(fleet_rpm={}, stage_call_budgets={"extractor": 1})
    for _ in range(5):
        broker.note_call("unbudgeted")
    assert broker.calls_made("unbudgeted") == 5


def test_default_budgets_match_adr_008():
    broker = RateBroker()  # defaults
    assert broker.note_call("extractor") == 1
    assert broker.note_call("extractor") == 2
    with pytest.raises(StageCallBudgetExceeded):
        broker.note_call("extractor")


class TestUsageSnapshot:
    """Lane telemetry (§4.3): trailing-60s request counts per model."""

    def test_counts_acquires_in_window(self):
        clock = FakeClock()
        broker = _broker(clock, [], fleet_rpm={"m": 60.0})
        for _ in range(3):
            broker.acquire("m")
        snap = broker.usage_snapshot("m")
        assert snap["rpm_used"] == 3
        assert snap["rpm_cap"] == 60
        assert snap["penalty_remaining_s"] == 0.0

    def test_old_acquires_age_out(self):
        clock = FakeClock()
        broker = _broker(clock, [], fleet_rpm={"m": 60.0}, burst=10)
        broker.acquire("m")
        clock.advance(61.0)
        broker.acquire("m")
        assert broker.usage_snapshot("m")["rpm_used"] == 1

    def test_penalty_surfaces_remaining_seconds(self):
        clock = FakeClock()
        broker = _broker(clock, [], fleet_rpm={"m": 60.0})
        broker.acquire("m")
        broker.penalize("m", 90.0)
        snap = broker.usage_snapshot("m")
        assert snap["penalty_remaining_s"] == 90.0

    def test_unknown_model_uses_default_cap(self):
        broker = _broker(FakeClock(), [], fleet_rpm={}, default_rpm=15.0)
        assert broker.usage_snapshot("mystery")["rpm_cap"] == 15
