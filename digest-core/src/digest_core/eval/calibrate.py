"""Per-stratum tau calibration at a target recall (PR10).

Given (support_score, gold_label) pairs per stratum (ru/en), find the highest
threshold tau whose recall on the positive (good) items still clears the target
(>= 0.90). The result drives the PR8 gate's weak_evidence decision; ``gates_p8``
is True only when every stratum has enough samples and a usable tau.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

Scored = List[Tuple[float, bool]]  # (support_score, gold_is_good)


def calibrate_stratum(scored: Scored, *, target_recall: float = 0.90) -> Dict[str, float]:
    """Highest tau such that recall(positives) >= target_recall."""
    positives = [score for score, good in scored if good]
    n_pos = len(positives)
    if n_pos == 0:
        return {"tau": 0.0, "recall": 0.0, "n_pos": 0, "n_total": len(scored), "usable": False}

    # Candidate thresholds: every observed positive score (plus 0.0). For a given
    # tau, recall = fraction of positives with score >= tau. Pick the largest tau
    # whose recall still clears the target (most selective gate that keeps recall).
    candidates = sorted({0.0, *positives})
    best_tau = 0.0
    best_recall = 1.0
    for tau in candidates:
        kept = sum(1 for score in positives if score >= tau)
        recall = kept / n_pos
        if recall >= target_recall:
            best_tau = tau
            best_recall = recall
        else:
            break  # recall only drops as tau rises
    return {
        "tau": best_tau,
        "recall": best_recall,
        "n_pos": n_pos,
        "n_total": len(scored),
        "usable": True,
    }


def calibrate(
    strata: Dict[str, Scored], *, target_recall: float = 0.90, min_samples: int = 20
) -> Dict[str, object]:
    """Calibrate every stratum; gates_p8 True only if all are usable and well-sampled."""
    per_stratum: Dict[str, Dict[str, float]] = {}
    gates = True
    for name, scored in strata.items():
        result = calibrate_stratum(scored, target_recall=target_recall)
        enough = result["n_pos"] >= min_samples
        result["low_confidence"] = not enough
        per_stratum[name] = result
        gates = gates and result["usable"] and enough
    if not strata:
        gates = False
    return {
        "target_recall": target_recall,
        "min_samples": min_samples,
        "strata": per_stratum,
        "gates_p8": gates,
    }
