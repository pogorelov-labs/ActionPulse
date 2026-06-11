"""Best-of-N extraction selected by the citation gate (EP-10, D5/D6).

Selector contract (gate metric first, deterministic ties, pairwise judge only
on exact ties), the offline acceptance proof over the replay corpus, and the
run.py seam (flag off by default = today's single-shot path, byte-identical;
every sampling failure degrades back to N=1).
"""

from types import SimpleNamespace

import pytest

import digest_core.run as run_module
from digest_core.config import ExtractConfig, LLMConfig
from digest_core.evidence.citation_gate import support_recall
from digest_core.llm.best_of_n import select_best_candidate
from digest_core.llm.rate_broker import StageCallBudgetExceeded
from digest_core.llm.schemas import Digest, EvidenceSpan, Item, Section

BODY = "Пожалуйста, пришли отчёт до пятницы и согласуй бюджет."
MSG_MAP = {"m-1": BODY}


def _digest(quotes):
    items = [
        Item(
            title=f"Пункт {i + 1}",
            evidence_id=f"ev-{i}",
            confidence=0.8,
            source_ref={"type": "email", "msg_id": "m-1"},
            evidence_spans=[EvidenceSpan(msg_id="m-1", quote=quote)],
        )
        for i, quote in enumerate(quotes)
    ]
    return Digest(
        schema_version="1.0",
        prompt_version="v",
        digest_date="d",
        trace_id="t",
        sections=[Section(title="Мои действия", items=items)],
    )


GOOD = "пришли отчёт до пятницы"
BAD = "этого нет в письме"


# --- selector ------------------------------------------------------------------


def test_selector_picks_max_recall():
    selected, scores = select_best_candidate(
        [_digest([GOOD, BAD]), _digest([GOOD, GOOD]), _digest([BAD, BAD])], MSG_MAP
    )
    assert selected == 1
    assert [s.support_recall for s in scores] == [0.5, 1.0, 0.0]


def test_selector_tie_prefers_deterministic_candidate():
    selected, scores = select_best_candidate(
        [_digest([GOOD]), _digest([GOOD]), _digest([GOOD])], MSG_MAP
    )
    assert selected == 0  # candidate 0 = temp-0 shot; an all-tie keeps today's digest


def test_selector_requires_candidates():
    with pytest.raises(ValueError):
        select_best_candidate([], MSG_MAP)


def test_selector_judge_breaks_exact_tie():
    class PreferSecondContent:
        """Consistently prefers the candidate whose first item is 'Пункт 1'... by content."""

        def judge(self, system_prompt, user_content):
            first = user_content.split("FIRST: ")[1].split("\n")[0]
            return {"winner": "first" if "vs" in first else "second"}

    # Identical gate scores; judge consistently prefers the challenger's content.
    a, b = _digest([GOOD]), _digest([GOOD])
    b.sections[0].items[0].title = "Пункт vs"  # marker the fake judge prefers
    selected, _ = select_best_candidate([a, b], MSG_MAP, judge_gateway=PreferSecondContent())
    assert selected == 1


def test_selector_judge_position_bias_keeps_deterministic():
    class FirstShownWins:
        def judge(self, system_prompt, user_content):
            return {"winner": "first"}

    selected, _ = select_best_candidate(
        [_digest([GOOD]), _digest([GOOD])], MSG_MAP, judge_gateway=FirstShownWins()
    )
    assert selected == 0  # order-flip -> tie -> deterministic candidate


def test_selector_judge_failure_degrades_to_deterministic():
    class Boom:
        def judge(self, system_prompt, user_content):
            raise RuntimeError("offline")

    selected, _ = select_best_candidate(
        [_digest([GOOD]), _digest([GOOD])], MSG_MAP, judge_gateway=Boom()
    )
    assert selected == 0


def test_selector_never_consulted_judge_when_gate_decides():
    class MustNotBeCalled:
        def judge(self, *args):
            raise AssertionError("the gate's verdict is never overridden by the judge")

    selected, _ = select_best_candidate(
        [_digest([BAD]), _digest([GOOD])], MSG_MAP, judge_gateway=MustNotBeCalled()
    )
    assert selected == 1


def test_support_recall_alias_unchanged():
    digest = _digest([GOOD, BAD])
    select_best_candidate([digest], MSG_MAP)  # annotates via the gate
    assert support_recall(digest) == run_module._support_recall(digest)


# --- offline acceptance proof (EP-10) --------------------------------------------


def test_offline_proof_on_replay_corpus():
    from digest_core.eval.best_of_n_harness import evaluate_corpus_best_of_n
    from digest_core.eval.corpus import load_corpus

    ok, reports = evaluate_corpus_best_of_n(load_corpus())
    assert ok and reports
    for report in reports:
        # The selector recovers the verbatim candidate from a degraded N=1 shot.
        assert report["support_recall_best"] == 1.0
        assert report["support_recall_best"] > report["support_recall_n1"]
        assert report["selected"] == 1  # the fully-verbatim candidate


# --- run.py seam ------------------------------------------------------------------


