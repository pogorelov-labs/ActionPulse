"""Discovery correctness batch — regression tests for A1-A10 / B5.

Each test targets one shipped bug fix and is written to fail against the
pre-fix behavior. Synthetic data only.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.config import MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostDeliverer, _blen
from digest_core.llm.schemas import Digest, Item, Section
from digest_core.run import _resolve_digest_date

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(title, *, evidence_id="ev-x", confidence=0.8):
    return Item(
        title=title,
        due=None,
        evidence_id=evidence_id,
        confidence=confidence,
        source_ref={"type": "email", "msg_id": "m-1"},
    )


def _digest(sections, *, date="2026-03-29"):
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date=date,
        trace_id="trace-batch",
        sections=sections,
    )


# ---------------------------------------------------------------------------
# FIX 1 (A1): digest_date honors the user's timezone, not UTC.
# ---------------------------------------------------------------------------


def test_resolve_digest_date_uses_user_timezone_after_local_midnight():
    """2026-06-17T23:30Z is already 2026-06-18 in Moscow (+03)."""
    instant = datetime(2026, 6, 17, 23, 30, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant

    with mock.patch("digest_core.run.datetime", _FrozenDatetime):
        moscow = _resolve_digest_date("today", "Europe/Moscow")
        utc = _resolve_digest_date("today", "UTC")

    # Moscow has crossed midnight; UTC has not.
    assert moscow == "2026-06-18"
    assert utc == "2026-06-17"
    assert moscow != utc


def test_resolve_digest_date_yesterday_is_relative_to_timezone():
    instant = datetime(2026, 6, 17, 23, 30, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant

    with mock.patch("digest_core.run.datetime", _FrozenDatetime):
        # Moscow "today" is 2026-06-18, so Moscow "yesterday" is 2026-06-17.
        assert _resolve_digest_date("yesterday", "Europe/Moscow") == "2026-06-17"


def test_resolve_digest_date_invalid_tz_falls_back_to_utc():
    instant = datetime(2026, 6, 17, 23, 30, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant

    with mock.patch("digest_core.run.datetime", _FrozenDatetime):
        # Garbage tz string must not raise; falls back to the UTC date.
        assert _resolve_digest_date("today", "Not/A_Zone") == "2026-06-17"


def test_resolve_digest_date_explicit_date_untouched():
    # Explicit YYYY-MM-DD never calls now() and is returned as-is.
    assert _resolve_digest_date("2026-01-02", "Europe/Moscow") == "2026-01-02"


# ---------------------------------------------------------------------------
# FIX 2 (A2 -> superseded by C5/C8): the trace footer is gone from the delivered
# message entirely — no `_trace:`, no `items:` count (operator metadata).
# ---------------------------------------------------------------------------


def test_delivered_message_has_no_trace_footer():
    digest = _digest(
        [
            Section(
                title="Мои действия",
                items=[_item("A"), _item("B"), _item("C")],
            ),
            Section(title="Не подтверждено", items=[_item("Q", confidence=0.2)]),
        ]
    )
    text = MattermostDeliverer(MattermostDeliverConfig())._format_digest(digest)
    assert "_trace:" not in text
    assert "items:" not in text
    assert "trace-batch" not in text


# ---------------------------------------------------------------------------
# FIX 3 (A6): an empty digest is delivered with the no-actions block, not a
# bare header.
# ---------------------------------------------------------------------------


def test_empty_digest_renders_no_actions_block():
    deliverer = MattermostDeliverer(MattermostDeliverConfig())
    no_actions = deliverer._s["no_actions"]

    # No sections at all.
    assert no_actions in deliverer._format_digest(_digest([]))
    # Sections present but all empty.
    all_empty = _digest([Section(title="Мои действия", items=[])])
    assert no_actions in deliverer._format_digest(all_empty)


# ---------------------------------------------------------------------------
# FIX 4 (A7): the dedup-repeat marker is "repeat" in EN, not "repaired".
# ---------------------------------------------------------------------------


def test_seen_before_marker_is_repeat_not_repaired_en():
    deliverer = MattermostDeliverer(MattermostDeliverConfig(), language="en")
    item = _item("Repeated action")
    item.seen_before = True
    line = deliverer._format_trace_line(item, None)
    assert "↻ repeat" in line
    assert "repaired" not in line


def test_seen_before_marker_is_povtor_ru():
    deliverer = MattermostDeliverer(MattermostDeliverConfig(), language="ru")
    item = _item("Повторное действие")
    item.seen_before = True
    line = deliverer._format_trace_line(item, None)
    assert "↻ повтор" in line


# ---------------------------------------------------------------------------
# FIX 5 (A10): word-limit truncation preserves newline / markdown structure.
# ---------------------------------------------------------------------------


def test_truncate_content_preserves_line_structure():
    assembler = MarkdownAssembler()
    # Build content well over 400 words across many heading lines.
    lines = []
    for i in range(120):
        lines.append(f"## Heading {i}")
        lines.append(f"body words alpha beta gamma delta {i}")
        lines.append("")
    content = "\n".join(lines)
    assert assembler._count_words(content) > 400

    out = assembler._truncate_content(content, assembler.max_words)

    # Structure survives: newlines and at least one heading remain.
    assert "\n" in out
    assert "\n##" in out or out.startswith("##")
    # The truncation note is appended.
    assert assembler._s["truncated_note"] in out
    # Within budget (note is excluded from the body line accounting).
    body = out.replace(assembler._s["truncated_note"], "")
    assert assembler._count_words(body) <= assembler.max_words


# ---------------------------------------------------------------------------
# FIX 6 (B5): the vestigial Sources section is gone from the live .md.
# ---------------------------------------------------------------------------


def test_markdown_has_no_vestigial_sources_section():
    digest = _digest(
        [
            Section(
                title="My actions",
                items=[_item("Do thing", evidence_id="ev-001")],
            )
        ]
    )
    md = MarkdownAssembler(language="ru")._generate_markdown(digest)
    md_en = MarkdownAssembler(language="en")._generate_markdown(digest)

    # No standalone Sources header / empty Evidence entries.
    assert "## Источники" not in md
    assert "## Sources" not in md_en
    assert "### Evidence ev-001" not in md
    assert "*ID: ev-001*" not in md
    # The real per-item source line is still there.
    assert "evidence ev-001" in md
    assert "evidence ev-001" in md_en


# ---------------------------------------------------------------------------
# FIX 7 (A3+A4): split measures UTF-8 bytes and reserves header space so every
# delivered part (header included) stays within the byte budget.
# ---------------------------------------------------------------------------


def test_split_pure_byte_overbudget_is_detected():
    deliverer = MattermostDeliverer(MattermostDeliverConfig())
    msg = "я" * 50  # 100 UTF-8 bytes, 50 code points
    assert _blen(msg) == 100
    parts = deliverer._split_message(msg, 80)
    # 100 bytes > 80 -> must split (a code-point check would see len==50 <= 80).
    assert len(parts) >= 2


def test_split_every_part_within_byte_budget_including_header():
    max_len = 200
    config = MattermostDeliverConfig(max_message_length=max_len)
    deliverer = MattermostDeliverer(config)

    titles = [f"Срочнейшее действие номер {i} требует внимания" for i in range(40)]
    digest = _digest([Section(title="Мои действия", items=[_item(t) for t in titles])])
    message = deliverer._format_digest(digest)
    assert _blen(message) > max_len  # precondition: it must actually split

    parts = deliverer._split_message(message, max_len)
    assert len(parts) >= 2
    for part in parts:
        assert _blen(part) <= max_len, f"part over byte budget: {_blen(part)} > {max_len}"

    # Every item title survives across the concatenated parts.
    joined = "\n".join(parts)
    for t in titles:
        assert t in joined


def test_split_prefers_space_boundary_for_long_line():
    deliverer = MattermostDeliverer(MattermostDeliverConfig())
    # One long Cyrillic line with spaces; small byte budget forces a line split.
    line = " ".join(["слово"] * 30)  # ~ many bytes, well over 40
    parts = deliverer._split_long_line(line, 40)
    assert len(parts) >= 2
    for part in parts:
        assert _blen(part) <= 40
    # No piece starts mid-word at a leading space artifact.
    assert all(not p.startswith(" ") for p in parts)


def test_deliver_writes_md_file_smoke(tmp_path: Path):
    """End-to-end smoke: the V1 markdown still writes and validates."""
    digest = _digest(
        [Section(title="My actions", items=[_item("Ship it", evidence_id="ev-smoke")])]
    )
    assembler = MarkdownAssembler()
    out = tmp_path / "digest.md"
    assembler.write_digest(digest, out)
    content = out.read_text(encoding="utf-8")
    assert assembler.validate_markdown(content)
