"""A1.6 — the v1-vs-v3 contract parity harness.

The comparison logic is the part that has to be right: a naive diff would flag
v3's *intended* behaviour as a regression and be ignored forever after. So the
three-way split (regression / explained / difference) is tested directly, and the
end-to-end replay is exercised with synthesized recordings so the harness proves
itself offline — before any corp capture exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from digest_core.eval.contract_parity import (
    COVERAGE_TOL,
    ContractRun,
    compare_contracts,
)


def _run(contract, *, items, coverage=1.0, unverifiable=0, sections=None, drops=None):
    stats = None
    if contract == "v3":
        stats = {
            "items": items,
            "dropped_unknown_evidence_id": (drops or {}).get("unknown", 0),
            "dropped_missing_evidence_span": (drops or {}).get("no_span", 0),
        }
    return ContractRun(
        contract=contract,
        metrics={
            "item_count": items,
            "section_counts": sections or {"My actions": items},
            "span_coverage": coverage,
            "evidence_ids_unverifiable": unverifiable,
            "evidence_ids_checked": True,
        },
        digest_path=Path("/dev/null"),
        adapter_stats=stats,
    )


class TestRegressions:
    """Things that must block the A1.7 flip."""

    def test_span_coverage_falling_is_a_regression(self):
        report = compare_contracts(
            _run("v1", items=5, coverage=1.0),
            _run("v3", items=5, coverage=1.0 - COVERAGE_TOL - 0.01),
        )
        assert not report.ok
        assert any("span_coverage fell" in r for r in report.regressions)

    def test_coverage_within_tolerance_is_not_a_regression(self):
        report = compare_contracts(
            _run("v1", items=5, coverage=1.0),
            _run("v3", items=5, coverage=1.0 - COVERAGE_TOL + 0.01),
        )
        assert report.ok

    def test_unverifiable_ids_rising_is_a_regression(self):
        report = compare_contracts(
            _run("v1", items=5, unverifiable=0),
            _run("v3", items=5, unverifiable=2),
        )
        assert not report.ok
        assert any("evidence_ids_unverifiable rose" in r for r in report.regressions)

    def test_unexplained_item_loss_is_a_regression(self):
        """Items vanishing with no adapter accounting is the thing we most fear."""
        report = compare_contracts(
            _run("v1", items=10),
            _run("v3", items=6, drops={"unknown": 1}),
        )
        assert not report.ok
        assert any("vanished unexplained" in r for r in report.regressions)
        assert any("3 item(s)" in r for r in report.regressions)


class TestExplainedDeltas:
    """v3 dropping bad items is a correctness gain that *looks* like a loss."""

    def test_item_loss_fully_covered_by_adapter_drops_is_explained_not_regressed(self):
        report = compare_contracts(
            _run("v1", items=10),
            _run("v3", items=7, drops={"unknown": 2, "no_span": 1}),
        )
        assert report.ok, report.regressions
        assert report.explained
        assert "correctness gain" in report.explained[0]

    def test_partial_explanation_still_fails(self):
        """3 lost, 2 explained -> the 3rd is unaccounted for."""
        report = compare_contracts(
            _run("v1", items=10),
            _run("v3", items=7, drops={"unknown": 2}),
        )
        assert not report.ok


class TestExpectedDifferences:
    """v3 is *supposed* to route differently. Flagging that as a regression would
    make the harness cry wolf on every single run."""

    def test_section_redistribution_is_a_difference_not_a_regression(self):
        report = compare_contracts(
            _run("v1", items=4, sections={"My actions": 3, "FYI": 1}),
            _run("v3", items=4, sections={"Urgent": 1, "My actions": 2, "FYI": 1}),
        )
        assert report.ok
        assert any("section distribution changed" in d for d in report.differences)

    def test_item_count_rising_is_a_difference(self):
        """v3 splits a meeting and its preparation task into two items."""
        report = compare_contracts(_run("v1", items=4), _run("v3", items=5))
        assert report.ok
        assert any("item_count rose" in d for d in report.differences)

    def test_identical_runs_report_clean_parity(self):
        report = compare_contracts(_run("v1", items=5), _run("v3", items=5))
        assert report.ok
        assert not report.regressions and not report.differences


class TestReportShape:
    def test_verdict_and_payload_are_serialisable(self):
        report = compare_contracts(_run("v1", items=10), _run("v3", items=8, drops={"unknown": 2}))
        payload = report.to_dict()
        assert payload["verdict"] == "PARITY"
        assert payload["candidate"]["adapter_stats"]["dropped_unknown_evidence_id"] == 2
        json.dumps(payload)  # must round-trip for a machine-readable artifact

    def test_render_names_the_contracts_and_verdict(self):
        text = compare_contracts(_run("v1", items=5), _run("v3", items=5)).render()
        assert "v1 -> v3" in text and "PARITY" in text

    def test_regressed_verdict_surfaces(self):
        report = compare_contracts(_run("v1", items=5), _run("v3", items=1))
        assert report.to_dict()["verdict"] == "REGRESSED"
        assert "REGRESSED" in report.render()


class TestExplainedDropAccounting:
    def test_v1_run_has_no_adapter_stats_and_explains_nothing(self):
        assert _run("v1", items=5).explained_drops == 0

    def test_drops_sum_across_both_reasons(self):
        run = _run("v3", items=5, drops={"unknown": 2, "no_span": 3})
        assert run.explained_drops == 5


@pytest.mark.parametrize("contract", ["v1", "v3"])
def test_contract_env_is_restored_after_a_run(monkeypatch, contract):
    """The harness sets DIGEST_EXTRACT_CONTRACT around each run; a leak would make
    every later run in the same process silently use the wrong contract."""
    import os

    from digest_core.eval.contract_parity import _contract

    monkeypatch.setenv("DIGEST_EXTRACT_CONTRACT", "sentinel")
    with _contract(contract):
        assert os.environ["DIGEST_EXTRACT_CONTRACT"] == contract
    assert os.environ["DIGEST_EXTRACT_CONTRACT"] == "sentinel"


def test_contract_env_is_removed_when_it_was_unset(monkeypatch):
    import os

    from digest_core.eval.contract_parity import _contract

    monkeypatch.delenv("DIGEST_EXTRACT_CONTRACT", raising=False)
    with _contract("v3"):
        assert os.environ["DIGEST_EXTRACT_CONTRACT"] == "v3"
    assert "DIGEST_EXTRACT_CONTRACT" not in os.environ