class _NullMetrics:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _fake_gateway_factory(constructed, responder):
    class FakeGateway:
        def __init__(self, config, **kwargs):
            constructed.append(config.temperature)
            self.config = config
            self.last_request_meta = {}

        def extract_actions(self, evidence, prompt_template, trace_id):
            return responder(self.config.temperature)

        def get_request_stats(self):
            return {"model": self.config.model, "last_latency_ms": 0, "timeout_s": 0}

        def close(self):
            pass

    return FakeGateway


def _ctx(extract_cfg, *, replay_llm=None):
    return SimpleNamespace(
        config=SimpleNamespace(
            llm=LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b"),
            report=SimpleNamespace(language="en"),
            extract=extract_cfg,
        ),
        metrics=_NullMetrics(),
        record_llm=None,
        replay_llm=replay_llm,
        rate_broker=None,
        digest_date="2026-03-29",
        trace_id="t",
        run_meta={"stage_durations_ms": {}},
    )


def _evidence():
    return [SimpleNamespace(evidence_id="ev-0", priority_score=1.0)]


def _messages():
    return [SimpleNamespace(msg_id="m-1", text_body=BODY)]


def _sections(quote):
    return [
        {
            "title": "Мои действия",
            "items": [
                {
                    "title": "Пункт",
                    "evidence_id": "ev-0",
                    "confidence": 0.8,
                    "source_ref": {"type": "email", "msg_id": "m-1"},
                    "evidence_spans": [{"msg_id": "m-1", "quote": quote}],
                }
            ],
        }
    ]


def test_flag_off_is_single_shot(monkeypatch):
    constructed = []
    monkeypatch.setattr(
        run_module,
        "LLMGateway",
        _fake_gateway_factory(constructed, lambda temp: {"sections": _sections(GOOD)}),
    )
    ctx = _ctx(ExtractConfig())  # best_of_n=1 default
    digest, err = run_module._stage_llm(ctx, _evidence(), _messages())
    assert err is None
    assert constructed == [0.0]  # exactly one gateway, deterministic temperature
    assert "best_of_n" not in ctx.run_meta


def test_flag_on_selects_better_sample(monkeypatch):
    constructed = []

    def responder(temp):
        # Deterministic shot extracts a non-verbatim span; samples are verbatim.
        return {"sections": _sections(BAD if temp == 0.0 else GOOD)}

    monkeypatch.setattr(run_module, "LLMGateway", _fake_gateway_factory(constructed, responder))
    ctx = _ctx(ExtractConfig(best_of_n=3, sample_temperature=0.7))
    digest, err = run_module._stage_llm(ctx, _evidence(), _messages())

    assert err is None
    assert constructed == [0.0, 0.7]  # one primary + ONE reused sample gateway
    meta = ctx.run_meta["best_of_n"]
    assert meta["n_candidates"] == 3
    assert meta["selected"] in (1, 2)
    assert digest.sections[0].items[0].evidence_spans[0].quote == GOOD


def test_flag_on_tie_keeps_deterministic_digest(monkeypatch):
    constructed = []

    def responder(temp):
        return {"sections": _sections(GOOD)}  # all candidates identical

    monkeypatch.setattr(run_module, "LLMGateway", _fake_gateway_factory(constructed, responder))
    ctx = _ctx(ExtractConfig(best_of_n=2))
    digest, err = run_module._stage_llm(ctx, _evidence(), _messages())
    assert ctx.run_meta["best_of_n"]["selected"] == 0


def test_sampling_failure_degrades_to_primary(monkeypatch):
    constructed = []

    def responder(temp):
        if temp == 0.0:
            return {"sections": _sections(GOOD)}
        raise StageCallBudgetExceeded("extractor", 2)

    monkeypatch.setattr(run_module, "LLMGateway", _fake_gateway_factory(constructed, responder))
    ctx = _ctx(ExtractConfig(best_of_n=3))
    digest, err = run_module._stage_llm(ctx, _evidence(), _messages())
    assert err is None
    assert "best_of_n" not in ctx.run_meta  # zero samples gathered -> pure N=1 path
    assert digest.sections[0].items[0].evidence_spans[0].quote == GOOD


def test_sampling_disabled_under_replay(monkeypatch, tmp_path):
    constructed = []
    monkeypatch.setattr(
        run_module,
        "LLMGateway",
        _fake_gateway_factory(constructed, lambda temp: {"sections": _sections(GOOD)}),
    )
    ctx = _ctx(ExtractConfig(best_of_n=3), replay_llm=str(tmp_path / "rec.json"))
    digest, err = run_module._stage_llm(ctx, _evidence(), _messages())
    assert constructed == [0.0]  # no sample gateway under --replay-llm
    assert "best_of_n" not in ctx.run_meta


def test_extract_config_merges_from_yaml(monkeypatch, tmp_path):
    from digest_core.config import Config

    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "extract:\n  best_of_n: 3\n  sample_temperature: 0.5\n", encoding="utf-8"
    )
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(config_yaml))
    config = Config()
    assert config.extract.best_of_n == 3
    assert config.extract.sample_temperature == 0.5


def test_extract_defaults_off():
    assert ExtractConfig().best_of_n == 1
