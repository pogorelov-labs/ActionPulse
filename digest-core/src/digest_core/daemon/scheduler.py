"""Pick and drive the platform's scheduler for ``actionpulse daemon tick`` (ACTPULSE-99).

The tick itself has always been portable — it is the no-LLM fetch+persist path, and it is
corp-aware (MM every tick; EWS only when its host resolves). Only *scheduling* was macOS
launchd, which meant store freshness — the thing that makes the MCP tools read current data
rather than whatever the last manual run left — was unavailable everywhere else.

Three backends, same surface, chosen in this order:

* **launchd** (macOS) — the original, unchanged;
* **systemd** user units (Linux) — a ``.service`` + ``.timer``, no root, ``linger`` optional;
* **cron** (anything else with ``crontab``) — a marked block, the lowest common denominator.

Every backend keeps the invariants the launchd one established: **no secrets in the unit**
(the tick self-loads ``DIGEST_STORE_KEY`` from the 0600 env file), an **absolute** ``uv``
path (schedulers run with a minimal or absent PATH), and stdout/stderr appended to
``var/logs/daemon.*.log`` so ``daemon logs`` reads the same files whichever backend ran.

Rendering is pure and importable everywhere — a macOS box can render a systemd unit and a
Linux CI box can render a plist. Only the ``systemctl``/``crontab``/``launchctl`` calls are
platform-gated, so the interesting logic is testable on every runner instead of hiding
behind skips that quietly never execute.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from digest_core import paths

#: Reverse-DNS label: launchd's plist basename + service name, systemd's unit stem, and the
#: marker cron uses to find its own block. One name everywhere so `uninstall` can never
#: strip a line it did not write.
LABEL = "ai.actionpulse.ingest"

#: PATH handed to the scheduled process. launchd gives an agent almost nothing and cron
#: gives it `/usr/bin:/bin`, so the resolved `uv` must be able to find its managed
#: interpreter. Locations only — no secrets.
AGENT_PATH = ":".join(
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


def tick_command() -> List[str]:
    """Absolute exec argv for one tick — identical for every backend.

    Prefers ``uv run --project <root>`` (matching the MCP server's entry) with an
    **absolute** ``uv``, because no scheduler guarantees a useful PATH. Falls back to an
    installed ``actionpulse``, then this interpreter's ``-m digest_core.cli``.
    """
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(paths.PROJECT_ROOT), "actionpulse", "daemon", "tick"]
    direct = shutil.which("actionpulse")
    if direct:
        return [direct, "daemon", "tick"]
    return [sys.executable, "-m", "digest_core.cli", "daemon", "tick"]


def log_paths() -> tuple[Path, Path]:
    """(stdout, stderr) log files — the pair ``daemon logs`` tails, for every backend."""
    logs = paths.logs_dir()
    return logs / "daemon.out.log", logs / "daemon.err.log"


@dataclass
class SchedulerResult:
    """Outcome of an install/uninstall, uniform across backends."""

    action: str  # installed | updated | already_current | uninstalled | not_present
    unit: Path  # the file that was written/removed (cron: the crontab is not a file we own)
    backup: Optional[Path] = None
    loaded: bool = False
    backend: str = ""


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def run(*args: str) -> subprocess.CompletedProcess:
    """Run a scheduler CLI, capturing output. Never raises on a non-zero exit — callers
    decide, and a background-control command must not explode in the user's face."""
    try:
        return subprocess.run(list(args), capture_output=True, text=True)
    except FileNotFoundError:  # the tool isn't installed
        return subprocess.CompletedProcess(list(args), 127, "", f"{args[0]}: not found")


# -- backend selection ---------------------------------------------------------


def available_backends() -> List[str]:
    """Backends usable on this host, best first."""
    from digest_core.daemon import crontab, launchd, systemd

    return [b.NAME for b in (launchd, systemd, crontab) if b.is_supported()]


def select(name: Optional[str] = None):
    """Return the scheduler backend MODULE for this host (or the named one).

    Order is deliberate: the platform-native manager first (launchd on macOS, systemd on
    Linux), cron only as the fallback — cron cannot express every interval (see
    ``crontab.schedule_for``) and has no concept of "run at boot/login".
    """
    from digest_core.daemon import crontab, launchd, systemd

    by_name = {m.NAME: m for m in (launchd, systemd, crontab)}
    if name:
        if name not in by_name:
            raise ValueError(f"unknown scheduler {name!r}; expected one of {sorted(by_name)}")
        return by_name[name]
    for module in (launchd, systemd, crontab):
        if module.is_supported():
            return module
    return None


def installed_backend():
    """The backend that actually has a unit on disk, if any.

    Checked independently of ``select`` so that a machine which gained (say) systemd after
    a cron install still finds — and can uninstall — the unit it really has.
    """
    from digest_core.daemon import crontab, launchd, systemd

    for module in (launchd, systemd, crontab):
        try:
            if module.is_installed():
                return module
        except Exception:  # noqa: BLE001 - a broken backend must not hide the others
            continue
    return None
