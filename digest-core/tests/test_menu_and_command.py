"""Interactive menu + env autoload + bare `actionpulse` invocation."""

import os
from datetime import datetime, timedelta, timezone

import yaml
from rich.console import Console
from typer.testing import CliRunner

import digest_core.cli as cli_mod
from digest_core.cli import app
from digest_core.config import MattermostSourceConfig
from digest_core.ui import menu as menu_mod
from digest_core.ui.menu import (
    RunChoice,
    _dm_consent_status,
    _mm_dm_menu,
    _mask,
    _read_dm_state,
    choose_run_options,
    load_env_file,
    load_last_run,
    run_menu,
    save_last_run,
)


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

    def _scripted(self, monkeypatch, choices, seen_kwargs=None):
        seq = iter(choices)

        def fake_choose(label, options, default_index=0, console=None, cancel_value=None):
            if seen_kwargs is not None:
                seen_kwargs.append({"label": label, "cancel_value": cancel_value})
            try:
                return next(seq)
            except StopIteration:
                return "quit"

        monkeypatch.setattr(menu_mod, "choose", fake_choose)
        # The post-action gate is a plain Enter prompt now, not a menu.
        monkeypatch.setattr(menu_mod.Console, "input", lambda self, *a, **k: "", raising=False)

    def test_dispatches_each_action_then_quits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(menu_mod, "ENV_PATH", tmp_path / "env")
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", tmp_path / "last_run.json")
        (tmp_path / "env").write_text("EWS_USER_UPN=ivan@corp.ru\nLLM_TOKEN=tok-abcdef123456\n")
        calls = {"run": [], "diag": 0, "settings": 0, "read": []}
        # "run" opens the U3 submenu, then the post-run "read now?" offer.
        self._scripted(
            monkeypatch,
            ["run", "today", "menu", "dry", "diagnose", "settings", "config", "quit"],
        )
        code = run_menu(
            on_run=lambda dry, choice: calls["run"].append((dry, choice)),
            on_diagnose=lambda: calls.__setitem__("diag", calls["diag"] + 1),
            on_settings=lambda: calls.__setitem__("settings", calls["settings"] + 1),
            on_read=lambda date: calls["read"].append(date),
            console=_console(),
        )
        assert code == 0
        assert calls["run"] == [(False, RunChoice()), (True, None)]  # run then dry
        assert calls["diag"] == 1
        assert calls["settings"] == 1
        assert calls["read"] == []  # the post-run offer was declined
        # The accepted choice persisted for "Repeat last run".
        assert load_last_run(tmp_path / "last_run.json") == RunChoice()

    def test_post_run_offer_opens_reader_with_absolute_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", tmp_path / "last_run.json")
        calls = {"read": []}
        self._scripted(monkeypatch, ["run", "yesterday", "read", "quit"])
        run_menu(
            on_run=lambda dry, choice: None,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: calls["read"].append(date),
            console=_console(),
        )
        # Yesterday resolved to an absolute date -> the reader opens that day.
        assert len(calls["read"]) == 1 and calls["read"][0] is not None

    def test_read_menu_item_opens_newest(self, monkeypatch):
        calls = {"read": []}
        self._scripted(monkeypatch, ["read", "quit"])
        run_menu(
            on_run=lambda dry, choice: None,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: calls["read"].append(date),
            console=_console(),
        )
        assert calls["read"] == [None]

    def test_run_submenu_back_returns_to_menu_without_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", tmp_path / "last_run.json")
        calls = {"run": 0}
        self._scripted(monkeypatch, ["run", "back", "quit"])
        code = run_menu(
            on_run=lambda dry, choice: calls.__setitem__("run", calls["run"] + 1),
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            console=_console(),
        )
        assert code == 0
        assert calls["run"] == 0  # backing out never runs anything
        assert not (tmp_path / "last_run.json").exists()

    def test_failed_run_does_not_persist_last_choice(self, tmp_path, monkeypatch):
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", tmp_path / "last_run.json")
        self._scripted(monkeypatch, ["run", "today", "quit"])

        def boom(_dry, _choice):
            raise RuntimeError("ews unreachable")

        console = _console()
        code = run_menu(
            on_run=boom,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            console=console,
        )
        assert code == 0
        assert "ews unreachable" in console.export_text()
        assert not (tmp_path / "last_run.json").exists()

    def test_keyboardinterrupt_at_menu_aborts_130(self, monkeypatch):
        # §5.5 abort contract: Ctrl+C -> 130 everywhere.
        def interrupt(*a, **k):
            raise KeyboardInterrupt

        monkeypatch.setattr(menu_mod, "choose", interrupt)
        code = run_menu(
            on_run=lambda d, c: None,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            console=_console(),
        )
        assert code == 130

    def test_menu_passes_cancel_value_quit(self, monkeypatch):
        # §5.2: Esc at the launcher menu must dismiss, never commit "run".
        seen = []
        self._scripted(monkeypatch, ["quit"], seen_kwargs=seen)
        run_menu(
            on_run=lambda d, c: None,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            console=_console(),
        )
        assert seen and seen[0]["cancel_value"] == "quit"


