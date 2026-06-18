"""Pure-logic tests for the Mattermost DM-scope consent helpers."""

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from digest_core.config import MattermostSourceConfig
from digest_core.dm_consent import (
    DM_CONSENT_STALE_DAYS,
    dm_consent_is_stale,
    dm_consent_required,
    normalize_partners,
    now_iso,
    update_mm_source_dm,
)

NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(days=10)).isoformat()
STALE = (NOW - timedelta(days=DM_CONSENT_STALE_DAYS + 1)).isoformat()


class TestConsentRequiredMatrix:
    """Every row of the consent-firing table (pure, no TTY)."""

    def test_new_off_never_requires(self):
        # Any current state → off never fires consent.
        for cur in ("off", "own_posts_only", "selected", "all"):
            assert dm_consent_required(cur, "off", True, FRESH, NOW) is False, cur

    def test_new_own_posts_only_never_requires(self):
        for cur in ("off", "own_posts_only", "selected", "all"):
            assert dm_consent_required(cur, "own_posts_only", True, FRESH, NOW) is False, cur

    def test_off_to_selected_requires(self):
        assert dm_consent_required("off", "selected", False, None, NOW) is True

    def test_own_posts_to_selected_requires(self):
        assert dm_consent_required("own_posts_only", "selected", False, None, NOW) is True

    def test_off_to_all_requires(self):
        assert dm_consent_required("off", "all", False, None, NOW) is True

    def test_selected_to_all_escalation_requires(self):
        # Escalation up the ladder even when already acknowledged + fresh.
        assert dm_consent_required("selected", "all", True, FRESH, NOW) is True

    def test_reenter_all_requires_reconfirm(self):
        # current == all, new == all → re-confirm, no silent re-arm.
        assert dm_consent_required("all", "all", True, FRESH, NOW) is True

    def test_selected_acknowledged_fresh_not_required(self):
        # Already armed, same rung, fresh → no consent.
        assert dm_consent_required("selected", "selected", True, FRESH, NOW) is False

    def test_selected_acknowledged_stale_requires_reaffirm(self):
        assert dm_consent_required("selected", "selected", True, STALE, NOW) is True

    def test_selected_acknowledged_missing_timestamp_requires(self):
        # In a consent scope but no timestamp → treat as stale → re-affirm.
        assert dm_consent_required("selected", "selected", True, None, NOW) is True

    def test_selected_not_acknowledged_requires(self):
        # Same rung but the ack flag is false → required.
        assert dm_consent_required("selected", "selected", False, None, NOW) is True

    def test_downgrade_all_to_selected_not_required(self):
        # Moving DOWN toward off (all → selected) when armed+fresh → no consent.
        assert dm_consent_required("all", "selected", True, FRESH, NOW) is False

    def test_downgrade_all_to_selected_stale_requires(self):
        # Downgrade, but the ack is stale → must re-affirm.
        assert dm_consent_required("all", "selected", True, STALE, NOW) is True


class TestConsentStale:
    def test_fresh_is_not_stale(self):
        assert dm_consent_is_stale(FRESH, NOW) is False

    def test_179_days_not_stale(self):
        ts = (NOW - timedelta(days=179)).isoformat()
        assert dm_consent_is_stale(ts, NOW) is False

    def test_181_days_stale(self):
        ts = (NOW - timedelta(days=181)).isoformat()
        assert dm_consent_is_stale(ts, NOW) is True

    def test_exactly_180_days_stale(self):
        ts = (NOW - timedelta(days=180)).isoformat()
        assert dm_consent_is_stale(ts, NOW) is True  # >= window

    def test_none_is_stale(self):
        assert dm_consent_is_stale(None, NOW) is True

    def test_unparseable_is_stale(self):
        assert dm_consent_is_stale("not-a-date", NOW) is True

    def test_empty_string_is_stale(self):
        assert dm_consent_is_stale("   ", NOW) is True

    def test_trailing_z_parses(self):
        ts = "2026-06-08T12:00:00Z"
        assert dm_consent_is_stale(ts, NOW) is False  # 10 days old

    def test_naive_timestamp_assumed_utc(self):
        ts = (NOW - timedelta(days=5)).replace(tzinfo=None).isoformat()
        assert dm_consent_is_stale(ts, NOW) is False


