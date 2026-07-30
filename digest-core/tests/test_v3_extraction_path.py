"""A1.4 — the v3 extraction contract, wired end-to-end through run.py.

The adapter is unit-tested in `test_v3_adapter`; this file proves the *wiring*:
that `extract.contract="v3"` selects the v3 prompt, sends a constrained
`response_format`, never spends the quality retry, and produces a digest the rest
of the pipeline can consume unchanged — while `contract="v1"` stays byte-identical
to today.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from digest_core.assemble.labels import MY_ACTIONS, URGENT, normalize_section
from digest_core.config import LLMConfig
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.gateway import LLMGateway

V3_RESPONSE = {
    "my_actions": [
        {
            "title": "Send comments on the Orion NDA",
            "description": "Legal needs comments and final approval.",
            "evidence_id": "ev-1",
            "quote": "пришли комментарии",
            "due_date": "2026-03-30",
            "owners": [],
            "confidence": "High",
            "evidence_spans": [{"msg_id": "msg-1", "quote": "пришли комментарии"}],
        }
    ],
    "others_actions": [],
    "deadlines_meetings": [],
    "risks_blockers": [
        {
            "title": "Billing integration blocked",
            "evidence_id": "ev-1",
            "quote": "заблокирована",
            "severity": "High",
            "impact": "release slips",
            "evidence_spans": [{"msg_id": "msg-1", "quote": "заблокирована"}],
        }
    ],
    "fyi": [],
}


def _chunk():
    return EvidenceChunk(
        evidence_id="ev-1",
        conversation_id="conv-1",
        content="пришли комментарии ... заблокирована",
        source_ref={"type": "email", "msg_id": "msg-1", "conversation_id": "conv-1"},
    )


def _mock_response(payload) -> Mock:
    r = Mock()
    r.status_code = 200
    r.headers = {}
    r.raise_for_status = Mock()
    r.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    return r


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("LLM_TOKEN", "test-token")
    return LLMGateway(
        LLMConfig(
            endpoint="https://example.invalid/v1/chat/completions",
            model="qwen35-397b-a17b",
            timeout_s=30,
        )
    )


class TestGatewayV3Call:
    def test_request_carries_the_projected_schema(self, gateway):
        gateway.client.post = Mock(return_value=_mock_response(V3_RESPONSE))
        gateway.extract_actions_v3([_chunk()], "prompt", "trace")

        payload = gateway.client.post.call_args.kwargs["json"]
        rf = payload["response_format"]
        assert rf["type"] == "json_schema"
        schema = rf["json_schema"]["schema"]
        # projected: only the five typed lists, no pipeline-owned metadata
        assert set(schema["properties"]) == {
            "my_actions",
            "others_actions",
            "deadlines_meetings",
            "risks_blockers",
            "fyi",
        }
        # and evidence_spans is mandatory, so P2 holds by construction
        action = schema["$defs"]["ActionItemV3"]
        assert "evidence_spans" in action["required"]
        assert action["properties"]["evidence_spans"]["minItems"] == 1

    def test_no_quality_retry_on_an_empty_extraction(self, gateway):
        """v1 spends a second call when `sections` is empty. v3 must not: an empty
        result from a constrained decode is a real answer, not a format failure."""
        empty = {k: [] for k in V3_RESPONSE}
        gateway.client.post = Mock(return_value=_mock_response(empty))
        gateway.extract_actions_v3([_chunk()], "prompt", "trace")
        assert gateway.client.post.call_count == 1

    def test_returns_the_raw_payload_without_run_metadata(self, gateway):
        gateway.client.post = Mock(return_value=_mock_response(V3_RESPONSE))
        out = gateway.extract_actions_v3([_chunk()], "prompt", "trace")
        assert "my_actions" in out
        # the gateway must not invent digest_date / trace_id — run.py owns those
        assert "digest_date" not in out and "trace_id" not in out


class TestStageWiring:
    """`_stage_llm` with contract=v3 produces a normal Digest for the rest of the run."""

    def _run_stage(self, monkeypatch, contract, response):
        from digest_core import run as runner

        cfg = runner.Config()
        cfg.extract.contract = contract
        cfg.report.language = "en"

        ctx = Mock()
        ctx.config = cfg
        ctx.digest_date = "2026-03-29"
        ctx.trace_id = "t-1"
        ctx.run_meta = {}
        ctx.metrics = Mock()
        ctx.sink = None
        ctx.record_llm = None
        ctx.replay_llm = None
        ctx.rate_broker = None

        monkeypatch.setenv("LLM_TOKEN", "test-token")
        fake = Mock()
        fake.get_request_stats.return_value = {}
        fake.last_request_meta = {}
        fake.extract_actions_v3.return_value = response
        fake.extract_actions.return_value = response
        monkeypatch.setattr(runner, "LLMGateway", lambda *a, **k: fake)
        monkeypatch.setattr(runner, "_emit", lambda *a, **k: None)
        monkeypatch.setattr(runner, "_finish_stage", lambda *a, **k: None)

        digest, err = runner._stage_llm(ctx, [_chunk()])
        return digest, err, ctx, fake

    def test_v3_contract_calls_the_constrained_method_and_adapts(self, monkeypatch):
        digest, err, ctx, fake = self._run_stage(monkeypatch, "v3", dict(V3_RESPONSE))
        assert err is None
        assert fake.extract_actions_v3.called
        assert not fake.extract_actions.called

        keys = [normalize_section(s.title) for s in digest.sections]
        # High-severity risk leads, then my actions
        assert keys == [URGENT, MY_ACTIONS]
        # source_ref came from the chunk, not the model
        assert digest.sections[0].items[0].source_ref["msg_id"] == "msg-1"
        # drop accounting is recorded even when nothing was dropped
        assert ctx.run_meta["extract_v3"]["items"] == 2
        assert ctx.run_meta["extract_v3"]["dropped_unknown_evidence_id"] == 0

    def test_v1_contract_is_untouched(self, monkeypatch):
        v1_response = {
            "sections": [
                {
                    "title": "My actions",
                    "items": [
                        {
                            "title": "Send comments",
                            "evidence_id": "ev-1",
                            "confidence": 0.9,
                            "source_ref": {"type": "email", "msg_id": "msg-1"},
                        }
                    ],
                }
            ]
        }
        digest, err, ctx, fake = self._run_stage(monkeypatch, "v1", v1_response)
        assert err is None
        assert fake.extract_actions.called
        assert not fake.extract_actions_v3.called
        assert "extract_v3" not in ctx.run_meta
        assert [normalize_section(s.title) for s in digest.sections] == [MY_ACTIONS]


class TestPromptSelection:
    @pytest.mark.parametrize(
        "language,contract,expected",
        [
            ("en", "v1", "extract_actions.en.v2"),
            ("en", "v3", "extract_actions.en.v3"),
            ("ru", "v3", "extract_actions.v3"),
        ],
    )
    def test_contract_and_language_pick_the_prompt(self, language, contract, expected):
        from digest_core.run import _load_extract_prompt

        version, text = _load_extract_prompt("qwen35-397b-a17b", language, contract)
        assert version == expected
        assert text.strip()

    def test_v3_prompts_ask_for_the_typed_lists(self):
        from digest_core.run import _load_extract_prompt

        _, text = _load_extract_prompt("qwen35-397b-a17b", "en", "v3")
        for name in ("my_actions", "others_actions", "deadlines_meetings", "risks_blockers"):
            assert name in text


def test_contract_defaults_to_v1_so_the_live_path_is_unchanged():
    from digest_core.config import Config

    assert Config().extract.contract == "v1"
