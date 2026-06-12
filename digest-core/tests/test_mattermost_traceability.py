"""Traceable Mattermost delivery (PR5).

Each digest item now carries a P2 traceability sub-line (evidence id + a link to
the JSON artifact) in the delivered message — previously it carried none. No
``Источники`` header is added (keeps test_e2e_pipeline green).
"""

from types import SimpleNamespace

from digest_core.config import MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.llm.schemas import Digest


def _deliverer():
    return MattermostDeliverer(MattermostDeliverConfig())


def _digest(items):
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-03-29",
        trace_id="trace-1",
        sections=[{"title": "Мои действия", "items": items}],
    )


def test_item_gets_evidence_subline_with_json_link():
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

    assert "↳ ev: ev_abc123def456" in text
    assert "[json](/out/digest-2026-03-29.json#ev_abc123def456)" in text
    assert "Источники" not in text  # no sources header (test_e2e_pipeline:284)


def test_no_json_link_when_path_absent():
    digest = _digest(
        [
            {
                "title": "Сделать X",
                "due": None,
                "evidence_id": "ev_xyz",
                "confidence": 0.5,
                "source_ref": {"type": "email", "msg_id": "m-2"},
            }
        ]
    )
    text = _deliverer()._format_digest(digest, None)

    assert "↳ ev: ev_xyz" in text
    assert "[json]" not in text


def test_trace_line_helper_edge_cases():
    deliverer = _deliverer()
    # system/status items are not traceable
    assert deliverer._format_trace_line(SimpleNamespace(evidence_id="system"), "/p.json") == ""
    assert deliverer._format_trace_line(SimpleNamespace(evidence_id=""), "/p.json") == ""

    # weak_evidence badge lights up once the gate (PR8) adds the field
    weak = SimpleNamespace(evidence_id="ev_1", weak_evidence=True)
    line = deliverer._format_trace_line(weak, "/p.json")
    assert "↳ ev: ev_1" in line
    assert "⚠ weak evidence" in line


def test_status_item_has_no_trace_line():
    # A degraded "Статус" digest item uses evidence_id="system" -> no sub-line.
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
    assert "↳ ev:" not in text
