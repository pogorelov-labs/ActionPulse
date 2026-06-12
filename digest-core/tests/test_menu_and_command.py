"""Interactive menu + env autoload + bare `actionpulse` invocation."""

import os

from rich.console import Console
from typer.testing import CliRunner

import digest_core.cli as cli_mod
from digest_core.cli import app
from digest_core.ui import menu as menu_mod
from digest_core.ui.menu import _mask, load_env_file, run_menu


def _console() -> Console:
    from digest_core.ui import THEME

    return Console(record=True, width=100, force_terminal=False, theme=THEME)


class TestLoadEnvFile:
    def test_loads_unset_keys(self, tmp_path, monkeypatch):
        env = tmp_path / "env"
        env.write_text("# comment\nEWS_PASSWORD=secret\nLLM_TOKEN=tok\n\nBAD LINE\n")
        monkeypatch.delenv("EWS_PASSWORD", raising=False)
        monkeypatch.delenv("LLM_TOKEN", raising=False)
        n = load_env_file(env)
        assert n == 2
        assert os.environ["EWS_PASSWORD"] == "secret"
        assert os.environ["LLM_TOKEN"] == "tok"

    def test_never_overrides_explicit_env(self, tmp_path, monkeypatch):
        env = tmp_path / "env"
        env.write_text("LLM_TOKEN=from-file\n")
        monkeypatch.setenv("LLM_TOKEN", "from-shell")
        load_env_file(env)
        assert os.environ["LLM_TOKEN"] == "from-shell"

    def test_missing_file_is_noop(self, tmp_path):
        assert load_env_file(tmp_path / "nope") == 0


class TestMask:
    def test_secrets_masked(self):
        assert _mask("LLM_TOKEN", "tok-abcdef123456") == "••••3456"
        assert _mask("EWS_PASSWORD", "short") == "••••"

    def test_webhook_keeps_host_hides_token(self):
        assert _mask("MM_WEBHOOK_URL", "https://mm.corp.ru/hooks/abc123def456") == (
            "https://mm.corp.ru/hooks/••••"
        )

    def test_non_secret_passthrough(self):
        assert _mask("EWS_USER_UPN", "ivan@corp.ru") == "ivan@corp.ru"


class TestRunMenu:
    """choose() is monkeypatched to script the user's selections."""

    def _scripted(self, monkeypatch, choices):
        seq = iter(choices)

        def fake_choose(label, options, default_index=0, console=None):
            try:
                return next(seq)
            except StopIteration:
                return "quit"

        monkeypatch.setattr(menu_mod, "choose", fake_choose)

    def test_dispatches_each_action_then_quits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(menu_mod, "ENV_PATH", tmp_path / "env")
        (tmp_path / "env").write_text("EWS_USER_UPN=ivan@corp.ru\nLLM_TOKEN=tok-abcdef123456\n")
        calls = {"run": [], "diag": 0, "settings": 0}
        # menu shows the action, then a "back" prompt; script both.
        self._scripted(
            monkeypatch,
            [
                "run",
                "back",
                "dry",
                "back",
                "diagnose",
                "back",
                "settings",
                "back",
                "config",
                "back",
                "quit",
            ],
        )
        code = run_menu(
            on_run=lambda dry: calls["run"].append(dry),
            on_diagnose=lambda: calls.__setitem__("diag", calls["diag"] + 1),
            on_settings=lambda: calls.__setitem__("settings", calls["settings"] + 1),
            console=_console(),
        )
        assert code == 0
        assert calls["run"] == [False, True]  # run then dry
        assert calls["diag"] == 1
        assert calls["settings"] == 1

    def test_action_error_keeps_menu_alive(self, monkeypatch):
        self._scripted(monkeypatch, ["run", "back", "quit"])

        def boom(_dry):
            raise RuntimeError("ews unreachable")

        console = _console()
        code = run_menu(
            on_run=boom,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            console=console,
        )
        assert code == 0
        assert "ews unreachable" in console.export_text()

    def test_keyboardinterrupt_at_menu_exits_cleanly(self, monkeypatch):
        def interrupt(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(menu_mod, "choose", interrupt)
        code = run_menu(
            on_run=lambda d: None,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            console=_console(),
        )
        assert code == 0


class TestBareInvocation:
    def test_non_tty_prints_help_not_menu(self, monkeypatch):
        # CliRunner stdin is not a tty -> help, no menu, exit 0.
        called = {"menu": False}
        monkeypatch.setattr(cli_mod, "run_menu", lambda **k: called.__setitem__("menu", True) or 0)
        result = CliRunner().invoke(app, [])
        assert result.exit_code == 0
        assert called["menu"] is False
        assert "Usage" in result.output or "Commands" in result.output

    def test_subcommand_still_works_through_callback(self, monkeypatch):
        # The callback (env autoload) must not swallow subcommands.
        monkeypatch.setattr(cli_mod, "load_env_file", lambda *a, **k: 0)
        result = CliRunner().invoke(app, ["setup", "--help"])
        assert result.exit_code == 0
        assert "setup" in result.output.lower()

    def test_callback_autoloads_env(self, monkeypatch):
        # Bare invocation runs the callback body (unlike eager --help); non-tty
        # then falls through to help. Autoload must have fired.
        seen = {"loaded": False}
        monkeypatch.setattr(
            cli_mod, "load_env_file", lambda *a, **k: seen.__setitem__("loaded", True) or 0
        )
        CliRunner().invoke(app, [])
        assert seen["loaded"] is True
