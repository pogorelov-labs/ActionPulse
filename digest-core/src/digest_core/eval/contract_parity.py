"""Extraction-contract parity: does v3 match v1 on the metrics that matter? (A1.6)

A1.7 flips ``extract.contract`` to ``v3``. That flip needs evidence, and the
evidence has to come from the same evidence: replay ONE ingest snapshot through
BOTH contracts and compare. This is the offline half — the corp session only has
to *capture* (an ingest snapshot plus one LLM recording per contract, brief task
T3); everything after that runs on a laptop, forever.

Why a dedicated comparison instead of `compare_metrics`
------------------------------------------------------
`replay_harness.compare_metrics` answers "did this regress against its own
baseline?". That is the wrong question here, because **v3 is supposed to differ**:

* Section distribution changes by design — `others_actions` reads as FYI, a
  High-severity `risks_blockers` entry leads as URGENT. A diff that flags this as
  a regression would fail every single time and teach everyone to ignore it.
* v3 may legitimately produce **fewer** items. The adapter drops an item citing an
  `evidence_id` the pipeline never issued, where v1 carries it through on a
  model-supplied `source_ref`. That is a **correctness improvement wearing the
  costume of a regression**, and the raw item counts cannot tell the two apart.

So this module splits the answer three ways — `regressions` (must not happen),
`explained` (a delta the adapter accounts for, i.e. a win), and `differences`
(expected, informational) — rather than collapsing them into one number that
would be either misleading or ignored.

What counts as a regression
---------------------------
* ``span_coverage`` falling — P2 is golden rule #1, and the whole point of the
  constrained contract is that it cannot get *worse*.
* ``evidence_ids_unverifiable`` rising — v3 should drive this to zero by
  construction, so any increase means the adapter is wrong.
* ``item_count`` falling by **more than the adapter explains**. Unexplained loss
  is items silently vanishing; explained loss is the gate doing its job.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from digest_core.eval.replay_harness import _NullDeliverer, _NullMetrics, compute_metrics

#: How far span coverage may drift before it is called a regression. Matches the
#: tolerance `replay_harness.compare_metrics` already uses, so the two agree.
COVERAGE_TOL = 0.05


@dataclass
class ContractRun:
    """One contract's replayed run over the shared snapshot."""

    contract: str
    metrics: Dict[str, Any]
    digest_path: Path
    #: run_meta["extract_v3"] — present only for v3. Carries the adapter's drop
    #: accounting, which is what makes an item-count delta explainable.
    adapter_stats: Optional[Dict[str, Any]] = None

    @property
    def explained_drops(self) -> int:
        stats = self.adapter_stats or {}
        return int(stats.get("dropped_unknown_evidence_id", 0)) + int(
            stats.get("dropped_missing_evidence_span", 0)
        )


@dataclass
class ParityReport:
    """The three-way answer. Only ``regressions`` blocks the flip."""

    baseline: ContractRun
    candidate: ContractRun
    regressions: List[str] = field(default_factory=list)
    explained: List[str] = field(default_factory=list)
    differences: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.regressions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": "PARITY" if self.ok else "REGRESSED",
            "baseline": {"contract": self.baseline.contract, "metrics": self.baseline.metrics},
            "candidate": {
                "contract": self.candidate.contract,
                "metrics": self.candidate.metrics,
                "adapter_stats": self.candidate.adapter_stats,
            },
            "regressions": self.regressions,
            "explained": self.explained,
            "differences": self.differences,
        }

    def render(self) -> str:
        lines = [f"contract parity: {self.baseline.contract} -> {self.candidate.contract}"]
        lines.append(f"  verdict: {'PARITY' if self.ok else 'REGRESSED'}")
        for label, entries in (
            ("regression", self.regressions),
            ("explained", self.explained),
            ("difference", self.differences),
        ):
            for entry in entries:
                lines.append(f"  [{label}] {entry}")
        return "\n".join(lines)


