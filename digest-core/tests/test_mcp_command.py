"""``actionpulse mcp`` CLI surface (list / install / uninstall) via CliRunner."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from digest_core.cli import app

runner = CliRunner()


def test_mcp_list(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    res = runner.invoke(app, ["mcp", "list"])
    assert res.exit_code == 0
    assert "MCP launch command" in res.output
    assert "Claude Code" in res.output and "opencode" in res.output and "qwen-code" in res.output


def test_mcp_install_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text("{}")  # claude "installed" via config
    res = runner.invoke(app, ["mcp", "install", "--all", "--dry-run"])
    assert res.exit_code == 0 and "would install" in res.output
    assert (tmp_path / ".claude.json").read_text() == "{}"  # untouched


def test_mcp_install_writes_on_darwin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    qwen = tmp_path / ".qwen" / "settings.json"
    qwen.parent.mkdir()
    qwen.write_text("{}")
    res = runner.invoke(app, ["mcp", "install", "--cli", "qwen", "--yes"])
    assert res.exit_code == 0
    assert "actionpulse" in json.loads(qwen.read_text())["mcpServers"]


def test_mcp_install_consent_decline(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    (tmp_path / ".claude.json").write_text("{}")
    res = runner.invoke(app, ["mcp", "install", "--all"], input="n\n")
    assert "No files changed" in res.output
    assert (tmp_path / ".claude.json").read_text() == "{}"


def test_mcp_install_macos_gate_off_darwin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    (tmp_path / ".claude.json").write_text("{}")
    res = runner.invoke(app, ["mcp", "install", "--all", "--yes"])
    assert res.exit_code == 0 and "macOS-only" in res.output
    assert (tmp_path / ".claude.json").read_text() == "{}"  # nothing written


def test_mcp_uninstall(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps({"mcpServers": {"actionpulse": {"command": "uv"}, "other": {}}})
    )
    res = runner.invoke(app, ["mcp", "uninstall", "--all", "--yes"])
    assert res.exit_code == 0
    doc = json.loads((tmp_path / ".claude.json").read_text())
    assert "actionpulse" not in doc["mcpServers"] and "other" in doc["mcpServers"]
