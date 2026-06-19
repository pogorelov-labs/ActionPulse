"""The MCP server launch command written into each CLI config — secret-free.

The registered command carries NO ``DIGEST_STORE_KEY`` (the server self-loads it from
``~/.config/actionpulse/env`` at startup), so ``env`` is always empty. Preference:
``uv run --project <digest-core>`` (matches the existing launcher, no PATH/PyPI
assumptions) → a directly-installed ``actionpulse-mcp`` → ``python -m digest_core.mcp``.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class ServerEntry:
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


def build_server_entry() -> ServerEntry:
    """The most robust launch command for this checkout. ``env`` stays empty — the
    server loads the store key from the 0600 env file (never from client config)."""
    from digest_core.config import PROJECT_ROOT

    uv = shutil.which("uv")
    if uv:
        return ServerEntry("uv", ["run", "--project", str(PROJECT_ROOT), "actionpulse-mcp"])
    direct = shutil.which("actionpulse-mcp")
    if direct:
        return ServerEntry(direct, [])
    return ServerEntry(sys.executable, ["-m", "digest_core.mcp"])
