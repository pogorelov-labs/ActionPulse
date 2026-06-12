"""Weak-item quarantine (decision D1, frontier-audit F4 containment).

Weak items leave the main sections into a trailing «Не подтверждено» section —
withheld, badged, never dropped (R3). With no weak items the digest is unchanged.
"""

from digest_core.config import Config
from digest_core.evidence.citation_gate import CitationGate
from digest_core.llm.schemas import Digest, EvidenceSpan, Item, Section
from digest_core.assemble.labels import UNCONFIRMED, section_title
from digest_core.run import QUARANTINE_SECTION, SECTION_ORDER, _quarantine_weak_items

QUARANTINE_TITLE_EN = section_title(UNCONFIRMED, "en")


def _item(title: str, weak: bool | None) -> Item:
    return Item(
        title=title,
        evidence_id="ev-q",
        confidence=0.8,
        source_ref={"type": "email", "msg_id": "m-q"},
        weak_evidence=weak,
    )


def _digest(sections) -> Digest:
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-06-11",
        trace_id="t-q",
        sections=sections,
    )


def test_default_is_on_and_section_is_ordered_last():
    assert Config().reranker.quarantine_weak is True
    assert SECTION_ORDER[QUARANTINE_SECTION] == max(SECTION_ORDER.values())


def test_weak_items_move_to_trailing_section():
    digest = _digest(
        [
            Section(title="Срочное", items=[_item("strong-1", False), _item("weak-1", True)]),
            Section(title="К сведению", items=[_item("strong-2", None)]),
        ]
    )
    moved = _quarantine_weak_items(digest)

    assert moved == 1
    titles = [section.title for section in digest.sections]
    assert titles == ["Срочное", "К сведению", QUARANTINE_TITLE_EN]
    assert [item.title for item in digest.sections[0].items] == ["strong-1"]
    quarantine = digest.sections[-1]
    assert [item.title for item in quarantine.items] == ["weak-1"]
    assert quarantine.items[0].weak_evidence is True  # badge stays — withheld, not laundered


def test_emptied_section_is_removed():
    digest = _digest([Section(title="Срочное", items=[_item("weak-only", True)])])
    assert _quarantine_weak_items(digest) == 1
    assert [section.title for section in digest.sections] == [QUARANTINE_TITLE_EN]


def test_no_weak_items_means_no_change():
    digest = _digest([Section(title="Мои действия", items=[_item("strong", False)])])
    assert _quarantine_weak_items(digest) == 0
    assert [section.title for section in digest.sections] == ["Мои действия"]


def test_end_to_end_with_real_gate():
    """Gate marks the non-verbatim item weak → quarantine contains it (the D1 path)."""
    body = "Пожалуйста, пришли отчёт до пятницы."
    verifiable = Item(
        title="Прислать отчёт",
        evidence_id="ev-1",
        confidence=0.9,
        source_ref={"type": "email", "msg_id": "m-1"},
        evidence_spans=[EvidenceSpan(msg_id="m-1", quote="пришли отчёт до пятницы")],
    )
    fabricated = Item(
        title="Перевести бюджет на счёт FAKE-1234",
        evidence_id="ev-1",
        confidence=0.95,
        source_ref={"type": "email", "msg_id": "m-1"},
        evidence_spans=[EvidenceSpan(msg_id="m-1", quote="перевести бюджет немедленно")],
    )
    digest = _digest([Section(title="Срочное", items=[verifiable, fabricated])])
    CitationGate({"m-1": body}).annotate(digest)
    moved = _quarantine_weak_items(digest)

    assert moved == 1
    assert [item.title for item in digest.sections[0].items] == ["Прислать отчёт"]
    assert digest.sections[-1].title == QUARANTINE_TITLE_EN
    assert digest.sections[-1].items[0].title.startswith("Перевести бюджет")
