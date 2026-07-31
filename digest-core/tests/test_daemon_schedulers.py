"""Scheduler backends: launchd / systemd / cron (ACTPULSE-99).

Store freshness used to be macOS-only, because only *scheduling* was launchd-bound — the
tick itself was always portable. These cover the three backends' selection, rendering and
shared invariants.

**Rendering is pure and tested on every runner**, deliberately: a macOS box renders a
systemd unit and a Linux box renders a plist. The issue asked for "no platform-gated skips
that silently never run in CI", and rendering is where the interesting logic lives — only
the `systemctl`/`crontab`/`launchctl` calls are genuinely platform-bound, and those are
mocked rather than skipped.
"""

from __future__ import annotations

import re

import pytest

from digest_core.daemon import crontab, launchd, scheduler, systemd

ALL_BACKENDS = (launchd, systemd, crontab)


# -- selection -----------------------------------------------------------------


class TestSelection:
    def test_every_backend_satisfies_the_shared_surface(self):
        """The registry treats these as interchangeable, so they must actually be."""
        required = (
            "NAME",
            "is_supported",
            "is_installed",
            "unit_path",
            "describe",
            "install",
            "uninstall",
            "start",
            "stop",
        )
        for backend in ALL_BACKENDS:
            missing = [attr for attr in required if not hasattr(backend, attr)]
            assert not missing, f"{backend.NAME} is missing {missing}"

    def test_platform_native_beats_cron(self, monkeypatch):
        """cron is the fallback, never the first choice — it cannot express every
        interval and has no run-at-boot."""
        monkeypatch.setattr(launchd, "is_supported", lambda: True)
        monkeypatch.setattr(systemd, "is_supported", lambda: True)
        monkeypatch.setattr(crontab, "is_supported", lambda: True)
        assert scheduler.select().NAME == "launchd"

        monkeypatch.setattr(launchd, "is_supported", lambda: False)
        assert scheduler.select().NAME == "systemd"

        monkeypatch.setattr(systemd, "is_supported", lambda: False)
        assert scheduler.select().NAME == "cron"

    def test_no_backend_available_is_none_not_a_crash(self, monkeypatch):
        """A host with no scheduler must get a clear message, not a traceback — the CLI
        tells them `daemon tick` still works."""
        for backend in ALL_BACKENDS:
            monkeypatch.setattr(backend, "is_supported", lambda: False)
        assert scheduler.select() is None

    def test_named_selection_rejects_typos(self):
        with pytest.raises(ValueError, match="unknown scheduler"):
            scheduler.select("systemdd")

    def test_installed_backend_finds_what_is_actually_there(self, monkeypatch):
        """Not what we would choose NOW: a host that gained systemd after a cron install
        must still be able to uninstall its cron entry."""
        monkeypatch.setattr(launchd, "is_installed", lambda: False)
        monkeypatch.setattr(systemd, "is_installed", lambda: False)
        monkeypatch.setattr(crontab, "is_installed", lambda: True)
        assert scheduler.installed_backend().NAME == "cron"

    def test_a_broken_backend_does_not_hide_the_others(self, monkeypatch):
        def boom():
            raise OSError("crontab is on fire")

        monkeypatch.setattr(launchd, "is_installed", boom)
        monkeypatch.setattr(systemd, "is_installed", lambda: True)
        assert scheduler.installed_backend().NAME == "systemd"


# -- shared invariants ---------------------------------------------------------


