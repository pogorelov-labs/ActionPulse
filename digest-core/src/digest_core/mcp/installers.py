"""Render + write our MCP server entry into a CLI's config — idempotent and reversible.

One logical ``ServerEntry`` → three on-disk shapes (Claude / opencode / qwen). Every
write backs up first, is atomic, touches only our ``actionpulse`` key, and never clobbers
a sibling server or an unparseable file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from digest_core.mcp.detect import CLIFormat, CLISpec
from digest_core.mcp.entry import ServerEntry
from digest_core.mcp.jsonfile import atomic_write_json, backup, read_json_or_empty


class InstallStatus(str, Enum):
    INSTALLED = "installed"  # added (was not present)
    UPDATED = "updated"  # replaced our existing (different) block
    ALREADY_CURRENT = "already_current"  # identical block already there → no write
    REMOVED = "removed"
    NOT_PRESENT = "not_present"  # uninstall found nothing
    SKIPPED_MALFORMED = "skipped_malformed"  # config unparseable → refuse to touch
    SKIPPED_BAD_SHAPE = "skipped_bad_shape"  # our top-level key isn't an object


@dataclass
class InstallResult:
    cli: str
    status: InstallStatus
    config_path: Path
    backup: Optional[Path] = None
    block: Optional[Dict[str, Any]] = None  # the rendered entry (for dry-run preview)


def render_entry(spec: CLISpec, entry: ServerEntry) -> Dict[str, Any]:
    """The exact JSON block for this CLI's format. ``env`` is omitted when empty."""
    if spec.fmt is CLIFormat.OPENCODE:
        block: Dict[str, Any] = {
            "type": "local",
            "command": [entry.command, *entry.args],
            "enabled": True,
        }
        if entry.env:
            block["environment"] = dict(entry.env)
        return block
    if spec.fmt is CLIFormat.QWEN_CODE:
        block = {
            "command": entry.command,
            "args": list(entry.args),
            "timeout": 30000,
            "trust": False,
        }
        if entry.env:
            block["env"] = dict(entry.env)
        return block
    # Claude Code (default)
    block = {"type": "stdio", "command": entry.command, "args": list(entry.args)}
    if entry.env:
        block["env"] = dict(entry.env)
    return block


def install(spec: CLISpec, entry: ServerEntry, *, dry_run: bool = False) -> InstallResult:
    """Add or update our server block under the spec's top-level key. Idempotent."""
    doc, malformed = read_json_or_empty(spec.global_config)
    if malformed:
        return InstallResult(spec.display, InstallStatus.SKIPPED_MALFORMED, spec.global_config)
    section = doc.get(spec.top_level_key)
    if section is None:
        section = {}
    if not isinstance(section, dict):
        return InstallResult(spec.display, InstallStatus.SKIPPED_BAD_SHAPE, spec.global_config)

    new_block = render_entry(spec, entry)
    prev = section.get(spec.server_name)
    if prev == new_block:
        return InstallResult(
            spec.display, InstallStatus.ALREADY_CURRENT, spec.global_config, block=new_block
        )
    status = InstallStatus.UPDATED if prev is not None else InstallStatus.INSTALLED
    if dry_run:
        return InstallResult(spec.display, status, spec.global_config, block=new_block)

    bak = backup(spec.global_config)
    section[spec.server_name] = new_block
    doc[spec.top_level_key] = section
    atomic_write_json(spec.global_config, doc)
    return InstallResult(spec.display, status, spec.global_config, backup=bak, block=new_block)


def uninstall(spec: CLISpec, *, dry_run: bool = False) -> InstallResult:
    """Remove our server block if present (leaving everything else untouched)."""
    doc, malformed = read_json_or_empty(spec.global_config)
    if malformed:
        return InstallResult(spec.display, InstallStatus.SKIPPED_MALFORMED, spec.global_config)
    section = doc.get(spec.top_level_key)
    if not isinstance(section, dict) or spec.server_name not in section:
        return InstallResult(spec.display, InstallStatus.NOT_PRESENT, spec.global_config)
    if dry_run:
        return InstallResult(spec.display, InstallStatus.REMOVED, spec.global_config)
    bak = backup(spec.global_config)
    del section[spec.server_name]
    atomic_write_json(spec.global_config, doc)
    return InstallResult(spec.display, InstallStatus.REMOVED, spec.global_config, backup=bak)
