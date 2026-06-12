"""Real-pty round-trips for the U3 run selector (TERMINAL_DESIGN.md §5.2/§5.5).

Render-only checks cannot catch tty-layer regressions (the buffered-read hang,
the TCSAFLUSH eaten-keystroke, lone-Esc disambiguation), so every interactive
keypath gets a real pty drive: spawn a driver process whose stdin/stdout is a
pty slave, write key bytes to the master, parse a RESULT: marker.

Notes for future keypaths:
- keys may be written immediately after spawn — `tty.setcbreak(fd, TCSANOW)`
  preserves input typed before cbreak engages (that is the point of TCSANOW);
- Ctrl+C cannot be tested by writing b"\\x03": the slave is not the child's
  controlling terminal here, so ISIG never fires — send SIGINT directly.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

DIGEST_CORE = Path(__file__).resolve().parents[1]

DRIVER = """
import json, sys
from rich.console import Console
from digest_core.ui import THEME
from digest_core.ui import menu as menu_mod

console = Console(theme=THEME, force_terminal=True, width=80)
last = menu_mod.RunChoice(from_date="2026-06-10", window="rolling_24h", force=True)
try:
    choice = menu_mod.choose_run_options(console, last=last)
except KeyboardInterrupt:
    print("RESULT:interrupted", flush=True)
    sys.exit(130)
payload = choice.__dict__ if choice is not None else None
print("RESULT:" + json.dumps(payload), flush=True)
"""


def _spawn():
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-c", DRIVER],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        cwd=str(DIGEST_CORE),
        env={**os.environ, "PYTHONPATH": "src"},
    )
    os.close(slave)
    return proc, master


def _read_until(master, proc, pattern: str, timeout_s: float = 20.0) -> str:
    out = b""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if pattern.encode() in out:
                break
        elif proc.poll() is not None:
            # Drained and exited — one last non-blocking sweep.
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                break
    return out.decode("utf-8", "replace")


def _drive(keys: bytes, *, send_sigint: bool = False) -> tuple[str, int]:
    proc, master = _spawn()
    try:
        # Wait for the selector to render before interacting; key bytes
        # written earlier would still be safe (TCSANOW), but the SIGINT case
        # must not race the menu setup.
        _read_until(master, proc, "time period")
        if send_sigint:
            proc.send_signal(signal.SIGINT)
        if keys:
            os.write(master, keys)
        out = _read_until(master, proc, "RESULT:")
        proc.wait(timeout=10)
        return out, proc.returncode
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        os.close(master)


class TestRunOptionsPty:
    def test_arrow_down_enter_selects_rolling_24h(self):
        out, code = _drive(b"\x1b[B\r")  # ↓ then Enter -> option 2
        assert code == 0
        assert '"window": "rolling_24h"' in out

    def test_quick_select_digit(self):
        out, code = _drive(b"1")  # quick-select 1 -> Today (calendar day)
        assert code == 0
        assert '"from_date": "today"' in out
        assert '"window": "calendar_day"' in out

    def test_repeat_last_label_renders_stored_params(self):
        # The submenu must show the absolute stored params before any choice.
        out, code = _drive(b"\x1b")  # then back out
        assert "2026-06-10" in out
        assert "rolling 24h" in out

    def test_lone_esc_backs_out_without_running(self):
        out, code = _drive(b"\x1b")  # lone Esc -> 50 ms disambiguation -> back
        assert code == 0
        assert "RESULT:null" in out

    def test_ctrl_c_aborts_130(self):
        out, code = _drive(b"", send_sigint=True)
        assert code == 130
        assert "RESULT:interrupted" in out
