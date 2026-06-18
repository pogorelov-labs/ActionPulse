"""Tests for the interactive setup wizard."""

import yaml

from digest_core.config import MattermostSourceConfig
from digest_core.setup_autodetect import DetectedEnv
from digest_core import setup_wizard as wiz
from digest_core.setup_wizard import (
    _auto_detect_ca_path,
    _dm_step,
    _dm_summary_value,
    _resolve_ews_login,
    _derive_from_email,
    _mask_secret,
    _merge_aliases,
    _read_existing_env,
    _validate_email,
    _validate_url,
    _write_env_file,
    _write_config_yaml,
)


class TestMergeAliases:
    """Detected real-name tokens + machine login extend the derived aliases."""

    def test_adds_name_tokens_and_login(self):
        det = DetectedEnv(login="ruapgr2", first_name="Ruslan", last_name="POGORELOV")
        merged = _merge_aliases(["Ruslan", "ruslan.pogorelov@megacorp.ru"], det)
        assert "Pogorelov" in merged  # caps surname title-cased
        assert "ruapgr2" in merged  # machine login differs from email local
        assert merged.count("Ruslan") == 1  # case-insensitive dedupe

    def test_no_detection_is_identity(self):
        assert _merge_aliases(["X"], None) == ["X"]


class TestMaskSecret:
    """Secrets must never render in full."""

    def test_long_secret_shows_tail_only(self):
        assert _mask_secret("tok-abcdef123456") == "••••3456"

    def test_short_secret_fully_masked(self):
        assert _mask_secret("abc123") == "••••"

    def test_no_tail_mode_for_passwords(self):
        assert _mask_secret("a-very-long-password-here", show_tail=0) == "••••"

    def test_empty(self):
        assert _mask_secret("") == ""


class TestValidators:
    """Input validators return error text or None."""

    def test_email_valid(self):
        assert _validate_email("ivan.petrov@megacorp.ru") is None

    def test_email_no_at(self):
        assert _validate_email("ivan.petrov") is not None

    def test_email_bare_domain(self):
        assert _validate_email("ivan@corp") is not None

    def test_url_valid(self):
        assert _validate_url("https://owa.corp.ru/EWS/Exchange.asmx") is None
        assert _validate_url("http://llm.corp.ru/v1") is None

    def test_url_missing_scheme(self):
        assert _validate_url("owa.corp.ru/EWS") is not None

    def test_url_with_spaces(self):
        assert _validate_url("https://owa corp.ru") is not None


class TestDeriveFromEmail:
    """Test email -> EWS field derivation."""

    def test_simple_email(self):
        result = _derive_from_email("ivan.petrov@megacorp.ru")
        assert result["user_login"] == "ivan.petrov"
        assert result["user_domain"] == "megacorp.ru"
        assert result["default_ews_endpoint"] == "https://owa.megacorp.ru/EWS/Exchange.asmx"
        assert "Ivan" in result["aliases"]
        assert "Petrov" in result["aliases"]
        assert "ivan.petrov@megacorp.ru" in result["aliases"]
        assert "ivan.petrov" in result["aliases"]

    def test_single_part_login(self):
        result = _derive_from_email("admin@corp.com")
        assert result["user_login"] == "admin"
        assert result["user_domain"] == "corp.com"
        assert "Admin" in result["aliases"]
        assert "admin@corp.com" in result["aliases"]

    def test_hyphen_in_login(self):
        result = _derive_from_email("ivan-petrov@corp.ru")
        assert result["user_login"] == "ivan-petrov"
        assert result["user_domain"] == "corp.ru"
        assert "Ivan" in result["aliases"]
        assert "Petrov" in result["aliases"]

    def test_underscore_in_login(self):
        result = _derive_from_email("i_petrov@corp.ru")
        assert result["user_login"] == "i_petrov"
        assert result["user_domain"] == "corp.ru"
        # "i" is 1 char -> filtered out by len(p) > 1
        assert "Petrov" in result["aliases"]