class TestRetrievalRows:
    """Search/Ask rows are gated on the store being enabled (M2 — the retrieval pillar was
    invisible to menu-driven users)."""

    def test_options_hidden_without_store(self):
        keys = [k for k, _ in menu_mod._main_menu_options(False)]
        assert "search" not in keys and "ask" not in keys

    def test_options_shown_with_store(self):
        keys = [k for k, _ in menu_mod._main_menu_options(True)]
        assert "search" in keys and "ask" in keys
        assert len(keys) <= 12  # stays a tidy list (quick-select stays sane)

    def test_search_row_invokes_callback_with_query(self, monkeypatch):
        seq = iter(["search", "quit"])
        monkeypatch.setattr(menu_mod, "choose", lambda *a, **k: next(seq, "quit"))
        monkeypatch.setattr(
            menu_mod.Console, "input", lambda self, *a, **k: "budget?", raising=False
        )
        captured = []
        run_menu(
            on_run=lambda d, c: None,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            on_search=lambda q: captured.append(q),
            on_ask=lambda q: None,
            store_enabled=True,
            console=_console(),
        )
        assert captured == ["budget?"]

    def test_rows_absent_when_store_disabled_even_with_callbacks(self, monkeypatch):
        seq = iter(["search", "quit"])  # 'search' is not an option → falls through harmlessly
        monkeypatch.setattr(menu_mod, "choose", lambda *a, **k: next(seq, "quit"))
        monkeypatch.setattr(menu_mod.Console, "input", lambda self, *a, **k: "", raising=False)
        captured = []
        run_menu(
            on_run=lambda d, c: None,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            on_search=lambda q: captured.append(q),
            on_ask=lambda q: None,
            store_enabled=False,  # disabled → no rows, callback never reachable
            console=_console(),
        )
        assert captured == []


class TestBareInvocation:
    def test_non_tty_prints_help_not_menu(self, monkeypatch):
        # CliRunner stdin is not a tty -> help, no menu, exit 0.
        called = {"menu": False}
        monkeypatch.setattr(cli_mod, "run_menu", lambda **k: called.__setitem__("menu", True) or 0)
        result = CliRunner().invoke(app, [])
        assert result.exit_code == 0
        assert called["menu"] is False
        assert "Usage" in result.output or "Commands" in result.output

    def test_eval_commands_hidden_but_user_commands_shown(self, monkeypatch):
        # M1: the 8 eval-* research commands clutter --help; hidden=True removes them from
        # the listing (still callable for CI). User-facing commands stay visible.
        monkeypatch.setattr(cli_mod, "load_env_file", lambda *a, **k: 0)
        out = CliRunner().invoke(app, ["--help"]).output
        assert "eval-prompt" not in out and "eval-calibrate" not in out
        assert "search" in out and "ask" in out and "run" in out

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
            cli_mod,
            "load_env_file",
            lambda *a, **k: seen.__setitem__("loaded", True) or 0,
        )
        CliRunner().invoke(app, [])
        assert seen["loaded"] is True


class TestRunChoicePersistence:
    """U3: the accepted run params persist for "Repeat last run"."""

    def test_round_trip(self, tmp_path):
        path = tmp_path / "last_run.json"
        save_last_run(RunChoice(from_date="2026-06-10", window="rolling_24h", force=True), path)
        loaded = load_last_run(path)
        assert loaded == RunChoice(from_date="2026-06-10", window="rolling_24h", force=True)

    def test_missing_file_is_none(self, tmp_path):
        assert load_last_run(tmp_path / "nope.json") is None

    def test_invalid_json_is_none(self, tmp_path):
        path = tmp_path / "last_run.json"
        path.write_text("{not json")
        assert load_last_run(path) is None

    def test_unknown_window_is_none(self, tmp_path):
        path = tmp_path / "last_run.json"
        path.write_text('{"from_date": "today", "window": "fortnight"}')
        assert load_last_run(path) is None


