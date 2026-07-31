"""systemd **user** units for the background ingestion tick (Linux; ACTPULSE-99).

A ``.service`` (oneshot: run one tick) plus a ``.timer`` that fires it. User units, not
system ones: no root, no unit outside ``~/.config/systemd/user``, and the tick runs as the
person whose store and 0600 env file it reads — the same trust boundary launchd's
per-user LaunchAgent has.

Invariants shared with the launchd backend: **no secrets in the unit** (the tick self-loads
``DIGEST_STORE_KEY``), an **absolute** ``uv`` path, and stdout/stderr appended to
``var/logs/daemon.*.log`` so ``daemon logs`` works identically here. The append: syntax
needs systemd 240+ (2018); older systems still get a working timer, they just log to the
journal instead — checked and reported rather than assumed.

``render_*`` are pure and platform-independent (a macOS box can render them, and CI does);
only the ``systemctl`` calls are gated.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import List, Optional

from digest_core import paths
from digest_core.daemon.scheduler import (
    AGENT_PATH,
    LABEL,
    SchedulerResult,
    log_paths,
    run,
    tick_command,
    write_atomic,
)
from digest_core.mcp.jsonfile import backup

NAME = "systemd"

#: Unit stem — `<stem>.service` + `<stem>.timer`. Derived from the shared LABEL so all
#: three backends are discoverable by the same name.
STEM = LABEL.replace("ai.", "").replace(".", "-")  # -> "actionpulse-ingest"


def unit_dir() -> Path:
    """The per-user unit directory (XDG-aware; systemd reads this without root)."""
    import os

    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def service_path() -> Path:
    return unit_dir() / f"{STEM}.service"


def timer_path() -> Path:
    return unit_dir() / f"{STEM}.timer"


def unit_path() -> Path:
    """The timer is the unit a user enables, so it is the canonical one to report."""
    return timer_path()


def is_supported() -> bool:
    """Linux with a usable ``systemctl``. The *user* manager also has to be reachable —
    a container or a bare chroot often has the binary and no session bus, and silently
    installing a timer nothing will ever run is worse than falling through to cron."""
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return False
    return run("systemctl", "--user", "is-system-running").returncode != 127


def is_installed() -> bool:
    """Just a file check — cross-platform, so ``installed_backend()`` works anywhere."""
    return timer_path().exists()


def render_service(*, command: Optional[List[str]] = None) -> str:
    """The oneshot service that runs a single tick. Pure."""
    argv = command or tick_command()
    out_log, err_log = log_paths()
    exec_start = " ".join(_quote(a) for a in argv)
    return "\n".join(
        [
            "[Unit]",
            "Description=ActionPulse background ingestion tick",
            "Documentation=https://github.com/pogorelov-labs/ActionPulse",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={paths.PROJECT_ROOT}",
            # No secrets: only a PATH, exactly like the launchd plist. The tick loads the
            # store key itself from the 0600 ~/.config/actionpulse/env.
            f"Environment=PATH={AGENT_PATH}",
            f"ExecStart={exec_start}",
            # Keep `daemon logs` meaningful on this backend too. append: is systemd 240+;
            # on older systemd these lines are ignored and output goes to the journal.
            f"StandardOutput=append:{out_log}",
            f"StandardError=append:{err_log}",
            # Be a good citizen on a laptop, mirroring the plist's Background/LowPriorityIO.
            "Nice=10",
            "IOSchedulingClass=idle",
            "",
        ]
    )


def render_timer(interval_minutes: int) -> str:
    """The timer that fires the service. Pure.

    ``OnUnitActiveSec`` gives a true "every N minutes" with no cron-style divisibility
    limits, and ``OnBootSec`` is the closest thing to launchd's ``RunAtLoad``.
    ``Persistent=true`` catches up one missed run after the machine was asleep — the point
    of the daemon is freshness, and a laptop that was shut overnight should not wait a
    whole interval to notice.
    """
    minutes = max(1, int(interval_minutes))
    return "\n".join(
        [
            "[Unit]",
            "Description=ActionPulse background ingestion timer",
            "",
            "[Timer]",
            f"OnBootSec={minutes}min",
            f"OnUnitActiveSec={minutes}min",
            "Persistent=true",
            "AccuracySec=1min",
            f"Unit={STEM}.service",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )


def _quote(arg: str) -> str:
    """Quote an ExecStart argument if it contains whitespace (paths can)."""
    return f'"{arg}"' if (" " in arg or "\t" in arg) else arg


def describe(interval_minutes: int) -> str:
    """Human-readable summary for --dry-run / the confirmation prompt."""
    return (
        f"systemd user timer every {interval_minutes} min\n"
        f"  service: {service_path()}\n"
        f"  timer:   {timer_path()}"
    )


def install(
    interval_minutes: int, *, load: bool = True, command: Optional[List[str]] = None
) -> SchedulerResult:
    """Write both units and (on Linux, when ``load``) reload + enable the timer.

    Idempotent: identical content is not rewritten, only re-enabled.
    """
    svc, tmr = service_path(), timer_path()
    new_svc = render_service(command=command).encode()
    new_tmr = render_timer(interval_minutes).encode()
    existed = svc.exists() or tmr.exists()
    unchanged = (
        svc.exists()
        and tmr.exists()
        and svc.read_bytes() == new_svc
        and tmr.read_bytes() == new_tmr
    )
    if unchanged:
        loaded = _enable() if load else False
        return SchedulerResult("already_current", tmr, loaded=loaded, backend=NAME)
    bak = backup(tmr) if tmr.exists() else (backup(svc) if svc.exists() else None)
    write_atomic(svc, new_svc)
    write_atomic(tmr, new_tmr)
    loaded = _enable() if load else False
    return SchedulerResult(
        "updated" if existed else "installed", tmr, backup=bak, loaded=loaded, backend=NAME
    )


def uninstall() -> SchedulerResult:
    """Disable + stop the timer and remove both units, leaving a byte-exact ``.bak``."""
    svc, tmr = service_path(), timer_path()
    if not (svc.exists() or tmr.exists()):
        return SchedulerResult("not_present", tmr, backend=NAME)
    if sys.platform == "linux":
        run("systemctl", "--user", "disable", "--now", f"{STEM}.timer")
    bak = backup(tmr) if tmr.exists() else None
    for path in (tmr, svc):
        if path.exists():
            path.unlink()
    if sys.platform == "linux":
        run("systemctl", "--user", "daemon-reload")
    return SchedulerResult("uninstalled", tmr, backup=bak, backend=NAME)


# -- systemctl (Linux only; every call is a no-op / False elsewhere) -----------


def _enable() -> bool:
    if sys.platform != "linux":
        return False
    run("systemctl", "--user", "daemon-reload")
    return run("systemctl", "--user", "enable", "--now", f"{STEM}.timer").returncode == 0


def start() -> bool:
    """Ensure the timer is enabled, then run one tick right now."""
    if sys.platform != "linux":
        return False
    _enable()
    return run("systemctl", "--user", "start", f"{STEM}.service").returncode == 0


def stop() -> None:
    """Stop the timer (units stay on disk; ``start`` re-enables)."""
    if sys.platform != "linux":
        return
    run("systemctl", "--user", "stop", f"{STEM}.timer")