class TestSharedInvariants:
    """Properties every backend must hold — these are the reason the daemon is safe."""

    @pytest.mark.parametrize("backend", ALL_BACKENDS, ids=lambda b: b.NAME)
    def test_unit_carries_no_secret(self, backend, monkeypatch):
        """The tick self-loads DIGEST_STORE_KEY from the 0600 env file; a unit file is
        world-readable-ish config and must never carry one."""
        monkeypatch.setenv("DIGEST_STORE_KEY", "s3cr3t-key-value")
        monkeypatch.setenv("EWS_PASSWORD", "s3cr3t-password")
        monkeypatch.setenv("MM_PAT", "s3cr3t-token")
        text = _render(backend, 30)
        for secret in ("s3cr3t-key-value", "s3cr3t-password", "s3cr3t-token"):
            assert secret not in text
        # ...and no env-var NAME that would imply one is being passed through.
        assert "DIGEST_STORE_KEY" not in text

    @pytest.mark.parametrize("backend", ALL_BACKENDS, ids=lambda b: b.NAME)
    def test_command_is_absolute(self, backend):
        """launchd gives an agent almost no PATH and cron gives it /usr/bin:/bin, so a
        bare `uv` would resolve differently — or not at all — than in a login shell."""
        argv = scheduler.tick_command()
        assert argv[0].startswith("/"), argv
        assert argv[0] in _render(backend, 30)

    @pytest.mark.parametrize("backend", ALL_BACKENDS, ids=lambda b: b.NAME)
    def test_logs_go_where_daemon_logs_reads(self, backend):
        """`daemon logs` tails one pair of files; a backend logging elsewhere would make
        it silently useless on that platform."""
        out_log, err_log = scheduler.log_paths()
        text = _render(backend, 30)
        assert str(out_log) in text and str(err_log) in text

    @pytest.mark.parametrize("backend", ALL_BACKENDS, ids=lambda b: b.NAME)
    def test_renders_anywhere(self, backend):
        """Pure rendering on any host — this is what keeps the logic out of skip-gated
        tests that never run in CI."""
        assert _render(backend, 30).strip()


def _render(backend, minutes: int) -> str:
    if backend.NAME == "launchd":
        return backend.render_plist(minutes).decode()
    if backend.NAME == "systemd":
        return backend.render_service() + backend.render_timer(minutes)
    return backend.render(minutes)


# -- systemd -------------------------------------------------------------------


class TestSystemd:
    def test_timer_uses_a_true_interval(self):
        timer = systemd.render_timer(30)
        assert "OnUnitActiveSec=30min" in timer
        assert "OnBootSec=30min" in timer  # launchd's RunAtLoad equivalent
        assert "Persistent=true" in timer  # catch up one missed run after sleep
        assert f"Unit={systemd.STEM}.service" in timer

    def test_service_is_oneshot(self):
        """The timer schedules; the service runs exactly one tick and exits. A long-lived
        service would fight the flock single-writer."""
        assert "Type=oneshot" in systemd.render_service()

    def test_user_units_not_system_units(self):
        """No root, and the tick runs as the person whose store and 0600 env it reads."""
        assert "/systemd/user/" in str(systemd.timer_path())
        assert "/systemd/user/" in str(systemd.service_path())

    def test_paths_with_spaces_are_quoted(self):
        """A checkout under /Users/me/My Projects/ must not split into two ExecStart args.

        Injected via ``command=`` rather than by patching ``scheduler.tick_command``:
        this module does ``from ... import tick_command``, so the name is bound in
        *systemd's* namespace and patching the source module would be a no-op that
        silently passed against the un-quoted output.
        """
        argv = ["/opt/u v/uv", "run", "--project", "/a b/c"]
        exec_line = [
            line
            for line in systemd.render_service(command=argv).splitlines()
            if line.startswith("ExecStart=")
        ][0]
        assert '"/opt/u v/uv"' in exec_line
        assert '"/a b/c"' in exec_line

    def test_xdg_config_home_is_honoured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert str(tmp_path / "xdg") in str(systemd.timer_path())

    def test_not_supported_off_linux(self, monkeypatch):
        monkeypatch.setattr(systemd.sys, "platform", "darwin")
        assert systemd.is_supported() is False


# -- cron ----------------------------------------------------------------------