class TestWriteEnvFile:
    """Test env file generation."""

    def test_writes_env_file(self, tmp_path, monkeypatch):
        env_dir = tmp_path / ".config" / "actionpulse"
        env_path = env_dir / "env"

        monkeypatch.setattr("digest_core.setup_wizard.ENV_DIR", env_dir)
        monkeypatch.setattr("digest_core.setup_wizard.ENV_PATH", env_path)

        values = {
            "EWS_PASSWORD": "secret123",
            "EWS_USER_UPN": "test@corp.ru",
            "EWS_ENDPOINT": "https://owa.corp.ru/EWS/Exchange.asmx",
            "LLM_TOKEN": "tok-abc",
            "LLM_ENDPOINT": "https://llm.corp.ru/api/v1/chat",
            "MM_WEBHOOK_URL": "https://mm.corp.ru/hooks/xxx",
        }
        result = _write_env_file(values)

        assert result.exists()
        content = result.read_text()

        # Check format: KEY=value, no export, no quotes
        assert "EWS_PASSWORD=secret123" in content
        assert "EWS_USER_UPN=test@corp.ru" in content
        assert "LLM_TOKEN=tok-abc" in content
        assert "MM_WEBHOOK_URL=https://mm.corp.ru/hooks/xxx" in content

        # No export prefix
        assert "export " not in content

        # Check permissions (600)
        mode = oct(result.stat().st_mode)[-3:]
        assert mode == "600"

    def test_systemd_compatible_format(self, tmp_path, monkeypatch):
        """Env file must work with systemd EnvironmentFile=."""
        env_dir = tmp_path / ".config" / "actionpulse"
        env_path = env_dir / "env"
        monkeypatch.setattr("digest_core.setup_wizard.ENV_DIR", env_dir)
        monkeypatch.setattr("digest_core.setup_wizard.ENV_PATH", env_path)

        _write_env_file({"EWS_PASSWORD": "p@ss w0rd!", "LLM_TOKEN": "tok"})
        content = env_path.read_text()

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # systemd format: KEY=value (no export, no quotes around value)
            assert "=" in line
            assert not line.startswith("export ")

    def test_ntlm_hint_written_commented(self, tmp_path, monkeypatch):
        """AD-login hint is a comment — never an active override by default."""
        env_dir = tmp_path / ".config" / "actionpulse"
        env_path = env_dir / "env"
        monkeypatch.setattr("digest_core.setup_wizard.ENV_DIR", env_dir)
        monkeypatch.setattr("digest_core.setup_wizard.ENV_PATH", env_path)

        _write_env_file({"EWS_PASSWORD": "x"}, ntlm_login_hint="ruapgr2")
        content = env_path.read_text()
        assert "# EWS_USER_LOGIN=ruapgr2" in content
        assert "\nEWS_USER_LOGIN=" not in content  # commented only

    def test_no_ntlm_hint_by_default(self, tmp_path, monkeypatch):
        env_dir = tmp_path / ".config" / "actionpulse"
        env_path = env_dir / "env"
        monkeypatch.setattr("digest_core.setup_wizard.ENV_DIR", env_dir)
        monkeypatch.setattr("digest_core.setup_wizard.ENV_PATH", env_path)

        _write_env_file({"EWS_PASSWORD": "x"})
        assert "EWS_USER_LOGIN" not in env_path.read_text()


class TestMmWebhookCheck:
    """Live Mattermost webhook probe — mocked transport."""

    def _resp(self, status):
        class R:
            status_code = status

        return R()

    def test_ok_on_200(self, monkeypatch):
        from digest_core.setup_wizard import _test_mm_webhook

        monkeypatch.setattr("httpx.post", lambda url, json, timeout: self._resp(200))
        ok, detail = _test_mm_webhook("https://mm.corp.ru/hooks/x")
        assert ok and detail == "delivered"

    def test_http_error_reported(self, monkeypatch):
        from digest_core.setup_wizard import _test_mm_webhook

        monkeypatch.setattr("httpx.post", lambda url, json, timeout: self._resp(404))
        ok, detail = _test_mm_webhook("https://mm.corp.ru/hooks/x")
        assert not ok and detail == "HTTP 404"

    def test_network_failure_is_soft(self, monkeypatch):
        from digest_core.setup_wizard import _test_mm_webhook

        def boom(url, json, timeout):
            raise ConnectionError("no route")

        monkeypatch.setattr("httpx.post", boom)
        ok, detail = _test_mm_webhook("https://mm.corp.ru/hooks/x")
        assert not ok and detail == "ConnectionError"


