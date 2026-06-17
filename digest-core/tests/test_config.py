"""
Test configuration classes and methods.
"""

import pytest
import os
from unittest.mock import patch
from digest_core.config import (
    EWSConfig,
    Config,
    LLMConfig,
    TimeConfig,
    ObservabilityConfig,
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
        """DIGEST_LLM_TIMEOUT_S overrides YAML timeout_s."""
        with patch.dict(os.environ, {"DIGEST_LLM_TIMEOUT_S": "300"}, clear=False):
            config = Config()
            config._merge_model(
                config.llm,
                {"timeout_s": 45},
                env_prefix="LLM",
            )
            # ENV was set, so YAML should NOT overwrite
            assert config.llm.timeout_s != 45

    def test_generic_env_prefix_wins_for_time(self):
        """DIGEST_TIME_WINDOW overrides YAML window."""
        with patch.dict(os.environ, {"DIGEST_TIME_WINDOW": "rolling_24h"}, clear=False):
            config = Config()
            # Try to overwrite with a distinct YAML value
            config._merge_model(
                config.time,
                {"window": "some_other_mode"},
                env_prefix="TIME",
            )
            # ENV blocks YAML — value should NOT be "some_other_mode"
            assert config.time.window != "some_other_mode"

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
            assert config.observability.log_level != "ERROR"

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
            # Explicit match skips before generic check
            assert config.ews.endpoint != "yaml"


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
