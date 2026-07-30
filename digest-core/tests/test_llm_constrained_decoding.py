"""A1 slice 3: the gateway can constrain generation to a JSON schema.

``build_json_schema_response_format`` turns any pydantic model into an OpenAI/vLLM
``response_format``, and ``_make_request_*`` thread it into the request payload. The
default stays ``{"type": "json_object"}``, so the live extract path is byte-identical
until A1.4 flips it on. This proves the mechanism (which removes the malformed/
off-schema class of failure the quality-retry exists to recover from) without
touching the live pipeline.
"""

import json
from unittest.mock import Mock

import pytest

from digest_core.config import LLMConfig
from digest_core.llm.gateway import (
    LLMGateway,
    build_extraction_response_format,
    build_json_schema_response_format,
)
from digest_core.llm.schemas import EnhancedDigestV3, _TraceBackbone


def _mock_response(content: str, *, prompt_tokens: int = 100, completion_tokens: int = 50) -> Mock:
    r = Mock()
    r.status_code = 200
    r.headers = {}
    r.raise_for_status = Mock()
    r.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
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


def _strict_violations(schema) -> list[str]:
    """Every way *schema* breaks OpenAI's strict structured-output contract.

    Strict mode is a contract on the schema, not an effort level: every object must
    set ``additionalProperties: false`` and name **all** its properties in
    ``required``.
    """
    bad: list[str] = []

    def walk(node, path):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            if node.get("additionalProperties") is not False:
                bad.append(f"{path}: additionalProperties is not false")
            missing = set(node["properties"]) - set(node.get("required", []))
            if missing:
                bad.append(f"{path}: not in required: {sorted(missing)}")
        for key in ("$defs", "properties", "definitions"):
            for name, sub in (node.get(key) or {}).items():
                walk(sub, f"{path}.{name}")
        walk(node.get("items"), f"{path}[]")
        for key in ("anyOf", "oneOf", "allOf"):
            for i, sub in enumerate(node.get(key) or []):
                walk(sub, f"{path}.{key}[{i}]")

    walk(schema, "root")
    return bad


def test_helper_builds_json_schema_block_from_pydantic_model():
    rf = build_json_schema_response_format(EnhancedDigestV3, name="digest_v3")
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "digest_v3"
    schema = rf["json_schema"]["schema"]
    # v3's typed sections survive into the constrained schema...
    assert "my_actions" in schema["properties"]
    # ...and so does the P2 traceability backbone grafted in A1.1 (evidence_spans).
    blob = json.dumps(schema)
    assert "ActionItemV3" in blob
    assert "evidence_spans" in blob


def test_strict_defaults_off_because_a_stock_schema_cannot_honour_it():
    """We must not advertise a contract the schema breaks.

    A stock `model_json_schema()` violates strict mode in a dozen places, so
    claiming `strict: true` over it invites a conforming server to 400 the request.
    vLLM guided decoding — the real target — needs only the schema.
    """
    rf = build_json_schema_response_format(EnhancedDigestV3)
    assert rf["json_schema"]["strict"] is False
    assert _strict_violations(rf["json_schema"]["schema"]), (
        "the stock schema is expected to be non-compliant — if this ever passes, "
        "pydantic changed and the strict default can be revisited"
    )


def test_strict_true_actually_makes_the_schema_comply():
    rf = build_json_schema_response_format(EnhancedDigestV3, strict=True)
    assert rf["json_schema"]["strict"] is True
    assert _strict_violations(rf["json_schema"]["schema"]) == []


def test_strict_shaped_payload_still_validates_into_the_model():
    """Strict mode forces every key to be present; the model must still accept it.

    Optionals stay expressible as explicit `null`, and list fields as `[]`, so a
    strict-mode response round-trips into `EnhancedDigestV3` without loosening it.
    """
    item = {
        "title": "Send the report",
        "description": "By Friday",
        "evidence_id": "ev-1",
        "quote": "please send the report",
        "due_date": None,
        "due_date_normalized": None,
        "due_date_label": None,
        "owners": [],
        "confidence": "High",
        "response_channel": None,
        # backbone fields the extractor does not fill, emitted explicitly under strict
        "evidence_spans": [{"msg_id": "msg-1", "quote": "please send the report"}],
        "citations": [],
        "citation_fidelity_ok": None,
        "support_score": None,
        "weak_evidence": None,
        "rank_score": None,
        "seen_before": None,
    }
    digest = EnhancedDigestV3.model_validate(
        {
            "schema_version": "3.0",
            "prompt_version": "extract_actions.v3",
            "digest_date": "2026-03-29",
            "trace_id": "t-1",
            "my_actions": [item],
            "others_actions": [],
            "deadlines_meetings": [],
            "risks_blockers": [],
            "fyi": [],
            "total_emails_processed": 1,
            "emails_with_actions": 1,
        }
    )
    assert digest.my_actions[0].evidence_spans[0].quote == "please send the report"
    assert digest.my_actions[0].citations == []