class TestCron:
    """cron's limits are the interesting part — and they must never be silent."""

    @pytest.mark.parametrize(
        "minutes,expected",
        [(1, "*/1 * * * *"), (5, "*/5 * * * *"), (30, "*/30 * * * *"), (60, "0 */1 * * *")],
    )
    def test_exact_intervals_have_no_note(self, minutes, expected):
        expr, note = crontab.schedule_for(minutes)
        assert expr == expected
        assert note is None, "an exact schedule must not warn"

    @pytest.mark.parametrize("minutes", [7, 45, 90, 300])
    def test_inexact_intervals_say_so(self, minutes):
        """No silent caps: if cron cannot do what was asked, the user hears about it."""
        _, note = crontab.schedule_for(minutes)
        assert note, f"{minutes} min is not exact in cron but produced no note"

    @pytest.mark.parametrize("minutes", [1, 7, 30, 59, 60, 90, 720, 1439, 1440, 2000, 10080])
    def test_every_expression_is_a_VALID_crontab_line(self, minutes):
        """The hour field is 0-23, so a naive `minutes // 60` emits `*/24` or `*/33` —
        not merely approximate but OUT OF RANGE, i.e. a broken entry. Caught by running
        the mapping over a wide span rather than the few values that look interesting.
        """
        expr, _ = crontab.schedule_for(minutes)
        minute, hour, dom, month, dow = expr.split()
        assert _field_in_range(minute, 0, 59), expr
        assert _field_in_range(hour, 0, 23), expr
        assert _field_in_range(dom, 1, 31), expr
        assert _field_in_range(month, 1, 12), expr
        assert _field_in_range(dow, 0, 7), expr

    def test_block_is_marked_so_uninstall_touches_nothing_else(self):
        block = crontab.render(30)
        assert block.startswith(crontab.BEGIN)
        assert block.rstrip().endswith(crontab.END)

    def test_strip_leaves_foreign_lines_byte_identical(self):
        """Someone else's crontab entries are not ours to rewrite."""
        foreign = "0 3 * * * /usr/bin/backup.sh\n@reboot /opt/thing --start"
        combined = foreign + "\n\n" + crontab.render(15)
        assert crontab._strip_block(combined) == foreign

    def test_strip_is_idempotent_and_safe_when_absent(self):
        foreign = "0 3 * * * /usr/bin/backup.sh"
        assert crontab._strip_block(foreign) == foreign

    def test_install_replaces_its_own_block_rather_than_appending(self, monkeypatch):
        """Re-installing must not leave two entries both ticking."""
        state = {"text": "0 3 * * * /usr/bin/backup.sh\n\n" + crontab.render(15)}
        monkeypatch.setattr(crontab, "_read_crontab", lambda: state["text"])
        monkeypatch.setattr(crontab, "_write_crontab", lambda t: state.update(text=t) or True)
        crontab.install(30)
        assert state["text"].count(crontab.BEGIN) == 1
        assert "*/30 * * * *" in state["text"]
        assert "/usr/bin/backup.sh" in state["text"], "foreign entry must survive"

    def test_uninstall_removes_only_our_block(self, monkeypatch):
        state = {"text": "0 3 * * * /usr/bin/backup.sh\n\n" + crontab.render(15)}
        monkeypatch.setattr(crontab, "_read_crontab", lambda: state["text"])
        monkeypatch.setattr(crontab, "_write_crontab", lambda t: state.update(text=t) or True)
        result = crontab.uninstall()
        assert result.action == "uninstalled"
        assert crontab.BEGIN not in state["text"]
        assert "/usr/bin/backup.sh" in state["text"]


def _field_in_range(field: str, low: int, high: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = field[2:]
        return step.isdigit() and low <= int(step) <= high
    return all(part.isdigit() and low <= int(part) <= high for part in re.split(r"[,-]", field))


class TestCliErrors:
    """A bad flag is a user error — one line, not a stack trace (the ACTPULSE-101 rule
    applied to the interactive path)."""

    def test_unknown_backend_is_one_line_not_a_traceback(self):
        from typer.testing import CliRunner

        from digest_core.cli import app

        result = CliRunner().invoke(app, ["daemon", "install", "--dry-run", "--backend", "nope"])
        assert result.exit_code == 1
        assert "unknown scheduler" in result.output
        assert "Traceback" not in result.output
        # The valid names must survive into the message the user actually sees.
        for name in ("launchd", "systemd", "cron"):
            assert name in result.output