class TestChooseRunOptions:
    """U3 selector: one menu, smart defaults, Esc/Back never runs."""

    def _scripted_choose(self, monkeypatch, value, seen=None):
        def fake_choose(label, options, default_index=0, console=None, cancel_value=None):
            if seen is not None:
                seen.append({"options": options, "cancel_value": cancel_value})
            return value

        monkeypatch.setattr(menu_mod, "choose", fake_choose)

    def test_mappings(self, monkeypatch):
        cases = {
            "today": RunChoice(),
            "24h": RunChoice(window="rolling_24h"),
            "force": RunChoice(force=True),
            "back": None,
        }
        for value, expected in cases.items():
            self._scripted_choose(monkeypatch, value)
            assert choose_run_options(_console(), last=None) == expected

    def test_yesterday_uses_absolute_date(self, monkeypatch):
        self._scripted_choose(monkeypatch, "yesterday")
        choice = choose_run_options(_console(), last=None)
        assert choice.window == "calendar_day"
        # An actual YYYY-MM-DD, not the word "yesterday" (run.py validates).
        from datetime import datetime

        datetime.strptime(choice.from_date, "%Y-%m-%d")

    def test_repeat_last_offered_only_when_present(self, monkeypatch):
        seen = []
        self._scripted_choose(monkeypatch, "back", seen=seen)
        choose_run_options(_console(), last=None)
        values = [v for v, _ in seen[0]["options"]]
        assert "last" not in values
        assert len(values) <= 9  # the §5.2 quick-select invariant

        last = RunChoice(from_date="2026-06-10", window="rolling_24h", force=True)
        choose_run_options(_console(), last=last)
        labels = dict(seen[1]["options"])
        assert "last" in labels
        # The label shows the absolute stored params — no silent drift.
        assert "2026-06-10" in labels["last"]
        assert "rolling 24h" in labels["last"]
        assert "force" in labels["last"]
        assert len(seen[1]["options"]) <= 9

    def test_submenu_esc_backs_out(self, monkeypatch):
        seen = []
        self._scripted_choose(monkeypatch, "back", seen=seen)
        assert choose_run_options(_console(), last=None) is None
        assert seen[0]["cancel_value"] == "back"  # Esc dismisses, never runs

    def test_pick_a_date_validates(self, monkeypatch):
        self._scripted_choose(monkeypatch, "date")
        answers = iter(["06/11/2026", "2026-06-11"])
        monkeypatch.setattr(
            menu_mod.Console,
            "input",
            lambda self, *a, **k: next(answers),
            raising=False,
        )
        console = _console()
        choice = choose_run_options(console, last=None)
        assert choice == RunChoice(from_date="2026-06-11", window="calendar_day")
        assert "Expected YYYY-MM-DD" in console.export_text()

    def test_pick_a_date_empty_backs_out(self, monkeypatch):
        self._scripted_choose(monkeypatch, "date")
        monkeypatch.setattr(menu_mod.Console, "input", lambda self, *a, **k: "", raising=False)
        assert choose_run_options(_console(), last=None) is None