class TestExtractionSchemaProjection:
    """A1.2a — constrain on what the extractor produces, not on the whole model."""

    def test_downstream_only_fields_are_projected_out(self):
        schema = build_extraction_response_format(EnhancedDigestV3)["json_schema"]["schema"]
        props = set(schema["$defs"]["ActionItemV3"]["properties"])
        assert not (props & _TraceBackbone.DOWNSTREAM_ONLY), (
            "the extractor must never be asked for fields the pipeline computes: "
            f"{sorted(props & _TraceBackbone.DOWNSTREAM_ONLY)}"
        )

    def test_evidence_spans_is_kept(self):
        """The one backbone field the model *does* own — the root of the P2 chain."""
        schema = build_extraction_response_format(EnhancedDigestV3)["json_schema"]["schema"]
        for item_type in ("ActionItemV3", "DeadlineMeetingV3", "RiskBlockerV3", "FYIItemV3"):
            assert "evidence_spans" in schema["$defs"][item_type]["properties"], item_type

    def test_orphaned_defs_are_pruned_and_no_ref_dangles(self):
        """Dropping `citations` orphans `Citation`; leaving it implies the model needs it."""
        schema = build_extraction_response_format(EnhancedDigestV3)["json_schema"]["schema"]
        assert "Citation" not in schema["$defs"]
        assert "EvidenceSpan" in schema["$defs"]  # still reachable via evidence_spans

        def refs(node, acc):
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    acc.add(ref.rsplit("/", 1)[-1])
                for value in node.values():
                    refs(value, acc)
            elif isinstance(node, list):
                for value in node:
                    refs(value, acc)
            return acc

        assert refs(schema, set()) <= set(schema["$defs"])

    def test_required_stays_consistent_after_projection(self):
        """A required-but-absent property is an invalid schema."""
        schema = build_extraction_response_format(EnhancedDigestV3, strict=True)["json_schema"][
            "schema"
        ]
        for name, node in schema["$defs"].items():
            assert not set(node.get("required", [])) - set(node.get("properties", {})), name
        assert _strict_violations(schema) == []

    def test_projected_payload_still_validates_into_the_full_model(self):
        """The projection is a view, not a second model: v3 keeps one definition."""
        digest = EnhancedDigestV3.model_validate(
            {
                "schema_version": "3.0",
                "prompt_version": "extract_actions.v3",
                "digest_date": "2026-03-29",
                "trace_id": "t-1",
                "my_actions": [
                    {
                        "title": "Send the report",
                        "description": "By Friday",
                        "evidence_id": "ev-1",
                        "quote": "please send the report",
                        "owners": [],
                        "confidence": "High",
                        "evidence_spans": [{"msg_id": "msg-1", "quote": "please send the report"}],
                    }
                ],
                "others_actions": [],
                "deadlines_meetings": [],
                "risks_blockers": [],
                "fyi": [],
            }
        )
        item = digest.my_actions[0]
        assert item.evidence_spans[0].quote == "please send the report"
        # the downstream-owned fields keep their defaults, ready to be filled
        assert item.citations == []
        assert item.support_score is None and item.weak_evidence is None

    def test_projection_is_materially_smaller(self):
        """Not cosmetic: fewer keys per item against a hard 16384-token output cap."""
        full = build_json_schema_response_format(EnhancedDigestV3)["json_schema"]["schema"]
        ext = build_extraction_response_format(EnhancedDigestV3)["json_schema"]["schema"]
        assert len(json.dumps(ext)) < len(json.dumps(full)) * 0.75


def test_default_request_uses_json_object(gateway):
    """The live path is unchanged: no response_format threaded -> json_object mode."""
    gateway.client.post = Mock(return_value=_mock_response('{"sections": []}'))
    gateway.extract_actions([], "Return strict JSON", "trace")
    payload = gateway.client.post.call_args.kwargs["json"]
    assert payload["response_format"] == {"type": "json_object"}


def test_threaded_response_format_reaches_the_payload(gateway):
    """A schema response_format threaded into the request path lands in the payload."""
    gateway.client.post = Mock(return_value=_mock_response('{"sections": []}'))
    rf = build_json_schema_response_format(EnhancedDigestV3)
    gateway._make_request_with_retry(
        [{"role": "user", "content": "x"}], "trace", response_format=rf
    )
    payload = gateway.client.post.call_args.kwargs["json"]
    assert payload["response_format"]["type"] == "json_schema"
    assert "my_actions" in payload["response_format"]["json_schema"]["schema"]["properties"]
