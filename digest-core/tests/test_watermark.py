"""Unit tests for the shared per-source high-water mark (BR: incremental load).

Offline, no network: SourceWatermark is pure file IO, and EWSIngest is built
without connecting (its __init__ only sets an SSL context).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from digest_core.config import EWSConfig, TimeConfig
from digest_core.ingest.ews import EWSIngest
from digest_core.ingest.watermark import DEFAULT_OVERLAP, SourceWatermark
from digest_core.run import _dedup_messages


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# SourceWatermark
# --------------------------------------------------------------------------- #


def test_load_missing_returns_none(tmp_path):
    wm = SourceWatermark(state_dir=tmp_path, source="mm")
    assert wm.path() == tmp_path / "mm.watermark"
    assert wm.load() is None


def test_advance_then_load_roundtrips_utc(tmp_path):
    wm = SourceWatermark(state_dir=tmp_path, source="mm")
    ts = _utc(2026, 6, 1, 14, 30)
    wm.advance(ts)
    assert wm.path().exists()
    assert wm.load() == ts


def test_advance_none_is_noop(tmp_path):
    wm = SourceWatermark(state_dir=tmp_path, source="mm")
    wm.advance(None)
    assert not wm.path().exists()
    # An existing mark is left untouched by a None advance (quiet window).
    wm.advance(_utc(2026, 6, 1))
    wm.advance(None)
    assert wm.load() == _utc(2026, 6, 1)


def test_effective_start_no_mark_is_window_start(tmp_path):
    wm = SourceWatermark(state_dir=tmp_path, source="mm")
    window_start = _utc(2026, 6, 1, 0, 0)
    assert wm.effective_start(window_start) == window_start


def test_effective_start_subtracts_overlap(tmp_path):
    wm = SourceWatermark(state_dir=tmp_path, source="mm", overlap=timedelta(minutes=10))
    wm.advance(_utc(2026, 6, 1, 14, 0))
    # watermark - overlap, independent of the caller's window_start (catch-up).
    assert wm.effective_start(_utc(2026, 6, 1, 0, 0)) == _utc(2026, 6, 1, 13, 50)


def test_malformed_file_degrades_to_none(tmp_path):
    p = tmp_path / "mm.watermark"
    p.write_text("not-a-timestamp", encoding="utf-8")
    assert SourceWatermark(state_dir=tmp_path, source="mm").load() is None


def test_naive_timestamp_coerced_to_utc(tmp_path):
    p = tmp_path / "mm.watermark"
    p.write_text("2026-06-01T14:00:00", encoding="utf-8")  # no offset
    assert SourceWatermark(state_dir=tmp_path, source="mm").load() == _utc(2026, 6, 1, 14, 0)


def test_filename_override(tmp_path):
    target = tmp_path / "nested" / "ews.syncstate"
    wm = SourceWatermark(state_dir=tmp_path, source="ews", filename=str(target))
    assert wm.path() == target
    wm.advance(_utc(2026, 6, 1, 9))
    assert target.exists()
    assert wm.load() == _utc(2026, 6, 1, 9)


# --------------------------------------------------------------------------- #
# EWS wiring (back-compat shims + incremental flag)
# --------------------------------------------------------------------------- #


def test_ews_watermark_targets_configured_syncstate(tmp_path):
    cfg = EWSConfig(sync_state_path=str(tmp_path / "ews.syncstate"))
    ing = EWSIngest(cfg, time_config=TimeConfig(), incremental=True)
    assert ing.incremental is True
    assert ing._watermark().path() == tmp_path / "ews.syncstate"


def test_ews_sync_state_shims_roundtrip(tmp_path):
    cfg = EWSConfig(sync_state_path=str(tmp_path / "ews.syncstate"))
    ing = EWSIngest(cfg, time_config=TimeConfig())
    ts = _utc(2026, 6, 1, 9, 0)
    ing._update_sync_state(ts)
    assert ing._load_sync_state() == ts.isoformat()
    # effective_start applies the overlap to the stored mark.
    assert ing._watermark().effective_start(_utc(2026, 6, 1, 0, 0)) == ts - DEFAULT_OVERLAP


def test_ews_incremental_defaults_true(tmp_path):
    ing = EWSIngest(EWSConfig(sync_state_path=str(tmp_path / "ews.syncstate")))
    assert ing.incremental is True


# --------------------------------------------------------------------------- #
# Pipeline-level dedup (BR: дедуп for ALL sources)
# --------------------------------------------------------------------------- #


def test_dedup_messages_collapses_source_msgid():
    from types import SimpleNamespace

    def m(source, msg_id):
        return SimpleNamespace(source=source, msg_id=msg_id)

    msgs = [
        m("email", "a@corp"),
        m("email", "a@corp"),  # duplicate (e.g. two folders / overlap re-read)
        m("mm", "mm:1"),
        m("email", "b@corp"),
        m("mm", "mm:1"),  # duplicate
    ]
    out = _dedup_messages(msgs)
    assert [(x.source, x.msg_id) for x in out] == [
        ("email", "a@corp"),
        ("mm", "mm:1"),
        ("email", "b@corp"),
    ]


def test_dedup_messages_cross_source_same_id_kept():
    """Same raw id in different sources is NOT a duplicate (different URNs)."""
    from types import SimpleNamespace

    msgs = [
        SimpleNamespace(source="email", msg_id="x"),
        SimpleNamespace(source="mm", msg_id="x"),
    ]
    assert len(_dedup_messages(msgs)) == 2
