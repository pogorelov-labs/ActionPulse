"""MCP installer internals: atomic JSON I/O, detection, render, install/uninstall."""

from __future__ import annotations

import json

from digest_core.mcp import detect, entry, installers, jsonfile
from digest_core.mcp.detect import CLIFormat, CLISpec
from digest_core.mcp.entry import ServerEntry
from digest_core.mcp.installers import InstallStatus


def _spec(tmp_path, fmt, key, name="config.json"):
    return CLISpec(fmt, "Test", ("no-such-binary-xyz",), tmp_path / name, key)


# --- jsonfile -------------------------------------------------------------


def test_read_json_or_empty(tmp_path):
    p = tmp_path / "a.json"
    assert jsonfile.read_json_or_empty(p) == ({}, False)  # missing
    p.write_text("   ")
    assert jsonfile.read_json_or_empty(p) == ({}, False)  # blank
    p.write_text("{not json")
    assert jsonfile.read_json_or_empty(p) == ({}, True)  # malformed
    p.write_text("[1, 2]")
    assert jsonfile.read_json_or_empty(p) == ({}, True)  # non-object
    p.write_text('{"a": 1}')
    assert jsonfile.read_json_or_empty(p) == ({"a": 1}, False)


def test_backup_and_atomic_write(tmp_path):
    p = tmp_path / "a.json"
    assert jsonfile.backup(p) is None  # absent → no-op
    p.write_text('{"x": 1}')
    bak = jsonfile.backup(p)
    assert bak is not None and bak.read_text() == '{"x": 1}' and bak.name.endswith(".bak")
    jsonfile.atomic_write_json(p, {"y": 2})
    assert json.loads(p.read_text()) == {"y": 2}
    assert not (tmp_path / "a.json.tmp").exists()  # temp cleaned up by rename


# --- detect ---------------------------------------------------------------


def test_specs_rooted_at_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = {sp.fmt: sp for sp in detect.specs()}
    assert s[CLIFormat.CLAUDE_CODE].global_config == tmp_path / ".claude.json"
    assert s[CLIFormat.OPENCODE].global_config == tmp_path / ".config/opencode/opencode.json"
    assert s[CLIFormat.QWEN_CODE].global_config == tmp_path / ".qwen/settings.json"
    assert s[CLIFormat.OPENCODE].top_level_key == "mcp"
    assert s[CLIFormat.CLAUDE_CODE].top_level_key == "mcpServers"


def test_detect_one_and_is_registered(tmp_path):
    spec = _spec(tmp_path, CLIFormat.CLAUDE_CODE, "mcpServers")
    assert detect.detect_one(spec).installed is False  # no binary, no config
    spec.global_config.write_text(json.dumps({"mcpServers": {"actionpulse": {}}}))
    d = detect.detect_one(spec)
    assert d.installed and d.config_exists and d.registered
    spec.global_config.write_text(json.dumps({"mcpServers": {"other": {}}}))
    assert detect.is_registered(spec) is False


# --- entry ----------------------------------------------------------------


def test_build_server_entry_prefers_uv(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/uv" if b == "uv" else None)
    e = entry.build_server_entry()
    assert e.command == "uv"
    assert e.args[:2] == ["run", "--project"] and e.args[-1] == "actionpulse-mcp"
    assert e.env == {}  # secret never in the entry


# --- render ---------------------------------------------------------------


def test_render_three_formats_no_env(tmp_path):
    e = ServerEntry("uv", ["run", "--project", "/p", "actionpulse-mcp"], {})
    claude = installers.render_entry(_spec(tmp_path, CLIFormat.CLAUDE_CODE, "mcpServers"), e)
    oc = installers.render_entry(_spec(tmp_path, CLIFormat.OPENCODE, "mcp"), e)
    qwen = installers.render_entry(_spec(tmp_path, CLIFormat.QWEN_CODE, "mcpServers"), e)
    assert claude == {
        "type": "stdio",
        "command": "uv",
        "args": ["run", "--project", "/p", "actionpulse-mcp"],
    }
    assert oc == {
        "type": "local",
        "command": ["uv", "run", "--project", "/p", "actionpulse-mcp"],
        "enabled": True,
    }
    assert qwen == {
        "command": "uv",
        "args": ["run", "--project", "/p", "actionpulse-mcp"],
        "timeout": 30000,
        "trust": False,
    }
    assert "env" not in claude and "environment" not in oc and "env" not in qwen


# --- install / uninstall --------------------------------------------------


def test_install_idempotent_and_preserves_siblings(tmp_path):
    spec = _spec(tmp_path, CLIFormat.CLAUDE_CODE, "mcpServers")
    spec.global_config.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "otherTopKey": 1})
    )
    r1 = installers.install(spec, ServerEntry("uv", ["run"]))
    assert r1.status is InstallStatus.INSTALLED and r1.backup is not None
    doc = json.loads(spec.global_config.read_text())
    assert doc["mcpServers"]["actionpulse"]["command"] == "uv"
    assert doc["mcpServers"]["other"] == {"command": "x"}  # sibling server preserved
    assert doc["otherTopKey"] == 1  # sibling top-level key preserved

    r2 = installers.install(spec, ServerEntry("uv", ["run"]))
    assert r2.status is InstallStatus.ALREADY_CURRENT and r2.backup is None  # no write

    r3 = installers.install(spec, ServerEntry("uv", ["run", "--project", "/q"]))
    assert r3.status is InstallStatus.UPDATED
    doc = json.loads(spec.global_config.read_text())
    assert doc["mcpServers"]["actionpulse"]["args"] == ["run", "--project", "/q"]
    assert list(doc["mcpServers"]) == ["other", "actionpulse"]  # updated in place, no dupe


def test_install_skips_malformed_without_touching(tmp_path):
    spec = _spec(tmp_path, CLIFormat.CLAUDE_CODE, "mcpServers")
    spec.global_config.write_text("{bad json")
    r = installers.install(spec, ServerEntry("uv"))
    assert r.status is InstallStatus.SKIPPED_MALFORMED
    assert spec.global_config.read_text() == "{bad json"  # left exactly as-is


def test_install_creates_missing_config(tmp_path):
    spec = _spec(tmp_path, CLIFormat.QWEN_CODE, "mcpServers", name="nested/settings.json")
    r = installers.install(spec, ServerEntry("uv", ["run"]))
    assert r.status is InstallStatus.INSTALLED and spec.global_config.exists()
    assert json.loads(spec.global_config.read_text())["mcpServers"]["actionpulse"]["trust"] is False


def test_uninstall_then_not_present(tmp_path):
    spec = _spec(tmp_path, CLIFormat.CLAUDE_CODE, "mcpServers")
    installers.install(spec, ServerEntry("uv"))
    assert installers.uninstall(spec).status is InstallStatus.REMOVED
    assert "actionpulse" not in json.loads(spec.global_config.read_text()).get("mcpServers", {})
    assert installers.uninstall(spec).status is InstallStatus.NOT_PRESENT


def test_dry_run_writes_nothing(tmp_path):
    spec = _spec(tmp_path, CLIFormat.CLAUDE_CODE, "mcpServers")
    r = installers.install(spec, ServerEntry("uv"), dry_run=True)
    assert r.status is InstallStatus.INSTALLED and r.block is not None
    assert not spec.global_config.exists()
