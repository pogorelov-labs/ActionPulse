"""
Test configuration classes and methods.
"""

import pytest
import os
from typing import List
from unittest.mock import patch
from digest_core.config import (
    EWSConfig,
    Config,
    LLMConfig,
    TimeConfig,
    ObservabilityConfig,
    _coerce_env_value,
)


class TestEWSConfig:
    """Test EWSConfig class methods."""

    def test_get_password_success(self):
        """Test successful password retrieval from environment."""
        with patch.dict(os.environ, {"EWS_PASSWORD": "test_password"}):
            config = EWSConfig()
            assert config.get_password() == "test_password"

    def test_get_password_failure(self):
        """Test password retrieval failure when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = EWSConfig()
            with pytest.raises(ValueError, match="Environment variable EWS_PASSWORD not set"):
                config.get_password()

    def test_get_ntlm_username_with_login_domain(self):
        """Test NTLM username generation with login and domain."""
        config = EWSConfig(user_login="ivanov", user_domain="company.ru")
        assert config.get_ntlm_username() == "ivanov@company.ru"

    def test_get_ntlm_username_with_upn(self):
        """Test NTLM username generation with UPN fallback."""
        config = EWSConfig(user_upn="ivanov@company.ru")
        assert config.get_ntlm_username() == "ivanov@company.ru"

    def test_get_ntlm_username_failure(self):
        """Test NTLM username generation failure."""
        config = EWSConfig()
        with pytest.raises(ValueError, match="Cannot determine NTLM username"):
            config.get_ntlm_username()

    def test_custom_password_env(self):
        """Test password retrieval with custom environment variable."""
        with patch.dict(os.environ, {"CUSTOM_PASSWORD": "custom_password"}):
            config = EWSConfig(password_env="CUSTOM_PASSWORD")
            assert config.get_password() == "custom_password"


class TestLLMConfig:
    """Test LLMConfig class methods."""

    def test_get_token_success(self):
        """Test successful token retrieval from environment."""
        with patch.dict(os.environ, {"LLM_TOKEN": "test_token"}):
            config = LLMConfig()
            assert config.get_token() == "test_token"

    def test_get_token_failure(self):
        """Test token retrieval failure when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            config = LLMConfig()
            with pytest.raises(ValueError, match="Environment variable LLM_TOKEN not set"):
                config.get_token()


class TestConfig:
    """Test main Config class methods."""

    def test_get_ews_password_delegation(self):
        """Test that get_ews_password delegates to ews.get_password()."""
        with patch.dict(os.environ, {"EWS_PASSWORD": "test_password"}):
            config = Config()
            assert config.get_ews_password() == "test_password"

    def test_get_llm_token_delegation(self):
        """Test that get_llm_token delegates to llm.get_token()."""
        with patch.dict(os.environ, {"LLM_TOKEN": "test_token"}):
            config = Config()
            assert config.get_llm_token() == "test_token"

    def test_config_initialization(self):
        """Test that Config initializes with default sub-configs."""
        config = Config()
        assert isinstance(config.time, TimeConfig)
        assert isinstance(config.ews, EWSConfig)
        assert isinstance(config.llm, LLMConfig)
        assert isinstance(config.observability, ObservabilityConfig)


class TestTimeConfig:
    """Test TimeConfig class."""

    def test_default_values(self):
        """Test default time configuration values."""
        config = TimeConfig()
        assert config.user_timezone == "Europe/Moscow"
        assert config.window == "calendar_day"


class TestObservabilityConfig:
    """Test ObservabilityConfig class."""

    def test_default_values(self):
        """Test default observability configuration values."""
        config = ObservabilityConfig()
        assert config.prometheus_port == 9108
        assert config.log_level == "INFO"