class TestWriteConfigYaml:
    """Test config.yaml generation."""

    def test_generates_valid_yaml(self, tmp_path, monkeypatch):
        # Create a minimal example config
        example = tmp_path / "config.example.yaml"
        example_data = {
            "ews": {
                "endpoint": "https://placeholder",
                "user_upn": "placeholder@corp.ru",
                "user_login": "placeholder",
                "user_domain": "corp.ru",
                "verify_ca": "/etc/ssl/corp-ca.pem",
                "user_aliases": [],
            },
            "llm": {
                "endpoint": "https://placeholder",
                "model": "qwen35-397b-a17b",
            },
        }
        with open(example, "w") as f:
            yaml.dump(example_data, f)

        user_config = tmp_path / "config.yaml"
        monkeypatch.setattr("digest_core.setup_wizard.CONFIG_EXAMPLE", example)
        monkeypatch.setattr("digest_core.setup_wizard.CONFIG_USER", user_config)

        derived = _derive_from_email("ivan@megacorp.ru")
        result = _write_config_yaml(
            user_upn="ivan@megacorp.ru",
            ews_endpoint="https://owa.megacorp.ru/EWS/Exchange.asmx",
            llm_endpoint="https://llm.megacorp.ru/api/v1/chat",
            derived=derived,
            verify_ca=None,
        )

        assert result.exists()
        with open(result) as f:
            config = yaml.safe_load(f)

        assert config["ews"]["endpoint"] == "https://owa.megacorp.ru/EWS/Exchange.asmx"
        assert config["ews"]["user_upn"] == "ivan@megacorp.ru"
        assert config["ews"]["user_login"] == "ivan"
        assert config["ews"]["user_domain"] == "megacorp.ru"
        assert config["llm"]["endpoint"] == "https://llm.megacorp.ru/api/v1/chat"
        # verify_ca=None -> removed from config
        assert "verify_ca" not in config["ews"]

    def test_preserves_ca_cert(self, tmp_path, monkeypatch):
        example = tmp_path / "config.example.yaml"
        example_data = {"ews": {"verify_ca": "/old/path"}, "llm": {}}
        with open(example, "w") as f:
            yaml.dump(example_data, f)

        user_config = tmp_path / "config.yaml"
        monkeypatch.setattr("digest_core.setup_wizard.CONFIG_EXAMPLE", example)
        monkeypatch.setattr("digest_core.setup_wizard.CONFIG_USER", user_config)

        derived = _derive_from_email("user@corp.ru")
        _write_config_yaml(
            user_upn="user@corp.ru",
            ews_endpoint="https://ews.corp.ru",
            llm_endpoint="https://llm.corp.ru",
            derived=derived,
            verify_ca="/etc/ssl/my-ca.pem",
        )

        with open(user_config) as f:
            config = yaml.safe_load(f)
        assert config["ews"]["verify_ca"] == "/etc/ssl/my-ca.pem"

    def _write_with_dm(self, tmp_path, monkeypatch, **dm):
        example = tmp_path / "config.example.yaml"
        with open(example, "w") as f:
            yaml.dump({"ews": {}, "llm": {}, "mm_source": {"enabled": True}}, f)
        user_config = tmp_path / "config.yaml"
        monkeypatch.setattr("digest_core.setup_wizard.CONFIG_EXAMPLE", example)
        monkeypatch.setattr("digest_core.setup_wizard.CONFIG_USER", user_config)
        derived = _derive_from_email("user@corp.ru")
        _write_config_yaml(
            user_upn="user@corp.ru",
            ews_endpoint="https://ews.corp.ru",
            llm_endpoint="https://llm.corp.ru",
            derived=derived,
            verify_ca=None,
            **dm,
        )
        with open(user_config) as f:
            return yaml.safe_load(f)

    def test_dm_defaults_off(self, tmp_path, monkeypatch):
        config = self._write_with_dm(tmp_path, monkeypatch)
        assert config["mm_source"]["dm_scope"] == "off"
        assert config["mm_source"]["dm_allowlist"] == []
        assert config["mm_source"]["dm_consent_acknowledged"] is False
        assert config["mm_source"]["dm_consent_acknowledged_at"] is None
        # The pre-existing mm_source key survives the merge.
        assert config["mm_source"]["enabled"] is True
        # And the written block loads through the model validator.
        MattermostSourceConfig(**config["mm_source"])

    def test_dm_selected_with_consent(self, tmp_path, monkeypatch):
        config = self._write_with_dm(
            tmp_path,
            monkeypatch,
            dm_scope="selected",
            dm_allowlist=["@ann", "bob@corp.ru"],
            dm_consent_acknowledged=True,
            dm_consent_acknowledged_at="2026-06-18T12:00:00+00:00",
        )
        block = config["mm_source"]
        assert block["dm_scope"] == "selected"
        assert block["dm_allowlist"] == ["@ann", "bob@corp.ru"]
        assert block["dm_consent_acknowledged"] is True
        model = MattermostSourceConfig(**block)
        assert model.dm_scope == "selected"


