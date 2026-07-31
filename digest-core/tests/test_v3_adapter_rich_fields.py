"""A1.5 — the v3 contract's per-section facts reach the reader (ACTPULSE-94).

v3 extracts more than a title: an action has `owners`, a meeting has `participants`
and a `location`, a risk has an `impact`. The adapter flattened all of that away, so
the constrained contract was strictly more informative than anything the reader ever
saw — and `others_actions` ("someone else owns this") was indistinguishable from an
unowned FYI note, which is the one case where the owner IS the information.

Two properties are asserted here, and the second is the one that would rot quietly:

1. the fields survive the adapter **and** render in the markdown;
2. a **v1** digest is byte-identical to before — these fields default to None, not
   to `[]`, because ASSEMBLE writes `model_dump(exclude_none=True)` and an empty
   list would survive that and change every artifact for a contract that is still
   default-off.
"""

from __future__ import annotations

import pytest

from digest_core.assemble.labels import report_strings
from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.schemas import Digest, EnhancedDigestV3, Item, Section
from digest_core.llm.v3_adapter import v3_to_digest

EVIDENCE_ID = "ev-1"
BODY = "Ivan will ship the budget by Friday; the room is 5B."


@pytest.fixture
def evidence():
    return [
        EvidenceChunk(
            evidence_id=EVIDENCE_ID,
            msg_id="m-1",
            text=BODY,
            source_ref={"type": "email", "msg_id": "m-1"},
        )
    ]


def _span():
    return [{"msg_id": "m-1", "quote": "ship the budget"}]


def _v3(**sections) -> EnhancedDigestV3:
    return EnhancedDigestV3(digest_date="2026-07-31", trace_id="t", **sections)


def _adapt(v3, evidence) -> Digest:
    digest, _ = v3_to_digest(
        v3,
        evidence=evidence,
        digest_date="2026-07-31",
        trace_id="t",
        prompt_version="v3",
        language="en",
    )
    return digest


def _only_item(digest: Digest) -> Item:
    items = [i for s in digest.sections for i in s.items]
    assert len(items) == 1, items
    return items[0]


class TestFieldsSurviveTheAdapter:
    def test_my_action_carries_owners(self, evidence):
        digest = _adapt(
            _v3(
                my_actions=[
                    {
                        "title": "Ship the budget",
                        "description": "Ivan ships the budget",
                        "quote": "ship the budget",
                        "evidence_id": EVIDENCE_ID,
                        "confidence": "high",
                        "owners": ["Ivan Petrov"],
                        "evidence_spans": _span(),
                    }
                ]
            ),
            evidence,
        )
        assert _only_item(digest).owners == ["Ivan Petrov"]

    def test_others_action_carries_owners(self, evidence):
        """The case that matters most: without the owner this is an unowned FYI note."""
        digest = _adapt(
            _v3(
                others_actions=[
                    {
                        "title": "Maria reviews the contract",
                        "description": "Maria reviews it",
                        "quote": "ship the budget",
                        "evidence_id": EVIDENCE_ID,
                        "confidence": "medium",
                        "owners": ["Maria"],
                        "evidence_spans": _span(),
                    }
                ]
            ),
            evidence,
        )
        assert _only_item(digest).owners == ["Maria"]

    def test_meeting_carries_participants_and_location(self, evidence):
        digest = _adapt(
            _v3(
                deadlines_meetings=[
                    {
                        "title": "Budget review",
                        "quote": "ship the budget",
                        "date_time": "2026-08-01T10:00",
                        "evidence_id": EVIDENCE_ID,
                        "participants": ["Ivan", "Maria"],
                        "location": "Room 5B",
                        "evidence_spans": _span(),
                    }
                ]
            ),
            evidence,
        )
        item = _only_item(digest)
        assert item.participants == ["Ivan", "Maria"]
        assert item.location == "Room 5B"

    def test_risk_carries_owners_and_impact(self, evidence):
        digest = _adapt(
            _v3(
                risks_blockers=[
                    {
                        "title": "Vendor slipped",
                        "quote": "ship the budget",
                        "evidence_id": EVIDENCE_ID,
                        "severity": "High",
                        "owners": ["Ops"],
                        "impact": "release slips a week",
                        "evidence_spans": _span(),
                    }
                ]
            ),
            evidence,
        )
        item = _only_item(digest)
        assert item.owners == ["Ops"]
        assert item.impact == "release slips a week"

    def test_absent_fields_stay_none_not_empty_list(self, evidence):
        """None, not []: the difference is whether every v1 artifact changes shape."""
        digest = _adapt(
            _v3(
                fyi=[
                    {
                        "title": "FYI note",
                        "quote": "ship the budget",
                        "evidence_id": EVIDENCE_ID,
                        "evidence_spans": _span(),
                    }
                ]
            ),
            evidence,
        )
        item = _only_item(digest)
        assert item.owners is None and item.participants is None
        assert item.location is None and item.impact is None