class TestMenuRunWiring:
    """cli._menu_run forwards the U3 choice into the pipeline call."""

    def test_choice_params_reach_run_digest(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(cli_mod, "run_digest", lambda **k: captured.update(k) or True)
        monkeypatch.setattr(cli_mod, "setup_logging", lambda **k: None)
        cli_mod._menu_run(
            False, RunChoice(from_date="2026-06-10", window="rolling_24h", force=True)
        )
        assert captured["from_date"] == "2026-06-10"
        assert captured["window"] == "rolling_24h"
        assert captured["force"] is True

    def test_dry_run_defaults_without_choice(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(cli_mod, "run_digest_dry_run", lambda **k: captured.update(k))
        monkeypatch.setattr(cli_mod, "setup_logging", lambda **k: None)
        cli_mod._menu_run(True, None)
        assert captured["from_date"] == "today"
        assert captured["window"] == "calendar_day"
        assert captured["force"] is False


class TestReadDmState:
    """Defensive reads of the mm_source.dm_* keys from config.yaml."""

    def test_missing_file_is_off(self, tmp_path):
        assert _read_dm_state(tmp_path / "nope.yaml") == ("off", [], False, None)

    def test_garbage_file_is_off(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("not: [valid: yaml: here")
        assert _read_dm_state(cfg) == ("off", [], False, None)

    def test_reads_selected_block(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.dump(
                {
                    "mm_source": {
                        "dm_scope": "selected",
                        "dm_allowlist": [" @ann ", "", "@bob"],
                        "dm_consent_acknowledged": True,
                        "dm_consent_acknowledged_at": "2026-06-18T00:00:00+00:00",
                    }
                }
            )
        )
        scope, allowlist, ack, ack_at = _read_dm_state(cfg)
        assert scope == "selected"
        assert allowlist == ["@ann", "@bob"]  # normalized
        assert ack is True
        assert ack_at == "2026-06-18T00:00:00+00:00"

    def test_unknown_scope_falls_back_off(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"mm_source": {"dm_scope": "everything"}}))
        assert _read_dm_state(cfg)[0] == "off"


class TestDmConsentStatus:
    """Header consent string renders defensively (never crashes)."""

    NOW = datetime(2026, 6, 18, tzinfo=timezone.utc)

    def test_off_shows_dash(self):
        assert _dm_consent_status("off", False, None) == "consent —"

    def test_own_posts_shows_dash(self):
        assert _dm_consent_status("own_posts_only", False, None) == "consent —"

    def test_selected_fresh_shows_date(self):
        fresh = (self.NOW - timedelta(days=10)).isoformat()
        out = _dm_consent_status("selected", True, fresh)
        assert "consent ✓" in out
        assert "2026-06" in out
        assert "stale" not in out

    def test_selected_none_timestamp_unknown(self):
        out = _dm_consent_status("selected", True, None)
        assert "(unknown)" in out

    def test_selected_garbage_timestamp_does_not_crash(self):
        out = _dm_consent_status("selected", True, 12345)  # non-str
        assert "consent ✓" in out
        assert "(unknown)" in out

    def test_selected_stale_flagged(self):
        stale = (self.NOW - timedelta(days=400)).isoformat()
        out = _dm_consent_status("selected", True, stale)
        assert "stale" in out

    def test_selected_not_acknowledged(self):
        out = _dm_consent_status("selected", False, None)
        assert "required" in out


class TestMmDmMenuScope:
    """The settings screen wiring over the tested helpers (thin glue)."""

    def _config(self):
        from digest_core.ui import THEME

        return Console(record=True, width=100, force_terminal=False, theme=THEME)

    def test_change_to_own_posts_persists_immediately(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"mm_source": {"dm_scope": "off"}, "llm": {"x": 1}}))
        monkeypatch.setattr(menu_mod, "CONFIG_USER_PATH", cfg)
        # First menu action: change scope; ladder picks own_posts_only; then Back.
        actions = iter(["scope", "own_posts_only", "back"])
        monkeypatch.setattr(menu_mod, "choose", lambda *a, **k: next(actions))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        _mm_dm_menu(self._config())
        doc = yaml.safe_load(cfg.read_text())
        assert doc["mm_source"]["dm_scope"] == "own_posts_only"
        assert doc["llm"] == {"x": 1}  # other section preserved
        MattermostSourceConfig(**doc["mm_source"])  # loadable

    def test_change_to_selected_consent_declined_keeps_off(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"mm_source": {"dm_scope": "off"}}))
        monkeypatch.setattr(menu_mod, "CONFIG_USER_PATH", cfg)
        actions = iter(["scope", "selected", "back"])
        monkeypatch.setattr(menu_mod, "choose", lambda *a, **k: next(actions))
        # Consent panel declined.
        monkeypatch.setattr(menu_mod, "_dm_consent_panel", lambda console: False)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        _mm_dm_menu(self._config())
        doc = yaml.safe_load(cfg.read_text())
        # Still off — never wrote an unloadable selected-without-consent.
        assert doc["mm_source"]["dm_scope"] == "off"

    def test_change_to_selected_consent_accepted_writes_loadable(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"mm_source": {"dm_scope": "off"}}))
        monkeypatch.setattr(menu_mod, "CONFIG_USER_PATH", cfg)
        actions = iter(["scope", "selected", "back"])
        monkeypatch.setattr(menu_mod, "choose", lambda *a, **k: next(actions))
        monkeypatch.setattr(menu_mod, "_dm_consent_panel", lambda console: True)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        _mm_dm_menu(self._config())
        doc = yaml.safe_load(cfg.read_text())
        block = doc["mm_source"]
        assert block["dm_scope"] == "selected"
        assert block["dm_consent_acknowledged"] is True
        assert block["dm_consent_acknowledged_at"] is not None
        MattermostSourceConfig(**block)  # loadable
