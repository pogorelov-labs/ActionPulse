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
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from digest_core import paths
from digest_core.daemon.scheduler import (
    AGENT_PATH as _AGENT_PATH,
)
from digest_core.daemon.scheduler import (
    LABEL,
    SchedulerResult,
    log_paths,
    write_atomic,
)
from digest_core.daemon.scheduler import (
    tick_command as _shared_tick_command,
)
from digest_core.mcp.jsonfile import backup

#: This backend's name in the scheduler registry (ACTPULSE-99).
NAME = "launchd"


def plist_path() -> Path:
    """The per-user LaunchAgent plist path."""
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def is_installed() -> bool:
    """True when the LaunchAgent plist exists (cross-platform: just a file check)."""
    return plist_path().exists()


def tick_command() -> List[str]:
    """Absolute exec argv for one tick — shared with every backend (see scheduler.py)."""
    return _shared_tick_command()


def is_supported() -> bool:
    """launchd is macOS-only."""
    return sys.platform == "darwin"


def unit_path() -> Path:
    """Registry-uniform alias for :func:`plist_path`."""
    return plist_path()


def describe(interval_minutes: int) -> str:
    return f"launchd LaunchAgent every {interval_minutes} min\n  plist: {plist_path()}"


def render_plist(interval_minutes: int, *, command: Optional[List[str]] = None) -> bytes:
    """The LaunchAgent plist as bytes. Pure — no filesystem writes, no launchctl.

    ``StartInterval`` (floor 60s) fires the tick every ``interval_minutes``; ``RunAtLoad``
    also fires once on load/login. Stdout/stderr go to ``var/logs/daemon.*`` (the tick logs
    counts only, never bodies). ``command`` overrides the resolved argv (tests)."""
    out_log, err_log = log_paths()
    doc = {
        "Label": LABEL,
        "ProgramArguments": command or tick_command(),
        "StartInterval": max(60, int(interval_minutes) * 60),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(out_log),
        "StandardErrorPath": str(err_log),
        "WorkingDirectory": str(paths.PROJECT_ROOT),
        "EnvironmentVariables": {"PATH": _AGENT_PATH},
    }
    return plistlib.dumps(doc)


#: Kept as the historical name so existing imports/tests keep working. The shared type
#: names the written file ``unit`` (not ``plist``) because the CLI now handles three
#: backends through one result.
LaunchdResult = SchedulerResult


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
        return SchedulerResult("already_current", path, loaded=loaded, backend=NAME)
    bak = backup(path) if existed else None
    write_atomic(path, new)
    loaded = False
    if load:
        if existed:
            _bootout()  # reload cleanly after an update
        loaded = _bootstrap()
    return SchedulerResult(
        "updated" if existed else "installed", path, backup=bak, loaded=loaded, backend=NAME
    )


def uninstall() -> LaunchdResult:
    """Unload (macOS) and remove the plist, leaving a byte-exact ``.bak``."""
    path = plist_path()
    if not path.exists():
        return SchedulerResult("not_present", path, backend=NAME)
    _bootout()
    bak = backup(path)
    path.unlink()
    return SchedulerResult("uninstalled", path, backup=bak, backend=NAME)


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
