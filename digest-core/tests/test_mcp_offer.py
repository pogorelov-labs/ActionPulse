"""commands.offer_install — the themed wizard/menu 'register the MCP server' offer."""

from __future__ import annotations

from digest_core.mcp import commands
from digest_core.mcp.detect import CLIFormat, CLISpec, DetectedCLI
from digest_core.mcp.installers import InstallResult, InstallStatus
from digest_core.ui import get_console


def _capture(fn):
    console = get_console()
    with console.capture() as cap:
        result = fn(console)
    return result, cap.get()


def _spec(tmp_path):
    return CLISpec(
        CLIFormat.CLAUDE_CODE, "Claude Code", ("claude",), tmp_path / ".claude.json", "mcpServers"
    )


def test_offer_install_installs_into_detected(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    spec = _spec(tmp_path)
    monkeypatch.setattr(
        commands,
        "detect_all",
        lambda: [
            DetectedCLI(spec, installed=True, on_path=True, config_exists=False, registered=False)
        ],
    )
    calls = []
    monkeypatch.setattr(
        commands,
        "install",
        lambda s, e: calls.append(s)
        or InstallResult("Claude Code", InstallStatus.INSTALLED, spec.global_config),
    )
    result, out = _capture(lambda c: commands.offer_install(c, assume_yes=True))
    assert result is True and calls  # wrote into the detected CLI
    assert "Claude Code" in out and "Undo" in out


def test_offer_install_non_darwin_is_noop(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    result, out = _capture(lambda c: commands.offer_install(c, assume_yes=True))
    assert result is False and "macOS-only" in out


def test_offer_install_no_clis_found(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(commands, "detect_all", lambda: [])
    result, out = _capture(lambda c: commands.offer_install(c, assume_yes=True))
    assert result is False and "No supported" in out
