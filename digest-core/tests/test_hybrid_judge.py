"""Hybrid judge: reference-anchored eval + pairwise library (EP-5 step 3, D5).

Offline contract tests on a fake gateway — real verdict quality requires the
corp gateway (EP-14). The no-gate rule is structural here: `reference_eval`
returns a report, never an exit-influencing flag, and `eval.judge_mode`
defaults to "pointwise" (today's behavior) until calibration clears κ >= 0.41.
"""

import json

import pytest

from digest_core.config import Config, EvalConfig
from digest_core.eval.gold_set import GoldSet
from digest_core.eval.judge import (
    JUDGE_MODES,
    LLMJudge,
    ReferenceAnchoredJudge,
    make_judge,
    pairwise_judge,
    reference_eval,
)


class _FakeGateway:
    """gateway.judge(system, user) double driven by a callable."""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def judge(self, system_prompt, user_content):
        self.calls.append((system_prompt, user_content))
        return self.responder(system_prompt, user_content)


def _gold(rows):
    """rows: list of (trace_id, evidence_id, title, label, lang)."""
    labels, strata = {}, {}
    for trace_id, evidence_id, title, label, lang in rows:
        key = (trace_id, f"{evidence_id}|{title[:64]}")
        labels[key] = label
        strata[key] = lang
    return GoldSet(labels=labels, strata=strata)


def _digest_json(items):
    return {"sections": [{"title": "Мои действия", "items": items}]}


def _item(evidence_id, title, quote="вердикт по бюджету"):
    return {
        "evidence_id": evidence_id,
        "title": title,
        "evidence_spans": [{"msg_id": "m-1", "quote": quote}],
    }


# --- config -------------------------------------------------------------------


def test_judge_mode_defaults_to_pointwise():
    assert EvalConfig().judge_mode == "pointwise"
    assert Config().eval.judge_mode == "pointwise"


def test_fleet_flags_merge_from_yaml(monkeypatch, tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "reranker:\n  enabled: true\n  budget_per_run: 3\n"
        "judge:\n  enabled: true\n"
        "eval:\n  judge_mode: reference\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(config_yaml))
    config = Config()
    assert config.reranker.enabled is True
    assert config.reranker.budget_per_run == 3
    assert config.judge.enabled is True
    assert config.eval.judge_mode == "reference"


# --- factory ------------------------------------------------------------------


def test_make_judge_selects_architecture():
    gateway = _FakeGateway(lambda s, u: {"supported": True})
    assert isinstance(make_judge("pointwise", gateway), LLMJudge)
    assert isinstance(make_judge("reference", gateway), ReferenceAnchoredJudge)
    with pytest.raises(ValueError):
        make_judge("rubric", gateway)
    assert set(JUDGE_MODES) == {"pointwise", "reference"}


# --- reference-anchored judge ---------------------------------------------------


def test_reference_judge_parses_verdicts():
    gateway = _FakeGateway(lambda s, u: {"supported": True, "prob_supported": 0.9})
    judge = ReferenceAnchoredJudge(gateway)

    verdict = judge.judge_item("k", "title", "span", "body")
    assert verdict.supported is True and verdict.prob_supported == 0.9

    comparison = judge.judge_against_reference("k", "cand", "span", "ref")
    assert comparison.supported is True
    # Distinct prompts for the two jobs (verify vs compare).
    assert gateway.calls[0][0] != gateway.calls[1][0]
    assert "REFERENCE (human-approved): ref" in gateway.calls[1][1]


def test_reference_eval_pairs_by_evidence_and_reports():
    def responder(system_prompt, user_content):
        # Verify calls: support everything; compare calls: reject ev-2's candidate.
        is_compare = system_prompt.startswith("You compare a CANDIDATE")
        if is_compare and "CANDIDATE: новый заголовок" in user_content:
            return {"supported": False, "prob_supported": 0.2}
        return {"supported": True, "prob_supported": 0.95}

    gold = _gold(
        [
            ("t0", "ev-1", "согласовать бюджет", True, "ru"),
            ("t0", "ev-2", "прислать отчёт", True, "ru"),
            ("t0", "ev-3", "созвон в пятницу", False, "en"),
            ("t0", "ev-9", "пропавший пункт", True, "ru"),
        ]
    )
    digest = _digest_json(
        [
            _item("ev-1", "согласовать бюджет"),
            _item("ev-2", "новый заголовок"),
            _item("ev-3", "созвон в пятницу"),
            _item("ev-7", "без gold-метки"),  # not labeled -> not judged
        ]
    )
    judge = ReferenceAnchoredJudge(_FakeGateway(responder))
    report = reference_eval(digest, gold, judge)

    # 3 calibration records (ev-1, ev-2, ev-3) — ev-7 unlabeled, ev-9 missing.
    assert len(report["records"]) == 3
    assert {r["lang"] for r in report["records"]} == {"ru", "en"}
    assert all(
        set(r) >= {"item_key", "predicted", "gold", "prob", "lang"} for r in report["records"]
    )

    # Regression: 3 positive references; ev-1 matched, ev-2 regressed, ev-9 missing.
    assert report["regression"] == {
        "references": 3,
        "matched": 1,
        "regressed": 1,
        "missing": 1,
    }

    # Agreement block (κ/α drift trackers + may_gate floor) is wired in.
    assert "agreement" in report["metrics"]
    assert report["metrics"]["agreement"]["gate_floor"] == 0.41


def test_reference_eval_empty_gold_is_empty_report():
    judge = ReferenceAnchoredJudge(_FakeGateway(lambda s, u: {"supported": True}))
    report = reference_eval(_digest_json([_item("ev-1", "t")]), GoldSet({}, {}), judge)
    assert report["records"] == []
    assert report["metrics"] == {}
    assert report["regression"]["references"] == 0


# --- pairwise (EP-10 library) ----------------------------------------------------


def test_pairwise_consistent_winner():
    def responder(system_prompt, user_content):
        # Always prefer the content "good" wherever it appears.
        first = user_content.split("FIRST: ")[1].split("\n")[0]
        return {"winner": "first" if first == "good" else "second"}

    assert pairwise_judge(_FakeGateway(responder), "ctx", "good", "bad") == "a"
    assert pairwise_judge(_FakeGateway(responder), "ctx", "bad", "good") == "b"


def test_pairwise_position_bias_yields_tie():
    # A judge that always prefers whatever is shown FIRST flips on reversal -> tie.
    gateway = _FakeGateway(lambda s, u: {"winner": "first"})
    assert pairwise_judge(gateway, "ctx", "x", "y") == "tie"
    assert len(gateway.calls) == 2  # both orders were asked


def test_pairwise_explicit_tie_and_garbage():
    assert pairwise_judge(_FakeGateway(lambda s, u: {"winner": "tie"}), "c", "x", "y") == "tie"
    assert pairwise_judge(_FakeGateway(lambda s, u: {"winner": "🤷"}), "c", "x", "y") == "tie"


# --- CLI refusal path -------------------------------------------------------------


def test_eval_judge_run_refuses_pointwise_mode(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from digest_core.cli import app

    digest = tmp_path / "digest.json"
    digest.write_text(json.dumps(_digest_json([])), encoding="utf-8")
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps({"trace_id": "t", "evidence_id": "ev-1", "title": "x", "emoji": "+1"}) + "\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app,
        ["eval-judge-run", "--digest", str(digest), "--gold", str(gold), "--mode", "pointwise"],
    )
    assert result.exit_code == 1
    assert "reference" in (result.output + str(result.exception or ""))
