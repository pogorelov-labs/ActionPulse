"""Agreement statistics for judge calibration (EP-5, frontier-audit F3).

Hand-computed expectations — the module must be exact and deterministic
(that is the point of vendoring a script instead of re-deriving in tokens).
"""

import pytest

from digest_core.eval.agreement import (
    GATE_FLOOR,
    bootstrap_kappa_ci,
    cohen_kappa,
    compute_agreement,
    krippendorff_alpha_nominal,
    pairs_from_judge_records,
    read_pairs_csv,
    spearman_rho,
)
from digest_core.eval.judge import compute_judge_metrics


def _imbalanced_pairs():
    """po=0.80, pe=0.52 → κ = 0.28/0.48 = 0.583333…"""
    return [("s", "s")] * 25 + [("s", "u")] * 5 + [("u", "s")] * 5 + [("u", "u")] * 15


def test_cohen_kappa_hand_computed():
    assert cohen_kappa(_imbalanced_pairs()) == pytest.approx(0.28 / 0.48, abs=1e-9)


def test_kappa_perfect_and_degenerate():
    assert cohen_kappa([("a", "a"), ("b", "b")]) == 1.0
    # degenerate: single category, full agreement → pe == 1.0 branch
    assert cohen_kappa([("a", "a"), ("a", "a")]) == 1.0


def test_krippendorff_alpha_hand_computed():
    # [(a,a),(a,b)]: Do=2, De=6, N_total=4 → α = 1 - 3*2/6 = 0.0
    assert krippendorff_alpha_nominal([("a", "a"), ("a", "b")]) == pytest.approx(0.0)
    assert krippendorff_alpha_nominal([("a", "a"), ("b", "b")]) == 1.0


def test_spearman_numeric_and_categorical():
    assert spearman_rho([("1", "1"), ("2", "2"), ("3", "3")]) == pytest.approx(1.0)
    assert spearman_rho([("pass", "pass"), ("fail", "fail")]) is None


def test_bootstrap_ci_is_deterministic_for_fixed_seed():
    pairs = _imbalanced_pairs()
    a = bootstrap_kappa_ci(pairs, iters=200, seed=42)
    b = bootstrap_kappa_ci(pairs, iters=200, seed=42)
    assert a == b
    assert a["lo"] <= a["hi"]


def test_compute_agreement_report_shape_and_gate():
    report = compute_agreement(_imbalanced_pairs(), bootstrap=100, seed=42)
    assert report["n_items"] == 50
    assert report["cohen_kappa"] == pytest.approx(0.5833, abs=1e-4)
    assert report["kappa_interpretation"] == "moderate"
    assert report["gate_floor"] == GATE_FLOOR
    assert report["may_gate"] is True  # floor only — the CI may still say "not yet"
    assert "verdict" in report

    weak = compute_agreement([("s", "u"), ("u", "s"), ("s", "u"), ("u", "s")], bootstrap=0)
    assert weak["may_gate"] is False


def test_pairs_from_judge_records_maps_and_skips():
    records = [
        {"predicted": True, "gold": True},
        {"predicted": False, "gold": True},
        {"predicted": True},  # missing gold → skipped
    ]
    assert pairs_from_judge_records(records) == [
        ("supported", "supported"),
        ("supported", "unsupported"),
    ]


def test_read_pairs_csv(tmp_path):
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text(
        "item,human,judge\ne1,pass,pass\ne2,fail,pass\ne3,,fail\n", encoding="utf-8"
    )
    assert read_pairs_csv(csv_path) == [("pass", "pass"), ("fail", "pass")]

    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain columns"):
        read_pairs_csv(bad)


def test_judge_metrics_carry_agreement_block():
    records = [
        {"predicted": True, "gold": True, "prob": 0.9, "lang": "ru"},
        {"predicted": True, "gold": False, "prob": 0.8, "lang": "ru"},
        {"predicted": False, "gold": False, "prob": 0.2, "lang": "en"},
        {"predicted": True, "gold": True, "prob": 0.95, "lang": "en"},
    ]
    out = compute_judge_metrics(records)
    # strata untouched
    assert out["ru"]["n"] == 2
    assert out["en"]["n"] == 2
    # reserved key with the full agreement report (judge-vs-gold, all records)
    assert out["agreement"]["n_items"] == 4
    assert 0.0 <= out["agreement"]["percent_agreement"] <= 1.0
    assert "may_gate" in out["agreement"]
