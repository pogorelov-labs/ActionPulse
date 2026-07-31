"""The cron fallback for the ingestion tick — lowest common denominator (ACTPULSE-99).

Used when neither launchd nor a usable systemd user manager exists. It manages **one
marked block** in the invoking user's crontab, so uninstall can only ever remove lines this
tool wrote:

    # BEGIN ai.actionpulse.ingest
    PATH=...
    */30 * * * * <absolute uv> run --project <root> actionpulse daemon tick >> out 2>> err
    # END ai.actionpulse.ingest

Two honest limitations, both surfaced rather than swallowed:

* **cron cannot express every interval.** ``*/7`` fires at :00,:07…:56 and then restarts at
  the hour, leaving a 4-minute gap; intervals above an hour must be expressed in hours.
  ``schedule_for`` returns the expression *and a note* whenever the result is not exactly
  "every N minutes", and the CLI prints it. Silently rounding a user's interval is the kind
  of drop this project treats as a bug.
* **No run-at-boot.** launchd has ``RunAtLoad`` and systemd has ``OnBootSec``; cron has
  neither, so after a reboot the first tick waits a full interval.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from digest_core.daemon.scheduler import (
    AGENT_PATH,
    LABEL,
    SchedulerResult,
    log_paths,
    run,
    tick_command,
)

NAME = "cron"

BEGIN = f"# BEGIN {LABEL}"
END = f"# END {LABEL}"


def unit_path() -> Path:
    """cron has no file we own — the crontab is the user's. Reported symbolically."""
    return Path("(user crontab)")


def is_supported() -> bool:
    """A ``crontab`` binary exists. Deliberately last in the selection order."""
    return sys.platform != "win32" and shutil.which("crontab") is not None


def _read_crontab() -> str:
    """Current crontab text ("" when there is none). Never raises."""
    result = run("crontab", "-l")
    return result.stdout if result.returncode == 0 else ""


def is_installed() -> bool:
    return BEGIN in _read_crontab()


def schedule_for(interval_minutes: int) -> Tuple[str, Optional[str]]:
    """(cron expression, note-if-inexact) for an every-N-minutes intent.

    The note is the whole point: cron's minute field is a repeating pattern within each
    hour, not a true interval, so only divisors of 60 behave as "every N minutes".
    """
    minutes = max(1, int(interval_minutes))
    if minutes < 60:
        if 60 % minutes == 0:
            return f"*/{minutes} * * * *", None
        return (
            f"*/{minutes} * * * *",
            f"cron repeats the minute pattern each hour, so */{minutes} fires at :00,"
            f":{minutes:02d},… and then restarts at the next hour — the gap across the hour"
            f" boundary is {60 % minutes} min shorter than {minutes}.",
        )
    exact_hours = minutes % 60 == 0
    hours = minutes // 60 if exact_hours else max(1, round(minutes / 60))
    # The hour field is 0-23: `*/24` and beyond are OUT OF RANGE, not merely approximate,
    # and would install a broken entry. Anything a day or longer becomes "daily at
    # midnight", which is the longest interval cron can actually express this way.
    if hours >= 24:
        return (
            "0 0 * * *",
            f"cron's hour field is 0-23, so every {minutes} min cannot be expressed;"
            " scheduled daily at midnight instead. Use systemd or launchd for a true"
            " interval.",
        )
    if exact_hours and 24 % hours == 0:
        return f"0 */{hours} * * *", None
    if exact_hours:
        return (
            f"0 */{hours} * * *",
            f"cron repeats the hour pattern each day, so */{hours} restarts at midnight —"
            f" the gap across midnight is shorter than {hours}h.",
        )
    return (
        f"0 */{hours} * * *",
        f"cron cannot express every {minutes} min; scheduled every {hours}h instead."
        " Use systemd or launchd for an exact interval.",
    )


def render(interval_minutes: int, *, command: Optional[List[str]] = None) -> str:
    """The marked crontab block. Pure — no crontab invocation."""
    argv = command or tick_command()
    out_log, err_log = log_paths()
    expr, _ = schedule_for(interval_minutes)
    line = " ".join(argv) + f" >> {out_log} 2>> {err_log}"
    return "\n".join([BEGIN, f"PATH={AGENT_PATH}", f"{expr} {line}", END, ""])


def describe(interval_minutes: int) -> str:
    expr, note = schedule_for(interval_minutes)
    text = f"cron entry `{expr}` in the user crontab"
    if note:
        text += f"\n  note: {note}"
    return text


def _strip_block(text: str) -> str:
    """Remove our marked block, leaving every other line byte-identical."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == BEGIN:
            skipping = True
            continue
        if line.strip() == END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out).strip("\n")


def _write_crontab(text: str) -> bool:
    """Replace the crontab. Returns success; never raises."""
    import subprocess

    payload = (text.rstrip("\n") + "\n") if text.strip() else ""
    try:
        proc = subprocess.run(["crontab", "-"], input=payload, text=True, capture_output=True)
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def install(
    interval_minutes: int, *, load: bool = True, command: Optional[List[str]] = None
) -> SchedulerResult:
    """Insert/replace our block in the user crontab. Idempotent."""
    current = _read_crontab()
    existed = BEGIN in current
    block = render(interval_minutes, command=command)
    rest = _strip_block(current)
    new = (rest + "\n\n" if rest else "") + block
    if existed and current.strip() == new.strip():
        return SchedulerResult("already_current", unit_path(), loaded=True, backend=NAME)
    ok = _write_crontab(new) if load else False
    return SchedulerResult(
        "updated" if existed else "installed", unit_path(), loaded=ok, backend=NAME
    )


def uninstall() -> SchedulerResult:
    """Remove only our marked block; the rest of the crontab is untouched."""
    current = _read_crontab()
    if BEGIN not in current:
        return SchedulerResult("not_present", unit_path(), backend=NAME)
    _write_crontab(_strip_block(current))
    return SchedulerResult("uninstalled", unit_path(), backend=NAME)


def start() -> bool:
    """cron has no 'run now'. The caller falls back to running a tick inline, which is
    what the user meant anyway."""
    return False


def stop() -> None:
    """Stopping cron means removing the entry — that is ``uninstall``. No-op here so the
    backend surface stays uniform; the CLI explains."""
    return None
