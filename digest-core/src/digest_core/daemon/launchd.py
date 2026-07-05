"""Render + (un)install the macOS launchd LaunchAgent that runs ``actionpulse daemon tick``.

The plist carries **no secrets** — the tick self-loads ``DIGEST_STORE_KEY`` from the 0600
``~/.config/actionpulse/env`` exactly like the MCP server — and it uses the **absolute**
``uv`` path because launchd runs agents with no shell PATH. ``install`` / ``uninstall`` are
idempotent, back up an existing plist byte-exact first, and drive ``launchctl
bootstrap``/``bootout``. ``render_plist`` is pure (used by tests) and platform-independent;
the ``launchctl`` calls are macOS-only and no-op elsewhere so a Linux/CI import is safe.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from digest_core import paths
from digest_core.mcp.jsonfile import backup

#: LaunchAgent label (reverse-DNS) — also the plist basename and launchctl service name.
LABEL = "ai.actionpulse.ingest"

#: PATH handed to the agent (launchd's default is minimal). Common tool locations so the
#: resolved ``uv`` can find its managed interpreter. No secrets.
_AGENT_PATH = ":".join(
    [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
)


def plist_path() -> Path:
    """The per-user LaunchAgent plist path."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def is_installed() -> bool:
    """True when the LaunchAgent plist exists (cross-platform: just a file check)."""
    return plist_path().exists()


def tick_command() -> List[str]:
    """Absolute exec argv for one tick. Prefer ``uv run --project <root>`` (matches the MCP
    entry) with an **absolute** ``uv`` — launchd has no PATH. Fall back to an installed
    ``actionpulse``, then this interpreter's ``-m digest_core.cli``."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(paths.PROJECT_ROOT), "actionpulse", "daemon", "tick"]
    direct = shutil.which("actionpulse")
    if direct:
        return [direct, "daemon", "tick"]
    return [sys.executable, "-m", "digest_core.cli", "daemon", "tick"]


def render_plist(interval_minutes: int, *, command: Optional[List[str]] = None) -> bytes:
    """The LaunchAgent plist as bytes. Pure — no filesystem writes, no launchctl.

    ``StartInterval`` (floor 60s) fires the tick every ``interval_minutes``; ``RunAtLoad``
    also fires once on load/login. Stdout/stderr go to ``var/logs/daemon.*`` (the tick logs
    counts only, never bodies). ``command`` overrides the resolved argv (tests)."""
    logs = paths.logs_dir()
    doc = {
        "Label": LABEL,
        "ProgramArguments": command or tick_command(),
        "StartInterval": max(60, int(interval_minutes) * 60),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(logs / "daemon.out.log"),
        "StandardErrorPath": str(logs / "daemon.err.log"),
        "WorkingDirectory": str(paths.PROJECT_ROOT),
        "EnvironmentVariables": {"PATH": _AGENT_PATH},
    }
    return plistlib.dumps(doc)


@dataclass
class LaunchdResult:
    action: str  # installed | updated | already_current | uninstalled | not_present
    plist: Path
    backup: Optional[Path] = None
    loaded: bool = False


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def install(
    interval_minutes: int, *, load: bool = True, command: Optional[List[str]] = None
) -> LaunchdResult:
    """Write the plist and (on macOS, when ``load``) (re)load it. Idempotent — an identical
    plist is not rewritten, only (re)loaded."""
    path = plist_path()
    new = render_plist(interval_minutes, command=command)
    existed = path.exists()
    prev = path.read_bytes() if existed else None
    if prev == new:
        loaded = _bootstrap() if load else False
        return LaunchdResult("already_current", path, loaded=loaded)
    bak = backup(path) if existed else None
    _write_atomic(path, new)
    loaded = False
    if load:
        if existed:
            _bootout()  # reload cleanly after an update
        loaded = _bootstrap()
    return LaunchdResult("updated" if existed else "installed", path, backup=bak, loaded=loaded)


def uninstall() -> LaunchdResult:
    """Unload (macOS) and remove the plist, leaving a byte-exact ``.bak``."""
    path = plist_path()
    if not path.exists():
        return LaunchdResult("not_present", path)
    _bootout()
    bak = backup(path)
    path.unlink()
    return LaunchdResult("uninstalled", path, backup=bak)


# -- launchctl (macOS only; every call is a no-op / False elsewhere) -----------


def _domain_target() -> str:
    return f"gui/{os.getuid()}"


def _run_launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _bootstrap() -> bool:
    """``launchctl bootstrap`` (load). True on success; never raises. If already loaded,
    ensure it's enabled and report False (caller treats it as "already running")."""
    if sys.platform != "darwin":
        return False
    r = _run_launchctl("bootstrap", _domain_target(), str(plist_path()))
    if r.returncode == 0:
        return True
    _run_launchctl("enable", f"{_domain_target()}/{LABEL}")
    return False


def _bootout() -> None:
    if sys.platform != "darwin":
        return
    _run_launchctl("bootout", f"{_domain_target()}/{LABEL}")


def start() -> bool:
    """Ensure loaded, then kickstart a run now. True if a run was kicked (macOS)."""
    if sys.platform != "darwin":
        return False
    _bootstrap()
    r = _run_launchctl("kickstart", "-k", f"{_domain_target()}/{LABEL}")
    return r.returncode == 0


def stop() -> None:
    """Unload the agent (macOS). It reloads on next install/login."""
    _bootout()