class TestTheReaderActuallySeesThem:
    """Carrying data onto a model nobody renders is not carrying it."""

    @staticmethod
    def _render(item: Item, language: str = "en") -> str:
        digest = Digest(
            prompt_version="v3",
            digest_date="2026-07-31",
            trace_id="t",
            sections=[Section(title="My actions", items=[item])],
        )
        # _generate_markdown is the established render seam in these tests
        # (see test_carryover_render.py); write_digest just wraps it in file IO.
        return MarkdownAssembler(language=language)._generate_markdown(digest)

    def _item(self, **kw) -> Item:
        return Item(
            title="Ship the budget",
            evidence_id=EVIDENCE_ID,
            confidence=0.95,
            source_ref={"type": "email"},
            **kw,
        )

    def test_all_four_render_with_their_labels(self):
        md = self._render(
            self._item(
                owners=["Ivan"],
                participants=["Ann", "Bob"],
                location="Room 5B",
                impact="release slips",
            )
        )
        s = report_strings("en")
        assert f"**{s['owners_label']}:** Ivan" in md
        assert f"**{s['participants_label']}:** Ann, Bob" in md
        assert f"**{s['location_label']}:** Room 5B" in md
        assert f"**{s['impact_label']}:** release slips" in md

    def test_labels_are_translated_not_inlined(self):
        """Report-bound strings live only in labels.py — never hardcoded in a renderer."""
        md = self._render(self._item(owners=["Ivan"]), language="ru")
        assert report_strings("ru")["owners_label"] in md
        assert report_strings("en")["owners_label"] not in md

    def test_a_v1_item_renders_none_of_them(self):
        md = self._render(self._item())
        s = report_strings("en")
        for key in ("owners_label", "participants_label", "location_label", "impact_label"):
            assert s[key] not in md


class TestV1ArtifactsAreUnchanged:
    """The reason there is no PIPELINE_VERSION bump — assert it rather than claim it."""

    def test_a_v1_item_gains_no_keys_in_the_artifact(self):
        item = Item(title="t", evidence_id="e", confidence=0.9, source_ref={"type": "email"})
        # Exactly what ASSEMBLE writes (run._stage_assemble).
        dumped = item.model_dump(exclude_none=True)
        for key in ("owners", "participants", "location", "impact"):
            assert key not in dumped, (
                f"{key} leaked into a v1 artifact — an empty-list default would do this, "
                "and every existing artifact would need an idempotency rebuild"
            )

    def test_a_v3_item_does_carry_them_into_the_artifact(self):
        """Anchor: guards against the test above passing because nothing works."""
        item = Item(
            title="t",
            evidence_id="e",
            confidence=0.9,
            source_ref={"type": "email"},
            owners=["Ivan"],
            location="Room 5B",
        )
        dumped = item.model_dump(exclude_none=True)
        assert dumped["owners"] == ["Ivan"]
        assert dumped["location"] == "Room 5B"
        assert "participants" not in dumped  # still absent when unset
