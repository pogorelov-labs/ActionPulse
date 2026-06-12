"""Maintenance (U6): usage, cleanup, logging toggle — pure helpers + wiring."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from rich.console import Console
from typer.testing import CliRunner

from digest_core import maintenance
from digest_core.cli import app
from digest_core.ui import THEME
from digest_core.ui import menu as menu_mod


def _console() -> Console:
    return Console(record=True, width=110, force_terminal=False, theme=THEME)


def _seed_data_home(monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    out = tmp_path / "var" / "out"
    logs = tmp_path / "var" / "logs"
    state = tmp_path / "var" / "state"
    for directory in (out, logs, state):
        directory.mkdir(parents=True)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    new = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for date in (old, new):
        (out / f"digest-{date}.json").write_text("{}")
        (out / f"digest-{date}.md").write_text("#")
    (out / "trace-abc.meta.json").write_text("{}")
    (logs / "run-1.log").write_text("x" * 100)
    (state / "ews.syncstate").write_text("2026-06-11T00:00:00")
    return tmp_path


class TestUsageAndClean:
    def test_collect_usage_counts_files(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", ())
        usage = {entry.key: entry for entry in maintenance.collect_usage()}
        assert usage["digests"].files == 5
        assert usage["logs"].files == 1 and usage["logs"].size_bytes == 100
        assert usage["state"].files == 1
        assert "never auto-cleaned" in usage["state"].label

    def test_clean_logs_includes_legacy_dirs(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        legacy = tmp_path / "legacy-logs"
        legacy.mkdir()
        (legacy / "run-old.log").write_text("y" * 50)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", (legacy,))
        removed, freed = maintenance.clean_logs()
        assert removed == 2 and freed == 150
        assert not list((tmp_path / "var" / "logs").iterdir())
        assert not list(legacy.iterdir())

    def test_clean_digests_keeps_recent_by_name_date(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        removed, _ = maintenance.clean_digests(maintenance.DEFAULT_KEEP_DAYS)
        out = tmp_path / "var" / "out"
        names = sorted(p.name for p in out.iterdir())
        assert removed == 2  # the 30-day-old json+md pair
        assert all("digest-" not in n or "30" not in n for n in names)
        # Recent pair + the trace meta (recent mtime) survive.
        assert len(names) == 3

    def test_clean_digests_all(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        removed, _ = maintenance.clean_digests(None)
        assert removed == 5
        assert not list((tmp_path / "var" / "out").iterdir())

    def test_state_never_touched(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        maintenance.clean_logs()
        maintenance.clean_digests(None)
        assert (tmp_path / "var" / "state" / "ews.syncstate").exists()

    def test_format_bytes(self):
        assert maintenance.format_bytes(0) == "0 B"
        assert maintenance.format_bytes(2048) == "2.0 KB"
        assert maintenance.format_bytes(5 * 1024 * 1024) == "5.0 MB"


class TestLoggingToggle:
    def test_set_and_read_round_trip(self, monkeypatch, tmp_path):
        config_path = tmp_path / "configs" / "config.yaml"
        monkeypatch.setattr(maintenance, "CONFIG_USER", config_path)
        assert maintenance.set_file_logging(False) is False
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert data["observability"]["log_to_file"] is False
        assert maintenance.set_file_logging(True) is True

    def test_toggle_preserves_other_config_keys(self, monkeypatch, tmp_path):
        config_path = tmp_path / "configs" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("report:\n  language: ru\n", encoding="utf-8")
        monkeypatch.setattr(maintenance, "CONFIG_USER", config_path)
        maintenance.set_file_logging(False)
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert data["report"]["language"] == "ru"
        assert data["observability"]["log_to_file"] is False

    def test_setup_logging_disabled_writes_no_file(self, monkeypatch, tmp_path):
        from digest_core.observability import logs as logs_mod

        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        monkeypatch.setattr(logs_mod, "_CONFIGURED_LOG_FILE", None)
        root = logging.getLogger()
        saved = root.handlers[:]
        root.handlers = []
        try:
            result = logs_mod.setup_logging(console=False, enabled=False)
            assert result is None
            assert not (tmp_path / "var" / "logs").exists()
        finally:
            root.handlers = saved

    def test_explicit_log_file_wins_over_disabled(self, monkeypatch, tmp_path):
        from digest_core.observability import logs as logs_mod

        monkeypatch.setattr(logs_mod, "_CONFIGURED_LOG_FILE", None)
        root = logging.getLogger()
        saved = root.handlers[:]
        root.handlers = []
        try:
            target = tmp_path / "explicit.log"
            result = logs_mod.setup_logging(log_file=str(target), console=False, enabled=False)
            assert result == target and target.exists()
        finally:
            root.handlers = saved
            monkeypatch.setattr(logs_mod, "_CONFIGURED_LOG_FILE", None)


class TestCleanCommand:
    def test_no_flags_shows_usage_only(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", ())
        result = CliRunner().invoke(app, ["clean"])
        assert result.exit_code == 0
        assert "Nothing deleted" in result.output
        assert (tmp_path / "var" / "logs" / "run-1.log").exists()

    def test_all_flag_cleans_digests_and_logs(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", ())
        result = CliRunner().invoke(app, ["clean", "--all"])
        assert result.exit_code == 0
        assert "Removed 6 files" in result.output
        assert (tmp_path / "var" / "state" / "ews.syncstate").exists()


class TestMaintenanceMenu:
    def _scripted(self, monkeypatch, choices):
        seq = iter(choices)

        def fake_choose(label, options, default_index=0, console=None, cancel_value=None):
            assert len(options) <= 9
            try:
                return next(seq)
            except StopIteration:
                return "back"

        monkeypatch.setattr(menu_mod, "choose", fake_choose)

    def test_clean_logs_path(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", ())
        self._scripted(monkeypatch, ["logs", "back"])
        console = _console()
        menu_mod._maintenance(console)
        text = console.export_text()
        assert "Removed 1 files" in text
        assert not list((tmp_path / "var" / "logs").iterdir())

    def test_clean_all_requires_confirmation(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", ())
        self._scripted(monkeypatch, ["all", "no", "back"])  # declined
        menu_mod._maintenance(_console())
        assert (tmp_path / "var" / "out" / "trace-abc.meta.json").exists()

    def test_logging_toggle_writes_config(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", ())
        config_path = tmp_path / "configs" / "config.yaml"
        monkeypatch.setattr(maintenance, "CONFIG_USER", config_path)
        self._scripted(monkeypatch, ["logging", "back"])
        console = _console()
        menu_mod._maintenance(console)
        assert "File logging is now off" in console.export_text()
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert data["observability"]["log_to_file"] is False

    def test_usage_readout_and_honesty_line(self, monkeypatch, tmp_path):
        _seed_data_home(monkeypatch, tmp_path)
        monkeypatch.setattr(maintenance, "LEGACY_LOG_DIRS", ())
        self._scripted(monkeypatch, ["back"])
        console = _console()
        menu_mod._maintenance(console)
        text = console.export_text()
        assert "Digests" in text and "Logs" in text and "State" in text
        assert "no phone-home telemetry" in text
