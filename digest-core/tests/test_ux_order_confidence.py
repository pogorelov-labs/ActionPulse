"""UX changes: Urgent-first section order (C1) + confidence shown only when it
adds signal (C2/C3).

Both renderers (the persisted ``.md`` via ``MarkdownAssembler._generate_markdown``
and the delivered Mattermost message via ``MattermostDeliverer._format_digest``)
must hide the confidence label for high-confidence items and never show it next to
the weak-evidence marker.
"""

from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.config import MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.llm.schemas import Digest
from digest_core.run import _sort_sections

# ---------------------------------------------------------------------------
# C1 — section order: Urgent leads
# ---------------------------------------------------------------------------


def _item(title: str, confidence: float = 0.8):
    return {
        "title": title,
        "due": None,
        "evidence_id": "ev_1",
        "confidence": confidence,
        "source_ref": {"type": "email", "msg_id": "m-1"},
    }


def test_sort_sections_puts_urgent_before_my_actions_before_fyi():
    # LLM emits sections in arbitrary order: My actions, then Urgent, then FYI.
    sections = [
        {"title": "Мои действия", "items": [_item("Подготовить отчёт")]},
        {"title": "Срочное", "items": [_item("Ответить клиенту")]},
        {"title": "К сведению", "items": [_item("Релиз выкатили")]},
    ]
    ordered = [s["title"] for s in _sort_sections(sections)]
    assert ordered == ["Срочное", "Мои действия", "К сведению"]


def test_sort_sections_quarantine_stays_last():
    sections = [
        {"title": "Не подтверждено", "items": [_item("Сомнительное")]},
        {"title": "Срочное", "items": [_item("Срочное дело")]},
        {"title": "Мои действия", "items": [_item("Моё дело")]},
    ]
    ordered = [s["title"] for s in _sort_sections(sections)]
    assert ordered == ["Срочное", "Мои действия", "Не подтверждено"]


# ---------------------------------------------------------------------------
# C2/C3 — confidence shown only when it adds signal
# ---------------------------------------------------------------------------


def _digest(items, *, title="Мои действия"):
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-03-29",
        trace_id="trace-1",
        sections=[{"title": title, "items": items}],
    )


def _mm_item(confidence: float, *, weak: bool = False):
    item = {
        "title": "Сделать дело",
        "due": None,
        "evidence_id": "ev_abc",
        "confidence": confidence,
        "source_ref": {"type": "email", "msg_id": "m-1"},
    }
    if weak:
        item["weak_evidence"] = True
    return item


def _render_md(item, language="ru") -> str:
    assembler = MarkdownAssembler(language=language)
    return assembler._generate_markdown(_digest([item]))


def test_high_confidence_hides_confidence_label_in_both_renderers():
    item = _mm_item(0.95)

    mm_text = MattermostDeliverer(MattermostDeliverConfig(), language="ru")._format_digest(
        _digest([item])
    )
    assert "уверенность" not in mm_text.lower()

    md_text = _render_md(item, language="ru")
    assert "Уверенность" not in md_text

    # English renderers likewise omit "Confidence".
    mm_en = MattermostDeliverer(MattermostDeliverConfig(), language="en")._format_digest(
        _digest([item])
    )
    assert "confidence" not in mm_en.lower()
    md_en = _render_md(item, language="en")
    assert "Confidence" not in md_en


def test_medium_confidence_shows_confidence_label_in_both_renderers():
    item = _mm_item(0.6)

    mm_text = MattermostDeliverer(MattermostDeliverConfig(), language="ru")._format_digest(
        _digest([item])
    )
    assert "уверенность: средняя" in mm_text.lower()

    md_text = _render_md(item, language="ru")
    # MM parity: lowercase, matching the Mattermost «уверенность: средняя».
    assert "**Уверенность:** средняя" in md_text
    assert "**Уверенность:** Средняя" not in md_text


def test_weak_evidence_hides_confidence_but_keeps_marker_in_mm():
    # Weak at any confidence: no confidence label, but the ⚠ marker stays.
    item = _mm_item(0.6, weak=True)

    mm_text = MattermostDeliverer(MattermostDeliverConfig(), language="ru")._format_digest(
        _digest([item])
    )
    assert "уверенность" not in mm_text.lower()
    assert "⚠ слабое обоснование" in mm_text

    # And in the markdown renderer the confidence line is gone too, but the
    # ⚠ weak marker IS shown (MM parity: the .md no longer hides weakness).
    md_text = _render_md(item, language="ru")
    assert "Уверенность" not in md_text
    assert "⚠ слабое обоснование" in md_text