class TestDmSummaryValue:
    """The summary-table DM row text."""

    def test_off(self):
        assert _dm_summary_value("off", []) == "Off"

    def test_own_posts(self):
        assert _dm_summary_value("own_posts_only", []) == "My posts only"

    def test_selected_with_partners(self):
        assert "3 partners" in _dm_summary_value("selected", ["a", "b", "c"])

    def test_selected_one_partner_singular(self):
        assert "1 partner)" in _dm_summary_value("selected", ["a"])

    def test_selected_empty_flags_off(self):
        text = _dm_summary_value("selected", [])
        assert "effectively OFF" in text

    def test_all(self):
        assert _dm_summary_value("all", []) == "All DMs"


class TestDmStep:
    """Wizard DM sub-section flow (thin glue over the tested helpers)."""

    def _patch_tty(self, monkeypatch, scope):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr(wiz, "choose", lambda *a, **k: scope)

    def test_off_no_consent(self, monkeypatch):
        self._patch_tty(monkeypatch, "off")
        result = _dm_step({})
        assert result.scope == "off"
        assert result.consent_acknowledged is False
        assert result.consent_acknowledged_at is None

    def test_own_posts_no_consent(self, monkeypatch):
        self._patch_tty(monkeypatch, "own_posts_only")
        result = _dm_step({"mm_source": {"dm_scope": "selected"}})
        assert result.scope == "own_posts_only"
        assert result.consent_acknowledged is False

    def test_selected_consent_declined_falls_back_off(self, monkeypatch):
        self._patch_tty(monkeypatch, "selected")
        monkeypatch.setattr(wiz, "_dm_consent_panel", lambda con: False)
        result = _dm_step({})
        assert result.scope == "off"
        assert result.consent_acknowledged is False

    def test_selected_consent_accepted_sets_timestamp(self, monkeypatch):
        self._patch_tty(monkeypatch, "selected")
        monkeypatch.setattr(wiz, "_dm_consent_panel", lambda con: True)
        monkeypatch.setattr(wiz, "_ask_dm_partners", lambda con, cur: ["@ann"])
        result = _dm_step({})
        assert result.scope == "selected"
        assert result.allowlist == ["@ann"]
        assert result.consent_acknowledged is True
        assert result.consent_acknowledged_at is not None

    def test_selected_empty_list_keeps_selected(self, monkeypatch):
        self._patch_tty(monkeypatch, "selected")
        monkeypatch.setattr(wiz, "_dm_consent_panel", lambda con: True)
        monkeypatch.setattr(wiz, "_ask_dm_partners", lambda con, cur: [])
        result = _dm_step({})
        assert result.scope == "selected"
        assert result.allowlist == []
        assert result.consent_acknowledged is True

    def test_all_consent_declined_falls_back_off(self, monkeypatch):
        self._patch_tty(monkeypatch, "all")
        monkeypatch.setattr(wiz, "_dm_consent_panel", lambda con: False)
        result = _dm_step({})
        assert result.scope == "off"

    def test_all_confirm_declined_falls_back_off(self, monkeypatch):
        self._patch_tty(monkeypatch, "all")
        monkeypatch.setattr(wiz, "_dm_consent_panel", lambda con: True)
        monkeypatch.setattr(wiz.Confirm, "ask", staticmethod(lambda *a, **k: False))
        result = _dm_step({})
        assert result.scope == "off"

    def test_all_both_yes_sets_all(self, monkeypatch):
        self._patch_tty(monkeypatch, "all")
        monkeypatch.setattr(wiz, "_dm_consent_panel", lambda con: True)
        monkeypatch.setattr(wiz.Confirm, "ask", staticmethod(lambda *a, **k: True))
        result = _dm_step({})
        assert result.scope == "all"
        assert result.consent_acknowledged is True
        assert result.consent_acknowledged_at is not None


