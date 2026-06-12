"""Shared Console factory (TERMINAL_DESIGN.md §2.2, §3).

One Console per process, themed with the semantic tokens. rich 14.x itself
implements the env contract we pin in the design doc: NO_COLOR (non-empty)
strips color and beats FORCE_COLOR; FORCE_COLOR (non-empty — including "0")
forces ANSI; TERM=dumb disables cursor control and fixes 80×25; COLUMNS/LINES
override detection. The factory exists so that contract has exactly one
entry point — do not construct Console elsewhere.
"""

from __future__ import annotations

from typing import Optional

from rich.console import Console

from digest_core.ui.theme import THEME

# The one brand spinner: rich "dots" (braille, 80 ms/frame ≈ 12.5 fps).
SPINNER = "dots"

_console: Optional[Console] = None
_err_console: Optional[Console] = None


def get_console() -> Console:
    """Process-wide themed Console (lazy singleton)."""
    global _console
    if _console is None:
        _console = Console(theme=THEME)
    return _console


def get_err_console() -> Console:
    """Themed stderr Console — progress/diagnostics channel (cargo/uv/gh
    convention: stdout stays clean for data)."""
    global _err_console
    if _err_console is None:
        _err_console = Console(theme=THEME, stderr=True)
    return _err_console
