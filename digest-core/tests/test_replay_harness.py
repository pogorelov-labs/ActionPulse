"""Replay + citation-fidelity harness and the de-circularised evidence-id eval (PR7)."""

import json

from digest_core.eval.corpus import CORPUS_DIR, load_corpus
from digest_core.eval.prompt_eval import _extract_evidence_ids, _parse_evidence_ids_from_text
from digest_core.eval.replay_harness import compare_metrics, compute_metrics, evaluate_corpus

# --- metrics ---------------------------------------------------------------


def test_compute_metrics_counts_items_and_span_coverage():
    digest = {
        "sections": [
            {
                "title": "Мои действия",
                "items": [
                    {"evidence_id": "ev_a", "evidence_spans": [{"msg_id": "m", "quote": "q"}]},
                    {"evidence_id": "ev_b", "evidence_spans": []},
                ],
            }
        ]
    }
    metrics = compute_metrics(digest, {"ev_a", "ev_b"})
    assert metrics["item_count"] == 2
    assert metrics["span_coverage"] == 0.5
    assert metrics["evidence_ids_unverifiable"] == 0
    assert metrics["evidence_ids_checked"] is True


def test_compute_metrics_flags_unverifiable_ids():
    digest = {"sections": [{"title": "X", "items": [{"evidence_id": "ev_ghost"}]}]}
    metrics = compute_metrics(digest, {"ev_real"})
    assert metrics["evidence_ids_unverifiable"] == 1


def test_compare_metrics_detects_regressions():
    baseline = {"item_count": 2, "span_coverage": 1.0, "evidence_ids_unverifiable": 0}
    assert compare_metrics(baseline, baseline) == []

    worse = {"item_count": 1, "span_coverage": 0.5, "evidence_ids_unverifiable": 1}
    assert len(compare_metrics(worse, baseline)) == 3


# --- corpus replay ---------------------------------------------------------


def test_committed_corpus_passes_its_baseline(tmp_path):
    cases = load_corpus()
    assert cases, "committed synthetic corpus must exist"

    ok, reports = evaluate_corpus(cases, tmp_path)

    assert ok, reports
    for report in reports:
        assert report["ok"], report["regressions"]


def test_corpus_metrics_match_committed_baseline(tmp_path):
    cases = load_corpus()
    _, reports = evaluate_corpus(cases, tmp_path)
    for report in reports:
        baseline = json.loads(
            (CORPUS_DIR / f"{report['case']}.baseline.json").read_text(encoding="utf-8")
        )
        assert report["metrics"]["item_count"] == baseline["item_count"]
        assert report["metrics"]["span_coverage"] == baseline["span_coverage"]


# --- de-circularised evidence-id eval --------------------------------------


def test_extract_evidence_ids_is_not_circular_from_output():
    # Output-only snapshot -> NO ids (deriving from the output would be circular).
    snapshot = {"responses": [{"data": {"sections": [{"items": [{"evidence_id": "ev_x"}]}]}}]}
    assert _extract_evidence_ids(snapshot) == set()


def test_extract_evidence_ids_from_recorded_input_messages():
    snapshot = {"responses": [{"messages": [{"content": "Evidence 1 (ID: ev_abc123, Msg: m1)"}]}]}
    assert "ev_abc123" in _extract_evidence_ids(snapshot)


def test_parse_evidence_ids_matches_pr1_and_legacy():
    assert _parse_evidence_ids_from_text("Evidence 1 (ID: ev_deadbeef12345678, Msg: m)") == {
        "ev_deadbeef12345678"
    }
    assert "ev-abc-123" in _parse_evidence_ids_from_text("ID: ev-abc-123, Msg: m")