class TestEnvOverYamlPrecedence:
    """Verify that ENV variables override YAML values via _merge_model."""

    def test_explicit_env_field_map_wins(self):
        """Explicit env_field_map entries (e.g. EWS_ENDPOINT) override YAML."""
        with patch.dict(os.environ, {"EWS_ENDPOINT": "https://env-wins"}, clear=False):
            config = Config()
            config._merge_model(
                config.ews,
                {"endpoint": "https://yaml-value"},
                env_field_map={"endpoint": "EWS_ENDPOINT"},
            )
            assert config.ews.endpoint == "https://env-wins"

    def test_generic_env_prefix_wins_for_llm(self):
        """DIGEST_LLM_TIMEOUT_S overrides YAML timeout_s — and is APPLIED + coerced."""
        with patch.dict(os.environ, {"DIGEST_LLM_TIMEOUT_S": "300"}, clear=False):
            config = Config()
            config._merge_model(
                config.llm,
                {"timeout_s": 45},
                env_prefix="LLM",
            )
            # ENV wins and lands as a coerced int (not the YAML 45, not the str "300").
            assert config.llm.timeout_s == 300
            assert isinstance(config.llm.timeout_s, int)

    def test_generic_env_prefix_wins_for_time(self):
        """DIGEST_TIME_WINDOW overrides YAML window with the env value."""
        with patch.dict(os.environ, {"DIGEST_TIME_WINDOW": "rolling_24h"}, clear=False):
            config = Config()
            # Try to overwrite with a distinct YAML value
            config._merge_model(
                config.time,
                {"window": "some_other_mode"},
                env_prefix="TIME",
            )
            # ENV value is applied, not merely "not the YAML value".
            assert config.time.window == "rolling_24h"

    def test_generic_env_prefix_wins_for_degrade(self):
        """DIGEST_DEGRADE_ENABLE overrides YAML degrade.enable."""
        with patch.dict(os.environ, {"DIGEST_DEGRADE_ENABLE": "false"}, clear=False):
            config = Config()
            # Set a known baseline
            config.degrade.enable = False
            # Try to overwrite via YAML
            config._merge_model(
                config.degrade,
                {"enable": True},
                env_prefix="DEGRADE",
            )
            # ENV blocks YAML — value should stay False
            assert config.degrade.enable is False

    def test_generic_env_prefix_wins_for_mm(self):
        """DIGEST_MM_ENABLED overrides YAML deliver.mattermost.enabled."""
        with patch.dict(os.environ, {"DIGEST_MM_ENABLED": "false"}, clear=False):
            config = Config()
            # Set a known baseline
            config.deliver.mattermost.enabled = False
            # Try to overwrite via YAML
            config._merge_model(
                config.deliver.mattermost,
                {"enabled": True},
                env_prefix="MM",
            )
            # ENV blocks YAML — value should stay False
            assert config.deliver.mattermost.enabled is False

    def test_generic_env_prefix_wins_for_observability(self):
        """DIGEST_OBS_LOG_LEVEL overrides YAML log_level."""
        with patch.dict(os.environ, {"DIGEST_OBS_LOG_LEVEL": "DEBUG"}, clear=False):
            config = Config()
            config._merge_model(
                config.observability,
                {"log_level": "ERROR"},
                env_prefix="OBS",
            )
            assert config.observability.log_level == "DEBUG"

    def test_yaml_applies_when_no_env_set(self):
        """Without ENV, YAML values are applied normally."""
        env_keys = ["DIGEST_LLM_TIMEOUT_S", "LLM_ENDPOINT"]
        with patch.dict(os.environ, {k: "" for k in env_keys}, clear=False):
            config = Config()
            config._merge_model(
                config.llm,
                {"timeout_s": 999},
                env_prefix="LLM",
            )
            assert config.llm.timeout_s == 999

    def test_explicit_map_takes_priority_over_generic_prefix(self):
        """If both explicit env_field_map and env_prefix match, explicit wins."""
        with patch.dict(
            os.environ,
            {"EWS_ENDPOINT": "explicit", "DIGEST_EWS_ENDPOINT": "generic"},
            clear=False,
        ):
            config = Config()
            config._merge_model(
                config.ews,
                {"endpoint": "yaml"},
                env_field_map={"endpoint": "EWS_ENDPOINT"},
                env_prefix="EWS",
            )
            # Explicit map wins over both YAML and the generic prefix var.
            assert config.ews.endpoint == "explicit"


class TestConfigIntegration:
    """Integration tests for Config class."""

    def test_ews_config_access_chain(self):
        """Test the correct access chain: Config.get_ews_password() -> EWSConfig.get_password()."""
        with patch.dict(os.environ, {"EWS_PASSWORD": "integration_test_password"}):
            config = Config()

            # Test that Config.get_ews_password() works
            password = config.get_ews_password()
            assert password == "integration_test_password"

            # Test that EWSConfig.get_password() also works
            ews_password = config.ews.get_password()
            assert ews_password == "integration_test_password"

            # Ensure they return the same value
            assert password == ews_password

    def test_llm_config_access_chain(self):
        """Test the correct access chain: Config.get_llm_token() -> LLMConfig.get_token()."""
        with patch.dict(os.environ, {"LLM_TOKEN": "integration_test_token"}):
            config = Config()

            # Test that Config.get_llm_token() works
            token = config.get_llm_token()
            assert token == "integration_test_token"

            # Test that LLMConfig.get_token() also works
            llm_token = config.llm.get_token()
            assert llm_token == "integration_test_token"

            # Ensure they return the same value
            assert token == llm_token


