"""compute_time_window: pure digest-window math.

Regression for the pytz Local-Mean-Time bug — applying a pytz zone via
``datetime(..., tzinfo=zone)`` / ``.replace(tzinfo=zone)`` selects the zone's 19th-century
LMT offset (Europe/Moscow +02:30) instead of the modern one (+03:00), shifting the whole
window by tens of minutes. ``tz.localize`` is the correct API. Pure — runs without extras.
"""

from __future__ import annotations

from datetime import datetime, timezone

from digest_core.config import TimeConfig
from digest_core.ingest.ews import compute_time_window


def _utc(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def test_calendar_day_window_non_utc_has_no_lmt_offset():
    """Europe/Moscow is a clean UTC+3 (no DST since 2014). 00:00 local on the 19th is
    21:00 UTC on the 18th — NOT 21:30, which the +02:30 LMT offset would produce."""
    tc = TimeConfig(user_timezone="Europe/Moscow", window="calendar_day")
    start, end = compute_time_window("2026-06-19", tc)
    assert start == _utc(2026, 6, 18, 21, 0, 0)
    assert end == _utc(2026, 6, 19, 20, 59, 59)


def test_calendar_day_window_utc_identity():
    tc = TimeConfig(user_timezone="UTC", window="calendar_day")
    start, end = compute_time_window("2026-06-19", tc)
    assert start == _utc(2026, 6, 19, 0, 0, 0)
    assert end == _utc(2026, 6, 19, 23, 59, 59)
