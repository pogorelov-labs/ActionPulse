"""launchd LaunchAgent: plist render (no secrets), install/update/uninstall + .bak.

launchctl is faked so the tests never touch the real per-user launchd domain.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys

import pytest

from digest_core.daemon import launchd


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # plist_path() and data_home() both derive from these — keep writes in tmp.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    # Never shell out to the real launchctl.
    monkeypatch.setattr(
        launchd, "_run_launchctl", lambda *a: subprocess.CompletedProcess(a, 0, "", "")
    )
    yield


def test_render_has_no_secrets_and_expected_shape(monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "sekret-key-value")
    raw = launchd.render_plist(30, command=["/abs/uv", "run", "actionpulse", "daemon", "tick"])
    assert b"sekret-key-value" not in raw and b"DIGEST_STORE_KEY" not in raw
    doc = plistlib.loads(raw)
    assert doc["Label"] == "ai.actionpulse.ingest"
    assert doc["StartInterval"] == 1800
    assert doc["RunAtLoad"] is True
    assert doc["ProgramArguments"] == ["/abs/uv", "run", "actionpulse", "daemon", "tick"]
    assert "DIGEST_STORE_KEY" not in doc.get("EnvironmentVariables", {})


def test_start_interval_has_a_floor():
    assert plistlib.loads(launchd.render_plist(0, command=["x"]))["StartInterval"] == 60


def test_tick_command_is_absolute_or_module():
    cmd = launchd.tick_command()
    assert cmd[-1] == "tick" and "daemon" in cmd
    assert cmd[0].startswith("/") or cmd[0] == sys.executable  # never a bare "uv"


def test_install_update_uninstall_cycle():
    cmd = ["/abs/uv", "run", "actionpulse", "daemon", "tick"]

    r1 = launchd.install(30, load=False, command=cmd)
    assert r1.action == "installed" and launchd.is_installed()

    r2 = launchd.install(30, load=False, command=cmd)  # identical → no rewrite, no backup
    assert r2.action == "already_current" and r2.backup is None

    r3 = launchd.install(45, load=False, command=cmd)  # changed interval → update + .bak
    assert r3.action == "updated"
    assert r3.backup is not None and r3.backup.exists()
    assert plistlib.loads(launchd.plist_path().read_bytes())["StartInterval"] == 2700

    r4 = launchd.uninstall()
    assert r4.action == "uninstalled" and not launchd.plist_path().exists()
    assert r4.backup is not None and r4.backup.exists()

    assert launchd.uninstall().action == "not_present"