def test_yaml_threading_section_is_honored(tmp_path, monkeypatch):
    """Regression (B8): the `threading:` YAML branch in _apply_yaml_config exists.

    The PR12a `threading` section shipped schema-only with no merge branch, so
    `embedding_merge` (a PC-2-gated /v1/embeddings egress flag) set in
    config.yaml was silently ignored. Assert a real YAML now applies.
    """
    for var in (
        "DIGEST_THREADING_EMBEDDING_MERGE",
        "DIGEST_THREADING_SIMILARITY_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)

    custom = tmp_path / "custom_config.yaml"
    custom.write_text(
        "threading:\n  embedding_merge: true\n  similarity_threshold: 0.5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(custom))

    cfg = Config()
    assert cfg.threading.embedding_merge is True
    assert cfg.threading.similarity_threshold == 0.5


def test_yaml_threading_env_still_wins(tmp_path, monkeypatch):
    """DIGEST_THREADING_* ENV overrides the YAML value (env_prefix precedence)."""
    custom = tmp_path / "custom_config.yaml"
    custom.write_text("threading:\n  embedding_merge: true\n", encoding="utf-8")
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(custom))
    monkeypatch.setenv("DIGEST_THREADING_EMBEDDING_MERGE", "false")
    cfg = Config()
    assert cfg.threading.embedding_merge is False  # ENV beats YAML


class TestCoerceEnvValue:
    """Unit tests for the env-string → field-type coercion helper.

    The env var always arrives as a string; the field default may be int / bool /
    list / str. A coercion bug here is silent (e.g. the truthy string "false"
    landing in a bool field), so pin the type fidelity directly.
    """

    def test_int_coerced_to_int(self):
        out = _coerce_env_value(int, "7")
        assert out == 7 and isinstance(out, int)

    def test_bool_false_is_false_not_truthy_string(self):
        # The footgun: bool("false") is True. Must coerce to the bool False.
        assert _coerce_env_value(bool, "false") is False
        assert _coerce_env_value(bool, "0") is False
        assert _coerce_env_value(bool, "off") is False

    def test_bool_true_variants(self):
        for raw in ("true", "1", "yes", "on"):
            assert _coerce_env_value(bool, raw) is True

    def test_str_passthrough(self):
        assert _coerce_env_value(str, "ru") == "ru"

    def test_list_comma_separated(self):
        assert _coerce_env_value(List[str], "eng, ops, sec") == ["eng", "ops", "sec"]

    def test_list_json_literal(self):
        assert _coerce_env_value(List[str], '["x", "y"]') == ["x", "y"]


def _config_with_yaml(tmp_path, monkeypatch, yaml_text):
    """Build a full Config() with *yaml_text* as the highest-precedence layer."""
    custom = tmp_path / "custom_config.yaml"
    custom.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(custom))
    return Config()


class TestGenericEnvOverrideApplied:
    """DIGEST_<PREFIX>_<FIELD> must APPLY the coerced env value (env > YAML > default).

    Regression for the dead generic-override path: ``_merge_model`` used to read
    the env var only as a signal to SKIP the YAML value and never applied it, so a
    documented override like ``DIGEST_MM_SOURCE_MAX_CHANNELS`` /
    ``DIGEST_LLM_TIMEOUT_S`` was a silent no-op and the field kept its YAML value
    or default. These build a full ``Config()`` so the merge path runs end to end.
    """

    def test_int_field_env_overrides_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIGEST_MM_SOURCE_MAX_CHANNELS", "7")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  max_channels: 50\n")
        assert cfg.mm_source.max_channels == 7  # env beats YAML 50
        assert isinstance(cfg.mm_source.max_channels, int)  # coerced, not the str "7"

    def test_int_field_env_overrides_default_when_absent_from_yaml(self, tmp_path, monkeypatch):
        # max_channels is in NO YAML layer -> env must still beat the default (200).
        # This is the exact case the old code missed (it only iterated YAML keys).
        monkeypatch.setenv("DIGEST_MM_SOURCE_MAX_CHANNELS", "7")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  enabled: false\n")
        assert cfg.mm_source.max_channels == 7

    def test_bool_field_env_overrides_yaml_and_coerces_false(self, tmp_path, monkeypatch):
        # YAML says true; env "false" must win AND coerce to the bool False.
        monkeypatch.setenv("DIGEST_DEGRADE_ENABLE", "false")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "degrade:\n  enable: true\n")
        assert cfg.degrade.enable is False

    def test_bool_field_env_overrides_default(self, tmp_path, monkeypatch):
        # ranker.enabled defaults False; env "true" flips it with no YAML value.
        monkeypatch.setenv("DIGEST_RANKER_ENABLED", "true")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "ranker:\n  log_positions: true\n")
        assert cfg.ranker.enabled is True

    def test_str_field_env_overrides_yaml(self, tmp_path, monkeypatch):
        # Also a regression for the documented DIGEST_REPORT_LANGUAGE override,
        # which flows ONLY through the (previously dead) generic prefix path.
        monkeypatch.setenv("DIGEST_REPORT_LANGUAGE", "ru")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "report:\n  language: en\n")
        assert cfg.report.language == "ru"

    def test_list_field_env_comma_separated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIGEST_MM_SOURCE_CHANNEL_ALLOWLIST", "eng, ops, sec")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  enabled: false\n")
        assert cfg.mm_source.channel_allowlist == ["eng", "ops", "sec"]


