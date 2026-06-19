"""Detect installed AI coding CLIs and whether our MCP server is registered.

Three targets, each with its own global config path + top-level key + entry shape:
* Claude Code  — ~/.claude.json                       key ``mcpServers``  (command: str)
* opencode     — ~/.config/opencode/opencode.json     key ``mcp``         (command: [str,...])
* qwen-code    — ~/.qwen/settings.json                key ``mcpServers``  (command: str)

Detection is OS-portable (no SDK, no network) so it is testable on any platform; the
*install* command is macOS-gated in commands.py.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Tuple

from digest_core.mcp.jsonfile import read_json_or_empty

#: Our fixed key inside each CLI's server map (idempotency is keyed on this).
SERVER_NAME = "actionpulse"


class CLIFormat(str, Enum):
    CLAUDE_CODE = "claude_code"
    OPENCODE = "opencode"
    QWEN_CODE = "qwen_code"


@dataclass(frozen=True)
class CLISpec:
    fmt: CLIFormat
    display: str
    binaries: Tuple[str, ...]
    global_config: Path
    top_level_key: str
    server_name: str = SERVER_NAME


@dataclass(frozen=True)
class DetectedCLI:
    spec: CLISpec
    installed: bool  # binary on PATH OR a config file already present
    on_path: bool
    config_exists: bool
    registered: bool


def specs() -> List[CLISpec]:
    """The three target specs, rooted at the current ``$HOME`` (re-read each call)."""
    home = Path.home()
    return [
        CLISpec(
            CLIFormat.CLAUDE_CODE,
            "Claude Code",
            ("claude",),
            home / ".claude.json",
            "mcpServers",
        ),
        CLISpec(
            CLIFormat.OPENCODE,
            "opencode",
            ("opencode",),
            home / ".config" / "opencode" / "opencode.json",
            "mcp",
        ),
        CLISpec(
            CLIFormat.QWEN_CODE,
            "qwen-code",
            ("qwen",),
            home / ".qwen" / "settings.json",
            "mcpServers",
        ),
    ]


def is_registered(spec: CLISpec) -> bool:
    """True iff our server name is present under the spec's top-level key. Defensive:
    a missing/malformed config reads as not-registered (never raises)."""
    doc, malformed = read_json_or_empty(spec.global_config)
    if malformed:
        return False
    section = doc.get(spec.top_level_key)
    return isinstance(section, dict) and spec.server_name in section


def detect_one(spec: CLISpec) -> DetectedCLI:
    on_path = any(shutil.which(b) for b in spec.binaries)
    config_exists = spec.global_config.exists()
    return DetectedCLI(
        spec=spec,
        installed=on_path or config_exists,
        on_path=on_path,
        config_exists=config_exists,
        registered=is_registered(spec),
    )


def detect_all() -> List[DetectedCLI]:
    return [detect_one(s) for s in specs()]
