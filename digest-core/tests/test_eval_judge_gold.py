"""Gold-set, judge metrics, and tau calibration (PR10) — all offline/pure."""

import json

from digest_core.eval.calibrate import calibrate, calibrate_stratum
from digest_core.eval.gold_set import item_key, load_gold_jsonl
from digest_core.eval.judge import compute_judge_metrics


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


# --- gold set ---------------------------------------------------------------


def test_gold_load_label_and_stats(tmp_path):
    path = _jsonl(
        tmp_path / "reactions.jsonl",
        [
            {"trace_id": "t1", "evidence_id": "ev_a", "title": "A", "emoji": "+1", "lang": "ru"},
            {"trace_id": "t1", "evidence_id": "ev_b", "title": "B", "emoji": ":x:", "lang": "en"},
            {"trace_id": "t1", "evidence_id": "ev_c", "title": "C", "emoji": "shrug"},  # ignored
        ],
    )
    gold = load_gold_jsonl(path)
    assert gold.stats() == {"total": 2, "positive": 1, "negative": 1}
    assert gold.label("t1", item_key("ev_a", "A")) is True
    assert gold.label("t1", item_key("ev_b", "B")) is False
    assert gold.stratum("t1", item_key("ev_b", "B")) == "en"
    assert gold.label("t1", "missing") is None


def test_gold_last_reaction_wins(tmp_path):
    path = _jsonl(
        tmp_path / "r.jsonl",
        [
            {"trace_id": "t", "item_key": "k", "emoji": "+1"},
            {"trace_id": "t", "item_key": "k", "emoji": "-1"},
        ],
    )
    assert load_gold_jsonl(path).label("t", "k") is False


# --- calibration ------------------------------------------------------------


def test_calibrate_stratum_finds_tau_at_recall():
    scored = [(0.2, True), (0.4, True), (0.6, True), (0.8, True), (1.0, True), (0.1, False)]
    result = calibrate_stratum(scored, target_recall=0.8)
    assert result["tau"] == 0.4  # keeps 4/5 positives (>= 0.8 recall)
    assert result["recall"] >= 0.8
    assert result["usable"] is True


def test_calibrate_stratum_no_positives_unusable():
    result = calibrate_stratum([(0.5, False)], target_recall=0.9)
    assert result["usable"] is False
    assert result["n_pos"] == 0


def test_calibrate_gates_only_when_well_sampled():
    gated = calibrate(
        {"ru": [(0.9, True)] * 25, "en": [(0.9, True)] * 25}, target_recall=0.9, min_samples=20
    )
    assert gated["gates_p8"] is True

    undersampled = calibrate({"ru": [(0.9, True)] * 5}, min_samples=20)
    assert undersampled["gates_p8"] is False
    assert undersampled["strata"]["ru"]["low_confidence"] is True


def test_calibrate_empty_does_not_gate():
    assert calibrate({}, min_samples=1)["gates_p8"] is False


# --- judge metrics ----------------------------------------------------------


def test_judge_metrics_prf_hallucination_brier():
    records = [
        {"predicted": True, "gold": True, "prob": 0.9, "lang": "ru"},
        {"predicted": True, "gold": False, "prob": 0.8, "lang": "ru"},  # FP / hallucination
        {"predicted": False, "gold": True, "prob": 0.2, "lang": "ru"},  # FN
        {"predicted": False, "gold": False, "prob": 0.1, "lang": "ru"},  # TN
    ]
    metrics = compute_judge_metrics(records)["ru"]
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["hallucination_rate"] == 0.5
    assert metrics["n"] == 4
    assert 0.0 <= metrics["brier"] <= 1.0


def test_judge_metrics_stratifies_by_lang():
    records = [
        {"predicted": True, "gold": True, "prob": 1.0, "lang": "ru"},
        {"predicted": True, "gold": True, "prob": 1.0, "lang": "en"},
    ]
    metrics = compute_judge_metrics(records)
    assert set(metrics) == {"ru", "en", "agreement"}  # "agreement" is the reserved EP-5 key
    assert metrics["en"]["f1"] == 1.0