class TestExplicitEnvFieldMapStillWins:
    """The backward-compat explicit names (EWS_ENDPOINT, ...) keep their precedence."""

    def test_explicit_map_overrides_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EWS_ENDPOINT", "https://env-explicit")
        cfg = _config_with_yaml(tmp_path, monkeypatch, 'ews:\n  endpoint: "https://yaml-value"\n')
        assert cfg.ews.endpoint == "https://env-explicit"

    def test_explicit_map_beats_generic_prefix(self, tmp_path, monkeypatch):
        # Both EWS_ENDPOINT and the generic DIGEST_EWS_ENDPOINT are set; explicit wins.
        monkeypatch.setenv("EWS_ENDPOINT", "https://explicit")
        monkeypatch.setenv("DIGEST_EWS_ENDPOINT", "https://generic")
        cfg = _config_with_yaml(tmp_path, monkeypatch, 'ews:\n  endpoint: "https://yaml-value"\n')
        assert cfg.ews.endpoint == "https://explicit"


class TestSecretAccessorsUnaffected:
    """Secrets are read from os.getenv at call time, never through _merge_model."""

    def test_secret_accessors_still_resolve_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EWS_PASSWORD", "pw-secret")
        monkeypatch.setenv("LLM_TOKEN", "llm-secret")
        monkeypatch.setenv("MM_PAT", "pat-secret")
        monkeypatch.setenv("MM_WEBHOOK_URL", "https://hook-secret")
        monkeypatch.setenv("MM_BASE_URL", "https://mm.corp")
        # A generic override on a sibling field must not disturb the secret reads.
        monkeypatch.setenv("DIGEST_MM_SOURCE_MAX_CHANNELS", "7")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  enabled: true\n")
        assert cfg.get_ews_password() == "pw-secret"
        assert cfg.get_llm_token() == "llm-secret"
        assert cfg.mm_source.get_token() == "pat-secret"
        assert cfg.deliver.mattermost.get_webhook_url() == "https://hook-secret"
        assert cfg.mm_source.get_base_url() == "https://mm.corp"
        # ...and the generic override still landed alongside the untouched secrets.
        assert cfg.mm_source.max_channels == 7


class TestDmConsentGateWithEnvOverride:
    """An env-set dm_scope must still flow through the DM-consent validator.

    The merge setattrs onto the live instance (no re-validation), but the caller
    reconstructs ``MattermostSourceConfig(**model_dump())`` after the mm_source
    merge, so the ``model_validator`` fires on the merged-in env value. Env must
    not be a side door around consent.
    """

    def test_dm_scope_all_via_env_without_consent_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DIGEST_MM_SOURCE_DM_CONSENT_ACKNOWLEDGED", raising=False)
        monkeypatch.setenv("DIGEST_MM_SOURCE_DM_SCOPE", "all")
        with pytest.raises(ValueError, match="dm_consent_acknowledged"):
            _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  enabled: true\n")

    def test_dm_scope_selected_via_env_without_consent_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DIGEST_MM_SOURCE_DM_CONSENT_ACKNOWLEDGED", raising=False)
        monkeypatch.setenv("DIGEST_MM_SOURCE_DM_SCOPE", "selected")
        with pytest.raises(ValueError, match="dm_consent_acknowledged"):
            _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  enabled: true\n")

    def test_dm_scope_all_via_env_with_consent_loads(self, tmp_path, monkeypatch):
        # Consent acknowledged via env -> scope flows THROUGH validation and loads.
        monkeypatch.setenv("DIGEST_MM_SOURCE_DM_SCOPE", "all")
        monkeypatch.setenv("DIGEST_MM_SOURCE_DM_CONSENT_ACKNOWLEDGED", "true")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  enabled: true\n")
        assert cfg.mm_source.dm_scope == "all"
        assert cfg.mm_source.dm_consent_acknowledged is True

    def test_dm_scope_own_posts_only_via_env_needs_no_consent(self, tmp_path, monkeypatch):
        # own_posts_only exposes no third-party text -> no consent required.
        monkeypatch.delenv("DIGEST_MM_SOURCE_DM_CONSENT_ACKNOWLEDGED", raising=False)
        monkeypatch.setenv("DIGEST_MM_SOURCE_DM_SCOPE", "own_posts_only")
        cfg = _config_with_yaml(tmp_path, monkeypatch, "mm_source:\n  enabled: true\n")
        assert cfg.mm_source.dm_scope == "own_posts_only"
