"""``actionpulse mcp`` — list / install / uninstall the MCP server in AI coding CLIs.

The actual JSON writing lives in ``installers.py`` (fully tested, platform-independent);
this is the user-facing flow: detect → show → consent → write → report + undo. Install
is macOS-gated (``--dry-run`` works anywhere); ``list`` works everywhere.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

import typer

from digest_core.mcp.detect import CLIFormat, DetectedCLI, detect_all
from digest_core.mcp.entry import ServerEntry, build_server_entry
from digest_core.mcp.installers import InstallResult, InstallStatus, install, uninstall
from digest_core.ui.glyphs import FAIL, OK

mcp_app = typer.Typer(
    help="Register the ActionPulse MCP server into your AI coding CLIs "
    "(Claude Code, opencode, qwen-code)."
)

_BY_NAME = {
    "claude": CLIFormat.CLAUDE_CODE,
    "opencode": CLIFormat.OPENCODE,
    "qwen": CLIFormat.QWEN_CODE,
}

_VERB = {
    InstallStatus.INSTALLED: ("installed", "would install"),
    InstallStatus.UPDATED: ("updated", "would update"),
    InstallStatus.ALREADY_CURRENT: ("already current", "already current"),
    InstallStatus.REMOVED: ("removed", "would remove"),
    InstallStatus.NOT_PRESENT: ("not present", "not present"),
    InstallStatus.SKIPPED_MALFORMED: ("SKIPPED (malformed JSON)", "SKIPPED (malformed JSON)"),
    InstallStatus.SKIPPED_BAD_SHAPE: ("SKIPPED (bad top-level shape)", "SKIPPED (bad shape)"),
}
_SKIPPED = {InstallStatus.SKIPPED_MALFORMED, InstallStatus.SKIPPED_BAD_SHAPE}


def _status_word(d: DetectedCLI) -> str:
    if not d.installed:
        return "not installed"
    return "registered" if d.registered else "installed, not registered"


def _launch_line(entry: ServerEntry) -> str:
    return f"{entry.command} {' '.join(entry.args)}".rstrip()


def _report(res: InstallResult, *, dry_run: bool) -> None:
    verb = _VERB[res.status][1 if dry_run else 0]
    mark = FAIL if res.status in _SKIPPED else OK
    line = f"  {mark} {res.cli:<12} {verb}"
    if res.backup:
        line += f"  (backup: {res.backup.name})"
    typer.echo(line)
    if dry_run and res.block is not None:
        typer.echo(f"      {json.dumps(res.block)}")


def _key_reminder() -> None:
    from digest_core.ui.menu import load_env_file

    load_env_file()
    if not os.getenv("DIGEST_STORE_KEY"):
        typer.echo(
            f"{FAIL} DIGEST_STORE_KEY isn't set — run `actionpulse store init` and enable the "
            "store, or the MCP server starts but exposes nothing."
        )


def _select(cli: Optional[str], all_: bool) -> Optional[List[DetectedCLI]]:
    """Resolve the target CLIs. ``None`` means 'no explicit selection' (caller decides)."""
    detected = {d.spec.fmt: d for d in detect_all()}
    if cli:
        fmt = _BY_NAME.get(cli.lower())
        if fmt is None:
            raise typer.BadParameter(f"unknown cli {cli!r} (claude | opencode | qwen)")
        return [detected[fmt]]
    if all_:
        return [d for d in detected.values() if d.installed]
    return None


@mcp_app.command("list")
def mcp_list() -> None:
    """Show which AI coding CLIs are installed and whether the MCP server is registered."""
    typer.echo(f"MCP launch command: {_launch_line(build_server_entry())}")
    for d in detect_all():
        mark = OK if d.installed else FAIL
        typer.echo(f"  {mark} {d.spec.display:<12} {_status_word(d):<26} [{d.spec.global_config}]")


@mcp_app.command("install")
def mcp_install(
    cli: Optional[str] = typer.Option(None, "--cli", help="claude | opencode | qwen"),
    all_: bool = typer.Option(False, "--all", help="all detected + installed CLIs"),
    yes: bool = typer.Option(False, "--yes", help="skip the confirmation prompt"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show the JSON, write nothing"),
) -> None:
    """Register the MCP server into the detected CLIs (with consent). macOS only."""
    if sys.platform != "darwin" and not dry_run:
        typer.echo(f"{FAIL} `mcp install` is macOS-only for now. Try `mcp list` or `--dry-run`.")
        raise typer.Exit(0)

    targets = _select(cli, all_)
    if targets is None:  # no --cli/--all → default to everything installed
        targets = [d for d in detect_all() if d.installed]
    targets = [d for d in targets if d.installed]
    if not targets:
        typer.echo("No supported AI coding CLIs found (looked for: claude, opencode, qwen).")
        raise typer.Exit(0)

    entry = build_server_entry()
    if not (yes or dry_run):
        typer.echo("This edits third-party config files:")
        for d in targets:
            typer.echo(f"  - {d.spec.display}: {d.spec.global_config}")
        typer.echo(f"Launch command written: {_launch_line(entry)}")
        typer.echo("A timestamped .bak is made first; `actionpulse mcp uninstall` reverses it.")
        typer.echo("The server exposes your local message store to that AI CLI (full content).")
        if not typer.confirm("Register the ActionPulse MCP server?", default=False):
            typer.echo("No files changed.")
            raise typer.Exit(0)

    for d in targets:
        _report(install(d.spec, entry, dry_run=dry_run), dry_run=dry_run)
    if not dry_run:
        typer.echo("Undo any time: actionpulse mcp uninstall --all")
        _key_reminder()


@mcp_app.command("uninstall")
def mcp_uninstall(
    cli: Optional[str] = typer.Option(None, "--cli", help="claude | opencode | qwen"),
    all_: bool = typer.Option(False, "--all", help="all CLIs"),
    yes: bool = typer.Option(False, "--yes", help="skip the confirmation prompt"),
) -> None:
    """Remove the ActionPulse MCP server entry from the CLIs (leaves backups)."""
    targets = _select(cli, all_)
    if targets is None:
        targets = detect_all()
    if not (yes) and not typer.confirm(
        "Remove the ActionPulse MCP server from these CLIs?", default=False
    ):
        typer.echo("No files changed.")
        raise typer.Exit(0)
    for d in targets:
        _report(uninstall(d.spec), dry_run=False)
