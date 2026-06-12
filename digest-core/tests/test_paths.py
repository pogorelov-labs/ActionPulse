"""One data home (U5): resolution order, lazy dirs, default rewiring."""

from __future__ import annotations

import json
from pathlib import Path

from digest_core import paths
from digest_core.config import EWSConfig


class TestDataHome:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path / "custom"))
        assert paths.data_home() == tmp_path / "custom"

    def test_checkout_detected(self, monkeypatch):
        # The test run itself happens inside a checkout (install.sh at the root).
        monkeypatch.delenv("ACTIONPULSE_HOME", raising=False)
        home = paths.data_home()
        assert (home / "install.sh").exists()
        assert home == paths.PROJECT_ROOT.parent

    def test_wheel_install_falls_back_to_xdg(self, monkeypatch, tmp_path):
        # No checkout markers around the package -> never write into site-packages.
        monkeypatch.delenv("ACTIONPULSE_HOME", raising=False)
        monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "site-packages" / "digest-core")
        assert paths.data_home() == Path.home() / ".local" / "share" / "actionpulse"

    def test_subdirs_created_lazily(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        probe = paths.out_dir(create=False)
        assert not probe.exists()  # create=False has no side effects
        created = paths.out_dir()
        assert created.exists() and created == tmp_path / "var" / "out"
        assert paths.logs_dir() == tmp_path / "var" / "logs"
        assert paths.state_dir() == tmp_path / "var" / "state"

    def test_describe_covers_every_label(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        described = paths.describe()
        assert set(described) == set(paths.LABELS)
        assert described["digests"] == str(tmp_path / "var" / "out")
        assert "/.config/actionpulse/env" in described["secrets_env"]


class TestSyncStateResolution:
    def test_default_resolves_into_data_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        config = EWSConfig()
        assert config.sync_state_path is None
        assert config.resolved_sync_state_path() == str(
            tmp_path / "var" / "state" / "ews.syncstate"
        )

    def test_explicit_value_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        config = EWSConfig(sync_state_path="/pinned/ews.syncstate")
        assert config.resolved_sync_state_path() == "/pinned/ews.syncstate"


class TestDefaultRewiring:
    def test_run_help_names_the_data_home(self):
        from typer.testing import CliRunner

        from digest_core.cli import app
        from tests.test_progress_plain import _strip_ansi

        result = CliRunner().invoke(app, ["run", "--help"])
        # Typer wraps help text mid-phrase inside box borders; strip the
        # borders and collapse whitespace before comparing.
        flat = " ".join(_strip_ansi(result.output).replace("│", " ").split())
        assert "<data home>/var/out" in flat

    def test_read_defaults_to_data_home(self, monkeypatch, tmp_path):
        from typer.testing import CliRunner

        from digest_core.cli import app

        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        out_dir = tmp_path / "var" / "out"
        out_dir.mkdir(parents=True)
        (out_dir / "digest-2026-06-12.md").write_text("# from the data home", encoding="utf-8")
        (out_dir / "digest-2026-06-12.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "prompt_version": "p",
                    "digest_date": "2026-06-12",
                    "trace_id": "t",
                    "sections": [],
                }
            ),
            encoding="utf-8",
        )
        result = CliRunner().invoke(app, ["read"])  # non-tty -> markdown path
        assert result.exit_code == 0
        assert "# from the data home" in result.output

    def test_paths_command_prints_the_map(self, monkeypatch, tmp_path):
        from typer.testing import CliRunner

        from digest_core.cli import app

        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        result = CliRunner().invoke(app, ["paths"])
        assert result.exit_code == 0
        assert "Data home" in result.output
        assert str(tmp_path / "var" / "out") in result.output
        assert "Secrets env" in result.output  # the documented exception is visible

    def test_log_dir_prefers_data_home(self, monkeypatch, tmp_path):
        from digest_core.observability import logs as logs_mod

        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        assert logs_mod._resolve_log_dir() == tmp_path / "var" / "logs"

    def test_diagnostics_roots_include_data_home(self, monkeypatch, tmp_path):
        from digest_core import diagnostics

        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        roots = diagnostics._iter_search_roots()
        assert roots[0] == tmp_path / "var" / "out"


class TestLastRunMigration:
    def test_legacy_location_read_when_new_missing(self, monkeypatch, tmp_path):
        from digest_core.ui import menu as menu_mod

        new_path = tmp_path / "state" / "last_run.json"
        legacy = tmp_path / "legacy" / "last_run.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"from_date": "2026-06-10", "window": "rolling_24h"}')
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", new_path)
        monkeypatch.setattr(menu_mod, "LEGACY_LAST_RUN_PATH", legacy)
        choice = menu_mod.load_last_run()
        assert choice is not None and choice.from_date == "2026-06-10"

    def test_new_location_wins_over_legacy(self, monkeypatch, tmp_path):
        from digest_core.ui import menu as menu_mod

        new_path = tmp_path / "state" / "last_run.json"
        new_path.parent.mkdir(parents=True)
        new_path.write_text('{"from_date": "2026-06-11", "window": "calendar_day"}')
        legacy = tmp_path / "legacy" / "last_run.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"from_date": "2026-06-01", "window": "calendar_day"}')
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", new_path)
        monkeypatch.setattr(menu_mod, "LEGACY_LAST_RUN_PATH", legacy)
        assert menu_mod.load_last_run().from_date == "2026-06-11"
