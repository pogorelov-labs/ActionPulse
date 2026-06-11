"""Inter-rater agreement statistics for judge calibration (EP-5, frontier-audit F3).

Vendored from the quality-loop plugin's deterministic ``scripts/agreement.py``:
statistical agreement must be exact and reproducible — never token-estimated.

Reading the numbers: κ/α are **drift trackers**, not the gate. ``may_gate`` only
means κ cleared the Landis-Koch "moderate" floor (0.41) on *this* sample — check
the bootstrap CI floor and per-class precision/recall before letting a judge gate
anything. The gate architecture itself (pairwise / reference-anchored) is an open
owner decision (ENHANCEMENT_PROGRAM.md D5) and is deliberately not made here.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

Pair = Tuple[str, str]

# Below Landis-Koch "moderate" the judge must not gate anything (advisory only).
GATE_FLOOR = 0.41


def percent_agreement(pairs: Sequence[Pair]) -> float:
    if not pairs:
        return 0.0
    return sum(1 for h, j in pairs if h == j) / len(pairs)


def cohen_kappa(pairs: Sequence[Pair]) -> float:
    """Cohen's kappa for two raters, nominal labels."""
    n = len(pairs)
    if n == 0:
        return float("nan")
    po = percent_agreement(pairs)
    h_counts = Counter(h for h, _ in pairs)
    j_counts = Counter(j for _, j in pairs)
    cats = set(h_counts) | set(j_counts)
    pe = sum((h_counts[c] / n) * (j_counts[c] / n) for c in cats)
    if pe == 1.0:
        return 1.0  # degenerate: everyone in one category and they agree
    return (po - pe) / (1.0 - pe)


def krippendorff_alpha_nominal(pairs: Sequence[Pair]) -> float:
    """Krippendorff's alpha (nominal metric) for two coders, no missing data.

    Reduced form: alpha = 1 - (N_total - 1) * Do / De, where Do is the sum of
    off-diagonal coincidences and De the expected off-diagonal mass from the
    marginals.
    """
    n = len(pairs)
    if n == 0:
        return float("nan")
    coincidence: Counter = Counter()
    for a, b in pairs:
        coincidence[(a, b)] += 1
        coincidence[(b, a)] += 1
    n_c: Counter = Counter()
    for (c, _k), v in coincidence.items():
        n_c[c] += v
    n_total = sum(n_c.values())  # == 2 * n
    if n_total <= 1:
        return float("nan")
    do_sum = sum(v for (c, k), v in coincidence.items() if c != k)
    de_sum = sum(n_c[c] * n_c[k] for c in n_c for k in n_c if c != k)
    if de_sum == 0:
        return 1.0  # no expected disagreement possible -> perfect by convention
    return 1.0 - (n_total - 1) * do_sum / de_sum


def _ranks(values: List[float]) -> List[float]:
    """Fractional (average) ranks, for Spearman with ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_rho(pairs: Sequence[Pair]) -> Optional[float]:
    """Spearman rank correlation, if both columns parse as numeric; else None."""
    try:
        xs = [float(h) for h, _ in pairs]
        ys = [float(j) for _, j in pairs]
    except ValueError:
        return None
    n = len(xs)
    if n < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = math.sqrt(
        sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n))
    )
    if den == 0:
        return None
    return num / den


def bootstrap_kappa_ci(
    pairs: Sequence[Pair], iters: int, seed: int, lo: float = 2.5, hi: float = 97.5
) -> Optional[Dict[str, Any]]:
    n = len(pairs)
    if n < 2 or iters <= 0:
        return None
    rng = random.Random(seed)
    stats: List[float] = []
    for _ in range(iters):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        k = cohen_kappa(sample)
        if not math.isnan(k):
            stats.append(k)
    if not stats:
        return None
    stats.sort()

    def pct(p: float) -> float:
        idx = min(len(stats) - 1, max(0, int(round(p / 100.0 * (len(stats) - 1)))))
        return stats[idx]

    return {"lo": round(pct(lo), 4), "hi": round(pct(hi), 4), "level": f"{hi - lo:.0f}%"}


def landis_koch(kappa: float) -> str:
    if math.isnan(kappa):
        return "undefined"
    if kappa < 0.0:
        return "poor (worse than chance)"
    if kappa < 0.21:
        return "slight"
    if kappa < 0.41:
        return "fair"
    if kappa < 0.61:
        return "moderate"
    if kappa < 0.81:
        return "substantial"
    return "almost perfect"


def compute_agreement(
    pairs: Sequence[Pair], *, bootstrap: int = 2000, seed: int = 42
) -> Dict[str, Any]:
    """Full agreement report for (human, judge) label pairs.

    Same shape as the quality-loop plugin script output; deterministic for a
    fixed seed.
    """
    kappa = cohen_kappa(pairs)
    alpha = krippendorff_alpha_nominal(pairs)
    rho = spearman_rho(pairs)
    ci = bootstrap_kappa_ci(pairs, bootstrap, seed)
    can_gate = (not math.isnan(kappa)) and kappa >= GATE_FLOOR

    return {
        "n_items": len(pairs),
        "percent_agreement": round(percent_agreement(pairs), 4),
        "cohen_kappa": None if math.isnan(kappa) else round(kappa, 4),
        "kappa_interpretation": landis_koch(kappa),
        "kappa_ci": ci,
        "krippendorff_alpha_nominal": None if math.isnan(alpha) else round(alpha, 4),
        "spearman_rho": None if rho is None else round(rho, 4),
        "gate_floor": GATE_FLOOR,
        "may_gate": can_gate,
        "verdict": (
            f"kappa={kappa:.3f} ({landis_koch(kappa)}) — "
            + (
                "judge MAY gate (advisory→hard once stable)."
                if can_gate
                else "judge must NOT gate; advisory only until kappa >= 0.41."
            )
        ),
    }


def pairs_from_judge_records(records: Sequence[dict]) -> List[Pair]:
    """Map judged-record rows ({predicted: bool, gold: bool}) to label pairs.

    Human label first (gold), judge label second — the same convention as the
    CSV path. Rows missing either field are skipped (n is reported).
    """
    pairs: List[Pair] = []
    for record in records:
        if "gold" not in record or "predicted" not in record:
            continue
        human = "supported" if record["gold"] else "unsupported"
        judge = "supported" if record["predicted"] else "unsupported"
        pairs.append((human, judge))
    return pairs


def read_pairs_csv(path, human_col: str = "human", judge_col: str = "judge") -> List[Pair]:
    """Read (human, judge) label pairs from a CSV; rows with a missing label are skipped."""
    import csv

    pairs: List[Pair] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if (
            reader.fieldnames is None
            or human_col not in reader.fieldnames
            or judge_col not in reader.fieldnames
        ):
            raise ValueError(
                f"CSV must contain columns '{human_col}' and '{judge_col}'; got {reader.fieldnames}"
            )
        for row in reader:
            h = (row.get(human_col) or "").strip()
            j = (row.get(judge_col) or "").strip()
            if h == "" or j == "":
                continue
            pairs.append((h, j))
    return pairs
