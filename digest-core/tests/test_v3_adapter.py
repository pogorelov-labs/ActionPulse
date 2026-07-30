"""A1.4 — the v3 extraction payload maps onto the live Digest.

The adapter is the whole risk surface of the v3 flip: everything downstream keeps
running on `Item`, so if this function is right the flip is safe, and if it is
wrong the digest silently changes shape. Hence a truth table rather than a smoke
test.
"""

from __future__ import annotations

import pytest

from digest_core.assemble.labels import FYI, MEETINGS, MY_ACTIONS, URGENT, normalize_section
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.schemas import EnhancedDigestV3
from digest_core.llm.v3_adapter import CONFIDENCE_BY_WORD, v3_to_digest


def _chunk(evidence_id="ev-1", msg_id="msg-1"):
    return EvidenceChunk(
        evidence_id=evidence_id,
        conversation_id="conv-1",
        content="please send the report",
        source_ref={"type": "email", "msg_id": msg_id, "conversation_id": "conv-1"},
    )


def _span(msg_id="msg-1", quote="please send the report"):
    return {"msg_id": msg_id, "quote": quote}


def _action(evidence_id="ev-1", confidence="High", title="Send the report", due=None):
    return {
        "title": title,
        "description": "d",
        "evidence_id": evidence_id,
        "quote": "please send the report",
        "due_date": due,
        "owners": [],
        "confidence": confidence,
        "evidence_spans": [_span()],
    }


def _adapt(payload, evidence=None, language="en"):
    v3 = EnhancedDigestV3.model_validate(
        {"digest_date": "2026-03-29", "trace_id": "t-1", **payload}
    )
    return v3_to_digest(
        v3,
        evidence=evidence if evidence is not None else [_chunk()],
        digest_date="2026-03-29",
        trace_id="t-1",
        prompt_version="extract_actions.en.v3",
        language=language,
    )


def _keys(digest):
    return [normalize_section(section.title) for section in digest.sections]


class TestListRouting:
    def test_my_actions_maps_to_my_actions(self):
        digest, _ = _adapt({"my_actions": [_action()]})
        assert _keys(digest) == [MY_ACTIONS]

    def test_others_actions_are_informational_for_the_recipient(self):
        digest, _ = _adapt({"others_actions": [_action()]})
        assert _keys(digest) == [FYI]

    def test_deadlines_meetings_map_to_the_calendar_section(self):
        digest, _ = _adapt(
            {
                "deadlines_meetings": [
                    {
                        "title": "Steering committee",
                        "evidence_id": "ev-1",
                        "quote": "q",
                        "date_time": "2026-03-30T15:00:00+03:00",
                        "evidence_spans": [_span()],
                    }
                ]
            }
        )
        assert _keys(digest) == [MEETINGS]
        assert digest.sections[0].items[0].due == "2026-03-30T15:00:00+03:00"

    @pytest.mark.parametrize("severity,expected", [("High", URGENT), ("Medium", FYI), ("Low", FYI)])
    def test_severity_routes_risks(self, severity, expected):
        """The routing v3's typed `severity` field exists to enable."""
        digest, _ = _adapt(
            {
                "risks_blockers": [
                    {
                        "title": "Integration blocked",
                        "evidence_id": "ev-1",
                        "quote": "q",
                        "severity": severity,
                        "impact": "release slips",
                        "evidence_spans": [_span()],
                    }
                ]
            }
        )
        assert _keys(digest) == [expected]

    def test_sections_come_out_in_canonical_order(self):
        digest, _ = _adapt(
            {
                "fyi": [
                    {
                        "title": "note",
                        "evidence_id": "ev-1",
                        "quote": "q",
                        "evidence_spans": [_span()],
                    }
                ],
                "my_actions": [_action()],
                "risks_blockers": [
                    {
                        "title": "blocked",
                        "evidence_id": "ev-1",
                        "quote": "q",
                        "severity": "High",
                        "impact": "i",
                        "evidence_spans": [_span()],
                    }
                ],
            }
        )
        # URGENT(0) before MY_ACTIONS(1) before FYI(5)
        assert _keys(digest) == [URGENT, MY_ACTIONS, FYI]

    def test_empty_extraction_yields_no_sections(self):
        digest, stats = _adapt({})
        assert digest.sections == []
        assert stats["items"] == 0


class TestProvenance:
    def test_source_ref_comes_from_the_pipeline_not_the_model(self):
        """v1 makes the model echo source_ref; here it cannot be wrong."""
        digest, _ = _adapt({"my_actions": [_action()]}, evidence=[_chunk(msg_id="msg-REAL")])
        assert digest.sections[0].items[0].source_ref["msg_id"] == "msg-REAL"

    def test_item_citing_an_unknown_evidence_id_is_dropped_and_counted(self):
        digest, stats = _adapt({"my_actions": [_action(evidence_id="ev-HALLUCINATED")]})
        assert digest.sections == []
        assert stats["dropped_unknown_evidence_id"] == 1

    def test_item_without_a_span_is_dropped_and_counted(self):
        """P2 is golden rule #1 — an unsupported item never reaches the report."""
        item = _action()
        item["evidence_spans"] = []
        digest, stats = _adapt({"my_actions": [item]})
        assert digest.sections == []
        assert stats["dropped_missing_evidence_span"] == 1

    def test_spans_survive_onto_the_item(self):
        digest, _ = _adapt({"my_actions": [_action()]})
        spans = digest.sections[0].items[0].evidence_spans
        assert [s.quote for s in spans] == ["please send the report"]


class TestConfidenceMapping:
    @pytest.mark.parametrize("word", ["High", "Medium", "Low"])
    def test_words_map_to_the_documented_bands(self, word):
        digest, _ = _adapt({"my_actions": [_action(confidence=word)]})
        assert digest.sections[0].items[0].confidence == CONFIDENCE_BY_WORD[word.lower()]

    def test_unknown_confidence_falls_back_low_not_high(self):
        """An unparseable confidence is not evidence of a confident item."""
        digest, _ = _adapt({"my_actions": [_action(confidence="Extremely Certain")]})
        assert digest.sections[0].items[0].confidence == CONFIDENCE_BY_WORD["low"]


class TestLanguage:
    def test_section_titles_follow_the_report_language(self):
        digest, _ = _adapt({"my_actions": [_action()]}, language="ru")
        assert digest.sections[0].title == "Мои действия"
        # ...and still normalize back to the canonical key
        assert normalize_section(digest.sections[0].title) == MY_ACTIONS