class TestReadExistingEnv:
    """Test reading existing env files for defaults."""

    def test_reads_key_value(self, tmp_path, monkeypatch):
        env_file = tmp_path / "env"
        env_file.write_text("EWS_PASSWORD=secret\nLLM_TOKEN=tok\n# comment\n\n")
        monkeypatch.setattr("digest_core.setup_wizard.ENV_PATH", env_file)

        result = _read_existing_env()
        assert result["EWS_PASSWORD"] == "secret"
        assert result["LLM_TOKEN"] == "tok"

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("digest_core.setup_wizard.ENV_PATH", tmp_path / "nonexistent")
        result = _read_existing_env()
        assert result == {}


class TestAutoDetectCaPath:
    """Test automatic CA certificate path detection."""

    def test_prefers_existing_config_path_when_present(self, tmp_path, monkeypatch):
        ca_path = tmp_path / "corp-ca.pem"
        ca_path.write_text("dummy", encoding="utf-8")
        existing_cfg = {"ews": {"verify_ca": str(ca_path)}}

        result = _auto_detect_ca_path(existing_cfg)
        assert result == str(ca_path)

    def test_falls_back_to_home_ssl_path(self, tmp_path, monkeypatch):
        fake_home = tmp_path / "home"
        ca_path = fake_home / ".ssl" / "corp-ca.pem"
        ca_path.parent.mkdir(parents=True)
        ca_path.write_text("dummy", encoding="utf-8")
        monkeypatch.setattr("digest_core.setup_wizard.Path.home", lambda: fake_home)

        result = _auto_detect_ca_path(existing_cfg={})
        assert result == str(ca_path)


class TestSetupCommand:
    """Test the CLI setup command integration."""

    def test_cli_help_includes_setup(self):
        """Verify setup command is registered in CLI."""
        from typer.testing import CliRunner
        from digest_core.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["setup", "--help"])
        assert result.exit_code == 0
        assert "setup" in result.output.lower() or "interactive" in result.output.lower()


class TestResolveEwsLogin:
    """EWS NTLM login = machine (AD) login, not the email local part."""

    def test_machine_login_beats_email_local_part(self):
        # The reference case: whoami=ruapgr2, email=Ruslan.POGORELOV@megacorp.ru
        assert _resolve_ews_login(None, "ruapgr2", "ruslan.pogorelov") == "ruapgr2"

    def test_existing_config_wins(self):
        assert _resolve_ews_login("manual-login", "ruapgr2", "x") == "manual-login"

    def test_falls_back_to_email_local_part(self):
        assert _resolve_ews_login(None, None, "ivan.petrov") == "ivan.petrov"
        assert _resolve_ews_login("", "", "ivan.petrov") == "ivan.petrov"

    def test_strips_whitespace(self):
        assert _resolve_ews_login(None, "  ruapgr2  ", "x") == "ruapgr2"


