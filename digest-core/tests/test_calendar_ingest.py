"""EWS calendar ingestion (E1, read-side) — events as NormalizedMessages, fully offline.

A fake exchangelib account (`account.calendar.view`) returns hand-built calendar items; the
real fetch is corp-only (ADR-012). Asserts the CalendarItem→NormalizedMessage mapping, the
forward window, cancelled-skip, the cap, and the source-adapter wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from digest_core.config import Config, EWSConfig, TimeConfig
from digest_core.ingest.ews import EWSIngest
from digest_core.ingest.source_adapter import (
    CalendarSourceAdapter,
    SourceAdapter,
    build_adapter,
    canonical_source,
)


def _mailbox(email, name=None):
    return SimpleNamespace(email_address=email, name=name)


def _attendee(email):
    return SimpleNamespace(mailbox=_mailbox(email))


def _event(
    uid,
    subject,
    *,
    start,
    end,
    organizer="org@corp",
    attendees=(),
    location="",
    body="",
    cancelled=False,
):
    return SimpleNamespace(
        uid=uid,
        id=uid,
        subject=subject,
        start=start,
        end=end,
        organizer=_mailbox(organizer, "Organizer"),
        required_attendees=[_attendee(a) for a in attendees],
        optional_attendees=None,
        location=location,
        body=body,
        is_cancelled=cancelled,
    )


def _fake_account(items):
    return SimpleNamespace(calendar=SimpleNamespace(view=lambda start, end: list(items)))


@pytest.fixture
def ingester(tmp_path):
    config = EWSConfig(
        endpoint="https://mail.corp/EWS/Exchange.asmx",
        user_upn="me@corp",
        user_login="me",
        user_domain="corp",
        sync_state_path=str(tmp_path / "ews.syncstate"),
    )
    return EWSIngest(
        config, time_config=TimeConfig(user_timezone="UTC", mailbox_tz="UTC", window="calendar_day")
    )


def test_fetch_events_maps_calendar_items(ingester, monkeypatch):
    start = datetime(2026, 6, 21, 15, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 21, 16, 0, tzinfo=timezone.utc)
    items = [
        _event(
            "uid-1",
            "Budget review",
            start=start,
            end=end,
            organizer="boss@corp",
            attendees=["alice@corp", "bob@corp"],
            location="Room 5",
            body="Agenda: approve Q3 budget; assign owners.",
        ),
        _event("uid-cancel", "Cancelled sync", start=start, end=end, cancelled=True),
    ]
    monkeypatch.setattr(ingester, "_connect", lambda: _fake_account(items))

    events = ingester.fetch_events("2026-06-21", ingester.time_config)
    assert [e.subject for e in events] == ["Budget review"]  # cancelled event skipped
    ev = events[0]
    assert ev.source == "calendar"
    assert ev.sender_email == "boss@corp"
    assert ev.to_recipients == ["alice@corp", "bob@corp"]
    assert ev.msg_id == "uid-1" and ev.conversation_id == "uid-1"
    assert ev.datetime_received == start  # dated by meeting start
    assert "When:" in ev.text_body and "Room 5" in ev.text_body
    assert "Attendees: alice@corp, bob@corp" in ev.text_body
    assert "approve Q3 budget" in ev.text_body  # verbatim agenda retained for extraction


def test_fetch_events_respects_cap(ingester, monkeypatch):
    start = datetime(2026, 6, 21, 9, 0, tzinfo=timezone.utc)
    items = [_event(f"u{i}", f"Meeting {i}", start=start, end=start) for i in range(5)]
    ingester.config.calendar_max_events = 3
    monkeypatch.setattr(ingester, "_connect", lambda: _fake_account(items))
    assert len(ingester.fetch_events("2026-06-21", ingester.time_config)) == 3


def test_calendar_window_is_forward(ingester):
    start, end = ingester._calendar_window("2026-06-21", ingester.time_config)
    assert start == datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc)  # today 00:00
    assert end.date().isoformat() == "2026-06-21"  # default lookahead 1 = today only
    ingester.config.calendar_lookahead_days = 3
    _, end3 = ingester._calendar_window("2026-06-21", ingester.time_config)
    assert end3.date().isoformat() == "2026-06-23"  # today + 2 more days


def test_calendar_source_adapter_delegates(ingester, monkeypatch):
    monkeypatch.setattr(ingester, "_connect", lambda: _fake_account([]))
    adapter = CalendarSourceAdapter(ingester)
    assert adapter.name == "calendar"
    assert isinstance(adapter, SourceAdapter)
    assert adapter.fetch("2026-06-21") == []


def test_canonical_source_and_build_adapter():
    assert canonical_source("calendar") == "calendar"
    assert canonical_source("cal") == "calendar"
    cfg = Config()
    cfg.ews.verify_ca = None  # default points at a corp CA path absent off-corp
    adapter = build_adapter("calendar", cfg)
    assert isinstance(adapter, CalendarSourceAdapter)


def test_meetings_section_built_from_calendar_events():
    """E2: the deterministic Meetings section lists only calendar events, sorted by start."""
    from digest_core import run
    from digest_core.ingest.ews import NormalizedMessage
    from digest_core.llm.schemas import Digest

    cfg = Config()
    cfg.time.user_timezone = "UTC"
    cfg.report.language = "en"
    ctx = SimpleNamespace(config=cfg, run_meta={}, trace_id="t-cal")
    digest = Digest(prompt_version="x", digest_date="2026-06-21", trace_id="t-cal", sections=[])

    def _cal(uid, subject, hour):
        return NormalizedMessage(
            msg_id=uid,
            conversation_id=uid,
            datetime_received=datetime(2026, 6, 21, hour, 0, tzinfo=timezone.utc),
            sender_email="org@corp",
            subject=subject,
            text_body="When: ...",
            source="calendar",
        )

    email = NormalizedMessage(
        msg_id="e1",
        conversation_id="c1",
        datetime_received=datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc),
        sender_email="a@corp",
        subject="An email",
        text_body="hi",
    )  # source defaults to "email"
    messages = [_cal("u-late", "Late sync", 16), email, _cal("u-early", "Standup", 9)]

    run._enrich_digest_with_meetings(ctx, digest, messages)

    meetings = [s for s in digest.sections if s.title == "Meetings"]
    assert len(meetings) == 1
    titles = [i.title for i in meetings[0].items]
    assert titles == ["Standup (09:00)", "Late sync (16:00)"]  # sorted by start; email excluded
    assert ctx.run_meta["meeting_items"] == 2
    assert meetings[0].items[0].source_ref["type"] == "meeting"


def test_meetings_section_skipped_when_no_calendar_events():
    from digest_core import run
    from digest_core.llm.schemas import Digest

    ctx = SimpleNamespace(config=Config(), run_meta={}, trace_id="t")
    digest = Digest(prompt_version="x", digest_date="2026-06-21", trace_id="t", sections=[])
    run._enrich_digest_with_meetings(ctx, digest, [])  # no calendar events
    assert digest.sections == [] and "meeting_items" not in ctx.run_meta


def test_meetings_section_flags_collisions():
    """E3: overlapping meetings get the ⚠ marker + source_ref['overlaps']; clear ones don't."""
    from digest_core import run
    from digest_core.ingest.ews import NormalizedMessage
    from digest_core.llm.schemas import Digest

    cfg = Config()
    cfg.time.user_timezone = "UTC"
    cfg.report.language = "en"
    ctx = SimpleNamespace(config=cfg, run_meta={}, trace_id="t")
    digest = Digest(prompt_version="x", digest_date="2026-06-21", trace_id="t", sections=[])

    def _cal(uid, subject, h0, h1):
        return NormalizedMessage(
            msg_id=uid,
            conversation_id=uid,
            datetime_received=datetime(2026, 6, 21, h0, 0, tzinfo=timezone.utc),
            event_end=datetime(2026, 6, 21, h1, 0, tzinfo=timezone.utc),
            sender_email="o@corp",
            subject=subject,
            text_body="x",
            source="calendar",
        )

    # A 09:00–10:00 and B 09:00–11:00 overlap; C 11:00–12:00 is clear (half-open: B ends as C starts).
    messages = [_cal("a", "A", 9, 10), _cal("b", "B", 9, 11), _cal("c", "C", 11, 12)]
    run._enrich_digest_with_meetings(ctx, digest, messages)

    items = {i.source_subject: i for i in digest.sections[0].items}
    assert "⚠ overlaps" in items["A"].title and "⚠ overlaps" in items["B"].title
    assert "⚠ overlaps" not in items["C"].title
    assert items["A"].source_ref["overlaps"] == ["B"]
    assert items["B"].source_ref["overlaps"] == ["A"]
    assert "overlaps" not in items["C"].source_ref
    assert ctx.run_meta["meeting_collisions"] == 2