class TestNormalizePartners:
    def test_comma_split_and_strip(self):
        assert normalize_partners("@ann, bob@corp.ru ,  uid-3 ") == [
            "@ann",
            "bob@corp.ru",
            "uid-3",
        ]

    def test_drops_blanks(self):
        assert normalize_partners("@ann,, ,@bob") == ["@ann", "@bob"]

    def test_empty_string(self):
        assert normalize_partners("") == []

    def test_list_input(self):
        assert normalize_partners(["  @ann ", "", "@bob"]) == ["@ann", "@bob"]

    def test_preserves_order_no_dedupe(self):
        assert normalize_partners("@ann, @ann@corp") == ["@ann", "@ann@corp"]

    def test_non_string_non_list(self):
        assert normalize_partners(None) == []
        assert normalize_partners(42) == []


class TestNowIso:
    def test_is_utc_iso(self):
        ts = now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)


class TestUpdateMmSourceDm:
    """Read-modify-write helper: sets dm_* keys, preserves everything else,
    never persists an unloadable (consent-scope-without-ack) state."""

    def test_sets_keys_and_preserves_other_sections(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.dump(
                {
                    "ews": {"endpoint": "https://ews", "user_upn": "me@corp.ru"},
                    "llm": {"endpoint": "https://llm"},
                    "mm_source": {"enabled": True, "channel_allowlist": ["ops"]},
                }
            )
        )
        update_mm_source_dm(
            cfg,
            dm_scope="selected",
            dm_allowlist=["@ann"],
            dm_consent_acknowledged=True,
            dm_consent_acknowledged_at=FRESH,
        )
        doc = yaml.safe_load(cfg.read_text())
        # DM keys set
        assert doc["mm_source"]["dm_scope"] == "selected"
        assert doc["mm_source"]["dm_allowlist"] == ["@ann"]
        assert doc["mm_source"]["dm_consent_acknowledged"] is True
        assert doc["mm_source"]["dm_consent_acknowledged_at"] == FRESH
        # Other mm_source keys untouched
        assert doc["mm_source"]["enabled"] is True
        assert doc["mm_source"]["channel_allowlist"] == ["ops"]
        # Other sections untouched
        assert doc["ews"]["endpoint"] == "https://ews"
        assert doc["llm"]["endpoint"] == "https://llm"

    def test_missing_file_creates_minimal_doc(self, tmp_path):
        cfg = tmp_path / "nested" / "config.yaml"
        update_mm_source_dm(cfg, dm_scope="own_posts_only")
        doc = yaml.safe_load(cfg.read_text())
        assert doc == {"mm_source": {"dm_scope": "own_posts_only"}}

    def test_empty_file_starts_from_blank(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("")
        update_mm_source_dm(cfg, dm_scope="off")
        doc = yaml.safe_load(cfg.read_text())
        assert doc["mm_source"]["dm_scope"] == "off"

    def test_refuses_selected_without_consent(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        with pytest.raises(ValueError):
            update_mm_source_dm(cfg, dm_scope="selected", dm_consent_acknowledged=False)
        assert not cfg.exists()  # nothing written

    def test_refuses_all_without_consent(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"mm_source": {"dm_scope": "off"}}))
        with pytest.raises(ValueError):
            update_mm_source_dm(cfg, dm_scope="all")
        # untouched: still off
        assert yaml.safe_load(cfg.read_text())["mm_source"]["dm_scope"] == "off"

    def test_selected_with_consent_loads_through_config_model(self, tmp_path):
        """A selected+consent write produces a block the config model accepts."""
        cfg = tmp_path / "config.yaml"
        update_mm_source_dm(
            cfg,
            dm_scope="selected",
            dm_allowlist=["@ann", "bob@corp.ru"],
            dm_consent_acknowledged=True,
            dm_consent_acknowledged_at=FRESH,
        )
        block = yaml.safe_load(cfg.read_text())["mm_source"]
        # The model validator (selected/all REQUIRE consent) must NOT raise.
        model = MattermostSourceConfig(**block)
        assert model.dm_scope == "selected"
        assert model.dm_allowlist == ["@ann", "bob@corp.ru"]
        assert model.dm_consent_acknowledged is True

    def test_downgrade_clears_consent(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        update_mm_source_dm(
            cfg,
            dm_scope="selected",
            dm_consent_acknowledged=True,
            dm_consent_acknowledged_at=FRESH,
        )
        # Now downgrade to off, clearing consent — must be loadable.
        update_mm_source_dm(
            cfg, dm_scope="off", dm_consent_acknowledged=False, dm_consent_acknowledged_at=None
        )
        block = yaml.safe_load(cfg.read_text())["mm_source"]
        model = MattermostSourceConfig(**block)
        assert model.dm_scope == "off"
        assert model.dm_consent_acknowledged is False
