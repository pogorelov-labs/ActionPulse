"""Tests for assemble/labels.py — canonical section keys + report language layer (L1)."""

import pytest

from digest_core.assemble import labels as L


class TestNormalization:
    """Any-language titles map to canonical keys; unknown titles pass through."""

    @pytest.mark.parametrize(
        "title,key",
        [
            ("Мои действия", L.MY_ACTIONS),
            ("My actions", L.MY_ACTIONS),
            ("my actions", L.MY_ACTIONS),
            ("Срочное", L.URGENT),
            ("Urgent", L.URGENT),
            ("К сведению", L.FYI),
            ("FYI", L.FYI),
            ("К сведению (FYI)", L.FYI),
            ("Статус", L.STATUS),
            ("Status", L.STATUS),
            ("Не подтверждено", L.UNCONFIRMED),
            ("Unconfirmed", L.UNCONFIRMED),
            ("  Urgent  ", L.URGENT),
        ],
    )
    def test_known_titles(self, title, key):
        assert L.normalize_section(title) == key

    def test_unknown_title(self):
        assert L.normalize_section("Something else") is None
        assert L.normalize_section("") is None

    def test_display_title_rerenders_by_language(self):
        assert L.display_title("Мои действия", "en") == "My actions"
        assert L.display_title("My actions", "ru") == "Мои действия"
        assert L.display_title("Unknown section", "en") == "Unknown section"

    def test_section_title_fallbacks(self):
        assert L.section_title(L.FYI, "ru") == "К сведению"
        assert L.section_title(L.FYI, "de") == "FYI"  # unknown lang -> EN
        assert L.section_title("nonexistent_key", "en") == "nonexistent_key"


class TestSortWeights:
    """Urgent leads, then My actions, Open loops, FYI, Unconfirmed — same in both languages."""

    def test_order_is_language_independent(self):
        ru = ["Срочное", "Мои действия", "Открытые вопросы", "К сведению", "Не подтверждено"]
        en = ["Urgent", "My actions", "Open loops", "FYI", "Unconfirmed"]
        assert [L.section_sort_weight(t) for t in ru] == [0, 1, 2, 3, 4]
        assert [L.section_sort_weight(t) for t in en] == [0, 1, 2, 3, 4]

    def test_unknown_sorts_last(self):
        assert L.section_sort_weight("Статус") == 99  # not in order map, same as pre-L1
        assert L.section_sort_weight("whatever") == 99


class TestStrings:
    def test_default_language_is_english(self):
        assert L.DEFAULT_LANGUAGE == "en"

    def test_tables_have_identical_keys(self):
        assert set(L.STRINGS["en"].keys()) == set(L.STRINGS["ru"].keys())
        assert set(L.SECTION_TITLES["en"].keys()) == set(L.SECTION_TITLES["ru"].keys())

    def test_unknown_language_falls_back_to_english(self):
        assert L.report_strings("de")["digest_header"] == "Action digest"

    def test_stage_banner(self):
        assert L.stage_banner("threads", "ru") == "Сбой при группировке писем. Дайджест неполный."
        assert L.stage_banner("threads", "en").startswith("Email grouping failed")
        assert L.stage_banner("nonexistent", "en") == L.STRINGS["en"]["banner_pipeline"]

    def test_confidence_thresholds_mirror_pre_l1(self):
        # Exact thresholds from the original mattermost/markdown implementations.
        assert L.confidence_text(0.95, "ru") == "очень высокая"
        assert L.confidence_text(0.9, "ru") == "очень высокая"
        assert L.confidence_text(0.89, "ru") == "высокая"
        assert L.confidence_text(0.7, "ru") == "высокая"
        assert L.confidence_text(0.69, "ru") == "средняя"
        assert L.confidence_text(0.5, "ru") == "средняя"
        assert L.confidence_text(0.49, "ru") == "низкая"
        assert L.confidence_text(0.3, "ru") == "низкая"
        assert L.confidence_text(0.29, "ru") == "очень низкая"
        assert L.confidence_text(0.95, "en") == "very high"

    def test_should_show_confidence_only_for_borderline(self):
        # High / very high: label is noise, suppressed.
        assert L.should_show_confidence(0.95) is False
        assert L.should_show_confidence(0.7) is False  # band boundary == high
        # Borderline («средняя» band) surfaces the label.
        assert L.should_show_confidence(0.69) is True
        assert L.should_show_confidence(0.5) is True

    def test_should_show_confidence_suppressed_by_weak_evidence(self):
        # weak_evidence wins at any confidence — the ⚠ marker is the signal.
        assert L.should_show_confidence(0.5, weak_evidence=True) is False
        assert L.should_show_confidence(0.95, weak_evidence=True) is False

    def test_confidence_display_max_is_high_band_boundary(self):
        assert L.CONFIDENCE_DISPLAY_MAX == 0.7


class TestConfigDefault:
    def test_report_config_default_en(self):
        from digest_core.config import ReportConfig

        assert ReportConfig().language == "en"


class TestPromptSelection:
    """(model, language) -> prompt version routing in run.py."""

    def test_en_selects_v2_for_all_models(self):
        from digest_core.run import _load_extract_prompt

        for model in ("qwen35-397b-a17b", "glm-4.7-flash", "unknown-model"):
            version, text = _load_extract_prompt(model, "en")
            assert version == "extract_actions.en.v2"
            assert '"My actions", "Urgent", or "FYI"' in text
            # The citation-gate invariant: quotes stay source-language, verbatim.
            assert "verbatim" in text.lower()

    def test_ru_keeps_per_model_map(self):
        from digest_core.run import _load_extract_prompt

        version, _ = _load_extract_prompt("qwen35-397b-a17b", "ru")
        assert version == "extract_actions.en.v1"
        version, _ = _load_extract_prompt("glm-4.7-flash", "ru")
        assert version == "extract_actions.v1"
