"""A1.2 — the v3 extraction prompts must agree with the v3 schema.

A prompt and a schema that drift apart fail in the worst way: the model produces
exactly what it was asked for, guided decoding rejects it (or silently drops the
field), and the digest quietly loses items. These tests are cheap and pin the
agreement in both directions — every list the prompt promises exists in the
schema, and every field the pipeline owns is absent from the prompt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from digest_core.llm.gateway import build_extraction_response_format
from digest_core.llm.prompt_registry import PROMPT_TEMPLATE_MAP, get_prompt_template_path
from digest_core.llm.schemas import EnhancedDigestV3, _TraceBackbone

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
V3_PROMPTS = ["extract_actions.v3", "extract_actions.en.v3"]

#: The five typed lists that ARE the model's job.
V3_LISTS = ("my_actions", "others_actions", "deadlines_meetings", "risks_blockers", "fyi")


def _prompt_text(key: str) -> str:
    return (PROMPTS_DIR / get_prompt_template_path(key)).read_text(encoding="utf-8")


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_prompt_is_registered_and_present_on_disk(key):
    assert key in PROMPT_TEMPLATE_MAP
    path = PROMPTS_DIR / get_prompt_template_path(key)
    assert path.is_file(), f"{key} maps to a file that does not exist: {path}"
    assert path.read_text(encoding="utf-8").strip(), "prompt is empty"


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_prompt_names_every_typed_list(key):
    text = _prompt_text(key)
    for name in V3_LISTS:
        assert name in text, f"{key} never mentions the {name!r} list"


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_prompt_never_asks_for_pipeline_owned_fields(key):
    """The prompt must not contradict the projected schema.

    Asking for `trace_id` or `markdown_summary` invites the model to fabricate run
    metadata (or to write the report, against ADR-001) — and guided decoding on the
    projected schema would reject it anyway.
    """
    text = _prompt_text(key)
    # Match the JSON-key form (`"timezone":`), not the bare word — the prompt
    # legitimately says "ISO-8601 with timezone" when explaining date formatting,
    # which is guidance, not a field request. Naming the field as a key is what
    # would teach the model to emit it.
    for field in sorted(EnhancedDigestV3.DOWNSTREAM_ONLY):
        assert f'"{field}"' not in text, (
            f"{key} uses {field!r} as a JSON key; it is pipeline-owned and is "
            "projected out of the extraction schema"
        )


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_prompt_never_asks_for_downstream_item_fields(key):
    """Same at item level: citations and the gate annotations are computed."""
    text = _prompt_text(key)
    for field in sorted(_TraceBackbone.DOWNSTREAM_ONLY):
        assert field not in text, (
            f"{key} mentions {field!r} — the extractor must never be asked for it "
            "(CitationBuilder / the shadow gate own it)"
        )


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_prompt_demands_verbatim_evidence_spans(key):
    """P2 is golden rule #1; the prompt has to state it, not imply it."""
    text = _prompt_text(key)
    assert "evidence_spans" in text
    assert "msg_id" in text
    lowered = text.lower()
    assert "verbatim" in lowered or "дословно" in lowered


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_prompt_carries_the_injection_warning(key):
    """C11: evidence bodies are attacker-influenceable; say so in the prompt too."""
    lowered = _prompt_text(key).lower()
    assert "ignore previous instructions" in lowered or "игнорируй предыдущие" in lowered


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_every_json_example_in_the_prompt_validates_against_the_model(key):
    """The examples are the highest-leverage part of the prompt — and the easiest
    to get subtly wrong. Parse every ``{...}`` block that looks like a full digest
    and validate it, so a bad example fails here instead of teaching the model a
    shape the schema rejects.
    """
    text = _prompt_text(key)
    blocks, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                blocks.append(text[start : i + 1])

    digests = []
    for block in blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue  # the shape template up top uses placeholders, not valid JSON
        if isinstance(payload, dict) and set(V3_LISTS) <= set(payload):
            digests.append(payload)

    assert len(digests) >= 3, f"{key}: expected the few-shot examples, found {len(digests)}"
    for payload in digests:
        # run.py supplies the metadata the extractor is forbidden to emit
        model = EnhancedDigestV3.model_validate(
            {**payload, "digest_date": "2026-03-29", "trace_id": "t-1"}
        )
        for name in V3_LISTS:
            for item in getattr(model, name):
                assert item.evidence_spans, f"{key}: example item {item.title!r} has no span"
                # the item-level `quote` must equal the first span (prompt rule)
                assert item.quote == item.evidence_spans[0].quote, (
                    f"{key}: example item {item.title!r} — quote does not match its "
                    "first evidence_span"
                )


@pytest.mark.parametrize("key", V3_PROMPTS)
def test_example_items_use_only_fields_the_projected_schema_allows(key):
    """An example key the schema drops would teach the model to emit a rejected field."""
    schema = build_extraction_response_format(EnhancedDigestV3)["json_schema"]["schema"]
    allowed = {
        name: set(node.get("properties", {})) for name, node in schema.get("$defs", {}).items()
    }
    by_list = {
        "my_actions": "ActionItemV3",
        "others_actions": "ActionItemV3",
        "deadlines_meetings": "DeadlineMeetingV3",
        "risks_blockers": "RiskBlockerV3",
        "fyi": "FYIItemV3",
    }

    text = _prompt_text(key)
    for match in re.finditer(r'"(\w+)":\s*\[\s*\{', text):
        list_name = match.group(1)
        if list_name not in by_list:
            continue
        depth, start = 0, match.end() - 1
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
        try:
            item = json.loads(text[start : i + 1])
        except json.JSONDecodeError:
            continue  # placeholder block in the shape template
        unknown = set(item) - allowed[by_list[list_name]]
        assert not unknown, f"{key}: {list_name} example uses fields not in the schema: {unknown}"
