"""Carryover "Open loops" items render cleanly on both surfaces.

Pure rendering contract (no store extra needed): a synthetic carryover item —
``source_ref.type == "carryover"``, no real evidence chunk, confidence at the
display threshold — must render on the markdown and Mattermost surfaces without
a broken source link, a stray confidence badge, or a crash.
"""

from __future__ import annotations

from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.config import MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.llm.schemas import Digest


def _carryover_digest():
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-06-19",
        trace_id="trace-1",
        sections=[
            {
                "title": "Open loops",
                "items": [
                    {
                        "title": 'Awaiting you 3d — "Q3 budget review"',
                        "evidence_id": "carryover:abc123def4567890",
                        "confidence": 0.7,  # at CONFIDENCE_DISPLAY_MAX → badge suppressed
                        "source_ref": {
                            "type": "carryover",
                            "msg_id": "urn:email:m-1",
                            "conversation_id": "thread-1",
                            "source": "email",
                            "age_days": 3,
                            "msg_count": 2,
                        },
                        "source_subject": "Q3 budget review",
                        "source_from": "Ivan Petrov",
                    }
                ],
            }
        ],
    )


def test_carryover_renders_in_markdown():
    content = MarkdownAssembler(language="en")._generate_markdown(_carryover_digest())
    assert "## Open loops" in content
    assert "Awaiting you 3d" in content
    assert "carryover" in content  # Source line: "**Source:** carryover, ..."
    assert "carryover:abc123def4567890" in content
    assert "Confidence" not in content  # 0.7 is at the threshold → suppressed


def test_carryover_renders_in_mattermost():
    text = MattermostDeliverer(MattermostDeliverConfig(), language="en")._format_digest(
        _carryover_digest()
    )
    assert "**Open loops**" in text
    assert "Awaiting you 3d" in text
    assert "confidence" not in text.lower()  # heuristic item → no confidence badge
    assert "↳" not in text  # no weak/seen sub-line


def test_carryover_section_title_localizes_to_russian():
    text = MattermostDeliverer(MattermostDeliverConfig(), language="ru")._format_digest(
        _carryover_digest()
    )
    # The section title is re-rendered from the canonical key, not the EN literal.
    assert "Открытые вопросы" in text


def _pending_digest():
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-06-19",
        trace_id="trace-1",
        sections=[
            {
                "title": "Awaiting your reply",
                "items": [
                    {
                        "title": 'Reply needed 3d — "Budget sign-off"',
                        "evidence_id": "pending:abc123def4567890",
                        "confidence": 0.7,
                        "source_ref": {
                            "type": "pending",
                            "msg_id": "urn:email:m-9",
                            "conversation_id": "thread-9",
                            "source": "email",
                            "age_days": 3,
                            "kind": "approval",
                        },
                        "source_subject": "Budget sign-off",
                        "source_from": "Ivan Petrov",
                    }
                ],
            }
        ],
    )


def test_pending_renders_in_markdown():
    content = MarkdownAssembler(language="en")._generate_markdown(_pending_digest())
    assert "## Awaiting your reply" in content
    assert "Reply needed 3d" in content
    assert "pending" in content  # Source line carries the type
    assert "Confidence" not in content


def test_pending_renders_in_mattermost():
    text = MattermostDeliverer(MattermostDeliverConfig(), language="en")._format_digest(
        _pending_digest()
    )
    assert "**Awaiting your reply**" in text
    assert "Reply needed 3d" in text
    assert "confidence" not in text.lower()


def test_pending_section_title_localizes_to_russian():
    text = MattermostDeliverer(MattermostDeliverConfig(), language="ru")._format_digest(
        _pending_digest()
    )
    assert "Ждут вашего ответа" in text
