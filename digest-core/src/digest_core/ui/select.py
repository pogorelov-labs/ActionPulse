"""Arrow-key select menu (TERMINAL_DESIGN.md §5.2, roadmap T6).

Line-oriented and scrollback-friendly: no alternate screen, **no mouse**
(evidence-backed §5.3 rule), overwrite-don't-clear redraw. Keymap per the
cross-library convention table:

- ``↑/↓`` and ``j/k`` move; ``1-9`` quick-select (menus are ≤9 options);
- ``Enter`` confirms; ``Esc`` cancels the *question* — returns the default,
  never exits the program; ``Ctrl+C`` aborts (KeyboardInterrupt propagates
  to the wizard's exit-130 contract).

The pure state machine (``apply_key``) is unit-tested; the tty layer
(cbreak + escape-sequence reads) restores terminal attributes in a finally.
Callers gate on ``sys.stdin.isatty()`` and keep their piped/scripted prompt
path as the fallback — scripted answer protocols stay stable.
"""

from __future__ import annotations

import select as _select
import sys
import termios
import tty
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from rich.console import Console

from digest_core.ui.console import get_console

# Action results from apply_key
MOVE = "move"
CONFIRM = "confirm"
CANCEL = "cancel"
NOOP = "noop"


@dataclass
class MenuState:
    index: int
    count: int
    default_index: int


def apply_key(state: MenuState, key: str) -> Tuple[str, MenuState]:
    """Pure §5.2 keymap: returns (action, new_state)."""
    index = state.index
    if key in ("up", "k"):
        index = (index - 1) % state.count
        return MOVE, MenuState(index, state.count, state.default_index)
    if key in ("down", "j"):
        index = (index + 1) % state.count
        return MOVE, MenuState(index, state.count, state.default_index)
    if key == "enter":
        return CONFIRM, state
    if key == "esc":
        return CANCEL, MenuState(state.default_index, state.count, state.default_index)
    if key.isdigit() and key != "0":
        slot = int(key) - 1
        if slot < state.count:
            return CONFIRM, MenuState(slot, state.count, state.default_index)
    return NOOP, state


def _read_key(stdin=sys.stdin) -> str:
    """One logical key from a cbreak tty: arrows decoded, Ctrl+C raised."""
    ch = stdin.read(1)
    if ch == "\x03":  # Ctrl+C in cbreak mode arrives as a byte
        raise KeyboardInterrupt
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x1b":
        # ESC disambiguation (research: prompt_toolkit ttimeoutlen / ncurses
        # ESCDELAY): an arrow's "[X" tail arrives within milliseconds; a lone
        # Esc has none — without the timeout a bare Esc would block on read.
        try:
            ready = _select.select([stdin], [], [], 0.05)[0]
        except Exception:
            ready = [stdin]  # non-fd streams (tests): data is already buffered
        if not ready:
            return "esc"
        seq = stdin.read(1)
        if seq == "[":
            final = stdin.read(1)
            if final == "A":
                return "up"
            if final == "B":
                return "down"
            return "noop"
        return "esc"
    return ch


def choose(
    label: str,
    options: Sequence[Tuple[str, str]],
    default_index: int = 0,
    console: Optional[Console] = None,
) -> str:
    """Interactive picker; returns the chosen option value.

    Caller contract: stdin is a tty (gate with ``sys.stdin.isatty()``); at
    most 9 options (the 1-9 quick-select invariant — asserted).
    """
    assert 1 <= len(options) <= 9, "menus are 1..9 options (§5.2 quick-select)"
    out = console or get_console()
    state = MenuState(default_index, len(options), default_index)

    out.print(f"[ap.accent.bold]{label}[/] [ap.dim](↑↓/jk · Enter · Esc = default)[/]")

    def render(first: bool) -> None:
        if not first:
            # Overwrite, don't clear (§3): move up over the option block.
            out.file.write(f"\x1b[{len(options)}A")
        for i, (_, text) in enumerate(options):
            marker = "[ap.accent]❯[/]" if i == state.index else " "
            style = "ap.em" if i == state.index else "ap.dim"
            out.print(f" {marker} [{style}]{i + 1}. {text}[/]", highlight=False)

    render(first=True)
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            action, state = apply_key(state, _read_key())
            if action == MOVE:
                render(first=False)
            elif action in (CONFIRM, CANCEL):
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    value, text = options[state.index]
    # Collapse the menu into a single answer line (scrollback stays tidy).
    out.file.write(f"\x1b[{len(options) + 1}A\x1b[J")
    out.print(f"[ap.accent.bold]{label}[/]: {text}", highlight=False)
    return value