class TestEnsureLauncher:
    """The finale may only advertise `actionpulse` when the command works;
    `make setup` from a bare checkout self-heals by writing the shim."""

    def _no_global(self, monkeypatch, tmp_path, uv="/usr/bin/uv"):
        import digest_core.setup_wizard as wizard_mod

        launcher = tmp_path / "bin" / "actionpulse"
        monkeypatch.setattr(wizard_mod, "LAUNCHER_PATH", launcher)
        monkeypatch.setattr(
            wizard_mod.shutil,
            "which",
            lambda name: uv if name == "uv" else None,
        )
        return wizard_mod, launcher

    def test_on_path_short_circuits(self, monkeypatch, tmp_path):
        import digest_core.setup_wizard as wizard_mod

        launcher = tmp_path / "bin" / "actionpulse"
        monkeypatch.setattr(wizard_mod, "LAUNCHER_PATH", launcher)
        monkeypatch.setattr(wizard_mod.shutil, "which", lambda name: f"/fake/{name}")
        state = wizard_mod._ensure_launcher()
        assert state.use_command and not state.created and not state.path_hint
        assert not launcher.exists()  # nothing written when the command resolves

    def test_creates_shim_when_missing(self, monkeypatch, tmp_path):
        wizard_mod, launcher = self._no_global(monkeypatch, tmp_path)
        monkeypatch.setenv("PATH", "/usr/bin")  # shim dir NOT on PATH
        state = wizard_mod._ensure_launcher()
        assert state.use_command and state.created and state.path_hint
        content = launcher.read_text()
        assert "digest_core.cli" in content
        assert str(wizard_mod.PROJECT_ROOT) in content
        assert launcher.stat().st_mode & 0o111  # executable

    def test_existing_shim_never_overwritten(self, monkeypatch, tmp_path):
        wizard_mod, launcher = self._no_global(monkeypatch, tmp_path)
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\n# sentinel: another checkout\n")
        monkeypatch.setenv("PATH", f"/usr/bin:{launcher.parent}")
        state = wizard_mod._ensure_launcher()
        assert state.use_command and not state.created and not state.path_hint
        assert "sentinel" in launcher.read_text()

    def test_no_uv_falls_back_to_module_form(self, monkeypatch, tmp_path):
        wizard_mod, launcher = self._no_global(monkeypatch, tmp_path, uv=None)
        state = wizard_mod._ensure_launcher()
        assert not state.use_command
        assert not launcher.exists()


class TestNextStepsText:
    """The Done panel never confuses the user with the module invocation."""

    def test_actionpulse_primary(self):
        from digest_core.setup_wizard import LauncherState, _next_steps_text

        text = _next_steps_text(LauncherState(True, False, False)).plain
        assert "actionpulse run --dry-run" in text
        assert "actionpulse diagnose" in text
        assert "python -m digest_core.cli" not in text
        assert "source" not in text or "no manual source" in text  # auto-load, no source dance

    def test_path_hint_shown_only_when_off_path(self):
        from digest_core.setup_wizard import LauncherState, _next_steps_text

        with_hint = _next_steps_text(LauncherState(True, True, True)).plain
        without = _next_steps_text(LauncherState(True, False, False)).plain
        assert "PATH" in with_hint and "~/.zshrc" in with_hint
        assert "~/.zshrc" not in without

    def test_module_form_only_in_no_launcher_fallback(self):
        from digest_core.setup_wizard import LauncherState, _next_steps_text

        text = _next_steps_text(LauncherState(False, False, False)).plain
        assert "uv run python -m digest_core.cli run --dry-run" in text
        # Never advertise a command that won't resolve (the env *path* still
        # contains "actionpulse" — check command forms, not the bare substring).
        assert "actionpulse run" not in text
        assert "actionpulse diagnose" not in text
