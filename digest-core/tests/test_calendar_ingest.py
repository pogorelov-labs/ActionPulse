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