@contextmanager
def _contract(contract: str):
    """Run the pipeline under a given extraction contract.

    Set through the environment rather than a parameter because ``run_digest``
    reads it off ``Config``; since #220 an invalid value now fails loudly instead
    of silently selecting v1, which is exactly the property a measurement harness
    needs.
    """
    key = "DIGEST_EXTRACT_CONTRACT"
    previous = os.environ.get(key)
    os.environ[key] = contract
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def run_contract(
    *,
    snapshot: Path,
    recording: Path,
    contract: str,
    digest_date: str,
    out_dir: Path,
    model: str = "qwen35-397b-a17b",
) -> ContractRun:
    """Replay one contract over the shared ingest snapshot and measure the result.

    Both contracts must see the SAME snapshot; that is what makes the comparison
    mean anything. The recordings differ per contract because the prompts differ
    (and therefore so do the request hashes replay matches on).
    """
    import digest_core.run as run_module

    out_dir.mkdir(parents=True, exist_ok=True)
    swaps = {
        "start_health_server": lambda *a, **k: None,
        "MetricsCollector": _NullMetrics,
        "MattermostDeliverer": _NullDeliverer,
    }
    originals = {name: getattr(run_module, name) for name in swaps}
    for name, value in swaps.items():
        setattr(run_module, name, value)
    try:
        with _contract(contract):
            result = run_module.run_digest(
                from_date=digest_date,
                sources=["ews"],
                out=str(out_dir),
                model=model,
                window="calendar_day",
                state=str(out_dir / "state"),
                force=True,
                replay_ingest=str(snapshot),
                replay_llm=str(recording),
            )
    finally:
        for name, value in originals.items():
            setattr(run_module, name, value)

    digest_path = out_dir / f"digest-{digest_date}.json"
    digest_json = json.loads(digest_path.read_text(encoding="utf-8"))

    # Evidence ids the run actually issued — the denominator for "unverifiable".
    seen_ids = set()
    snapshot_data = json.loads(Path(snapshot).read_text(encoding="utf-8"))
    for message in snapshot_data.get("messages", []):
        if message.get("msg_id"):
            seen_ids.add(message["msg_id"])

    adapter_stats = None
    run_meta = getattr(result, "run_meta", None)
    if isinstance(run_meta, dict):
        adapter_stats = run_meta.get("extract_v3")

    return ContractRun(
        contract=contract,
        metrics=compute_metrics(digest_json, set()),
        digest_path=digest_path,
        adapter_stats=adapter_stats,
    )


def compare_contracts(baseline: ContractRun, candidate: ContractRun) -> ParityReport:
    """Three-way comparison: regressions block, explained deltas are wins."""
    report = ParityReport(baseline=baseline, candidate=candidate)
    base, cand = baseline.metrics, candidate.metrics

    # --- P2: span coverage must not fall -----------------------------------
    coverage_delta = cand["span_coverage"] - base["span_coverage"]
    if coverage_delta < -COVERAGE_TOL:
        report.regressions.append(
            f"span_coverage fell {base['span_coverage']} -> {cand['span_coverage']} "
            f"(tolerance {COVERAGE_TOL}); P2 must not get worse under a constrained contract"
        )

    # --- unverifiable evidence ids must not rise ----------------------------
    if cand["evidence_ids_unverifiable"] > base["evidence_ids_unverifiable"]:
        report.regressions.append(
            f"evidence_ids_unverifiable rose {base['evidence_ids_unverifiable']} -> "
            f"{cand['evidence_ids_unverifiable']}; the v3 adapter should drive this to zero"
        )

    # --- item count: separate real loss from the gate doing its job ---------
    lost = base["item_count"] - cand["item_count"]
    if lost > 0:
        explained = candidate.explained_drops
        if lost <= explained:
            report.explained.append(
                f"item_count fell {base['item_count']} -> {cand['item_count']} ({lost}), fully "
                f"accounted for by {explained} adapter drop(s) — items citing an evidence_id the "
                "pipeline never issued, or with no supporting span. v1 carries those through on a "
                "model-supplied source_ref; v3 refuses them. This is a correctness gain, not a loss."
            )
        else:
            report.regressions.append(
                f"item_count fell {base['item_count']} -> {cand['item_count']} ({lost}), but the "
                f"adapter only accounts for {explained}. {lost - explained} item(s) vanished "
                "unexplained — investigate before flipping."
            )
    elif lost < 0:
        report.differences.append(
            f"item_count rose {base['item_count']} -> {cand['item_count']}; v3 splits some items "
            "across typed lists (a meeting and its preparation task are two items)"
        )

    # --- section routing: expected to differ, never a regression ------------
    if base["section_counts"] != cand["section_counts"]:
        report.differences.append(
            f"section distribution changed {base['section_counts']} -> {cand['section_counts']}; "
            "v3 routes by typed list (others_actions -> FYI, High-severity risks -> Urgent) "
            "instead of by a model-emitted title string. Expected."
        )

    return report


def evaluate_parity(
    *,
    snapshot: Path,
    baseline_recording: Path,
    candidate_recording: Path,
    digest_date: str,
    out_dir: Path,
    baseline_contract: str = "v1",
    candidate_contract: str = "v3",
) -> ParityReport:
    """Run both contracts over one snapshot and compare. The whole harness."""
    baseline = run_contract(
        snapshot=snapshot,
        recording=baseline_recording,
        contract=baseline_contract,
        digest_date=digest_date,
        out_dir=out_dir / baseline_contract,
    )
    candidate = run_contract(
        snapshot=snapshot,
        recording=candidate_recording,
        contract=candidate_contract,
        digest_date=digest_date,
        out_dir=out_dir / candidate_contract,
    )
    return compare_contracts(baseline, candidate)
