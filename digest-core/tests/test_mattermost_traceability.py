"""Recipient-facing Mattermost delivery (owner decision C5/C8).

The delivered message strips operator metadata: no ``ev: <id>`` token, no local
``[json](...)`` link, and no trace/budget footer. What survives is recipient
signal — the ``⚠ слабое обоснование`` (weak evidence) and ``↻ повтор`` (repeat)
badges. The evidence ids / json paths / budget remain in the persisted run
artifacts, just not in the message a human receives.
"""

from types import SimpleNamespace

from digest_core.config import MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.llm.schemas import Digest


def _deliverer(language: str = "ru"):
    return MattermostDeliverer(MattermostDeliverConfig(), language=language)


def _digest(items):
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-03-29",
        trace_id="trace-1",
        sections=[{"title": "Мои действия", "items": items}],
    )


def test_delivered_message_strips_operator_metadata():
    # Even with a json_path threaded for compat, the message must not leak the
    # internal evidence id nor the local operator filesystem link.
    digest = _digest(
        [
            {
                "title": "Подготовить отчёт",
                "due": "2026-03-30",
                "evidence_id": "ev_abc123def456",
                "confidence": 0.9,
                "source_ref": {"type": "email", "msg_id": "m-1"},
            }
        ]
    )
    text = _deliverer()._format_digest(digest, "/out/digest-2026-03-29.json")

    assert "ev:" not in text
    assert "ev_abc123def456" not in text
    assert "[json]" not in text
    assert "Источники" not in text
    # A plain item (no badges) gets no sub-line at all.
    assert "↳" not in text


def test_weak_evidence_item_keeps_weak_badge():
    digest = _digest(
        [
            {
                "title": "Сделать X",
                "due": None,
                "evidence_id": "ev_xyz",
                "confidence": 0.6,
                "weak_evidence": True,
                "source_ref": {"type": "email", "msg_id": "m-2"},
            }
        ]
    )
    text = _deliverer()._format_digest(digest, None)

    assert "↳ ⚠ слабое обоснование" in text
    assert "ev:" not in text
    assert "[json]" not in text


def test_seen_before_item_keeps_repeat_badge():
    digest = _digest(
        [
            {
                "title": "Повторное действие",
                "due": None,
                "evidence_id": "ev_seen",
                "confidence": 0.8,
                "seen_before": True,
                "source_ref": {"type": "email", "msg_id": "m-3"},
            }
        ]
    )
    text = _deliverer()._format_digest(digest, None)

    assert "↳ ↻ повтор" in text
    assert "ev:" not in text


def test_both_badges_share_one_subline():
    item = SimpleNamespace(evidence_id="ev_1", weak_evidence=True, seen_before=True)
    line = _deliverer()._format_trace_line(item, "/p.json")
    assert line == "   ↳ ⚠ слабое обоснование | ↻ повтор"


def test_trace_line_helper_edge_cases():
    deliverer = _deliverer()
    # No badges -> no sub-line, regardless of evidence id or json_path.
    assert deliverer._format_trace_line(SimpleNamespace(evidence_id="ev_1"), "/p.json") == ""
    assert deliverer._format_trace_line(SimpleNamespace(evidence_id="system"), "/p.json") == ""
    assert deliverer._format_trace_line(SimpleNamespace(evidence_id=""), None) == ""

    # weak_evidence badge, no operator metadata.
    weak = SimpleNamespace(evidence_id="ev_1", weak_evidence=True)
    line = deliverer._format_trace_line(weak, "/p.json")
    assert line == "   ↳ ⚠ слабое обоснование"
    assert "ev:" not in line
    assert "[json]" not in line


def test_status_item_has_no_trace_line():
    # A degraded "Статус" digest item has no badges -> no sub-line.
    digest = Digest(
        schema_version="1.0",
        prompt_version="none",
        digest_date="2026-03-29",
        trace_id="t",
        sections=[
            {
                "title": "Статус",
                "items": [
                    {
                        "title": "LLM Gateway недоступен. Дайджест неполный.",
                        "due": None,
                        "evidence_id": "system",
                        "confidence": 0.0,
                        "source_ref": {"type": "system"},
                    }
                ],
            }
        ],
    )
    text = _deliverer()._format_digest(digest, "/out/digest.json")
    assert "↳" not in text
