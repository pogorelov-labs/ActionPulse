"""MM source adapter: per-source incremental watermark (BR: high-water marks).

Reuses the offline fake-HTTP harness from ``test_mm_source_adapter`` (one owner,
one active channel whose single in-window @-mention ``p_mention`` lands at
2026-03-29 12:00:00 UTC). Fully offline, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

from digest_core.config import MattermostSourceConfig
from digest_core.ingest.mattermost import MattermostReadClient, MattermostSourceAdapter
from digest_core.ingest.watermark import SourceWatermark
from tests.test_mm_source_adapter import _DIGEST_DATE, _build_fake_http, _utc_time_config

_MENTION_AT = datetime(2026, 3, 29, 12, 0, 0, tzinfo=timezone.utc)


def _adapter(http, *, state_dir=None, incremental=True) -> MattermostSourceAdapter:
    client = MattermostReadClient(
        "https://mm.corp", "fake-pat-not-a-real-token", http_client=http, per_page=200
    )
    return MattermostSourceAdapter(
        MattermostSourceConfig(base_url="https://mm.corp"),
        _utc_time_config(),
        client=client,
        incremental=incremental,
        state_dir=state_dir,
    )


def test_first_run_full_window_then_advances(tmp_path):
    adapter = _adapter(_build_fake_http(), state_dir=tmp_path)
    msgs = adapter.fetch(_DIGEST_DATE)

    assert len(msgs) == 1  # the single in-window mention
    # No prior mark → full window was used.
    assert adapter.last_fetch_stats["watermark_used"] is None
    # Advanced to the latest post actually seen (NOT the window end).
    assert adapter.last_fetch_stats["watermark_advanced_to"] == _MENTION_AT.isoformat()
    assert SourceWatermark(state_dir=tmp_path, source="mm").load() == _MENTION_AT


def test_second_run_narrows_and_excludes_seen(tmp_path):
    # Pre-seed a mark one hour after the only mention → next run excludes it.
    SourceWatermark(state_dir=tmp_path, source="mm").advance(
        datetime(2026, 3, 29, 13, 0, 0, tzinfo=timezone.utc)
    )
    adapter = _adapter(_build_fake_http(), state_dir=tmp_path)
    msgs = adapter.fetch(_DIGEST_DATE)

    # effective_start = 13:00 - 10min overlap = 12:50, past the 12:00 mention.
    assert msgs == []
    assert adapter.last_fetch_stats["watermark_used"] == "2026-03-29T12:50:00+00:00"
    # Quiet window → mark not advanced (still 13:00).
    assert adapter.last_fetch_stats["watermark_advanced_to"] is None
    assert SourceWatermark(state_dir=tmp_path, source="mm").load() == datetime(
        2026, 3, 29, 13, 0, 0, tzinfo=timezone.utc
    )


def test_not_incremental_bypasses_watermark(tmp_path):
    wm = SourceWatermark(state_dir=tmp_path, source="mm")
    wm.advance(datetime(2026, 3, 29, 13, 0, 0, tzinfo=timezone.utc))

    adapter = _adapter(_build_fake_http(), state_dir=tmp_path, incremental=False)
    msgs = adapter.fetch(_DIGEST_DATE)

    # Full window despite the mark (back-fill must not be truncated).
    assert len(msgs) == 1
    assert adapter.last_fetch_stats["watermark_used"] is None
    # Bypass also means no advance — the existing mark is untouched.
    assert wm.load() == datetime(2026, 3, 29, 13, 0, 0, tzinfo=timezone.utc)


def test_no_state_dir_means_no_watermark(tmp_path):
    adapter = _adapter(_build_fake_http(), state_dir=None)
    msgs = adapter.fetch(_DIGEST_DATE)

    assert len(msgs) == 1
    assert adapter.last_fetch_stats["watermark_used"] is None
    assert adapter.last_fetch_stats["watermark_advanced_to"] is None
    assert not (tmp_path / "mm.watermark").exists()
