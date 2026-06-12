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

import os
import select as _select
import sys
import termios
import tty
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from rich.console import Console

from digest_core.ui.console import get_console
from digest_core.ui.glyphs import glyphs_unicode_ok

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


def resolve_choice(
    action: str,
    state: MenuState,
    options: Sequence[Tuple[str, str]],
    cancel_value: Optional[str] = None,
) -> Tuple[str, str]:
    """Final (value, text) for a finished selection.

    Esc semantics (§5.2): inside a *question* (no cancel_value) Esc restores
    the default option. At a top-level navigation menu the caller passes
    cancel_value (e.g. "quit") — Esc then dismisses the menu instead of
    committing the highlighted action (a cancel gesture must never run
    something).
    """
    if action == CANCEL and cancel_value is not None:
        for value, text in options:
            if value == cancel_value:
                return value, text
        return cancel_value, cancel_value
    return options[state.index]


def _read_key(stdin=sys.stdin) -> str:
    """One logical key from a cbreak tty: arrows decoded, Ctrl+C raised.

    On a real terminal we read with ``os.read(fd, 1)`` rather than
    ``stdin.read(1)``: TextIOWrapper buffers, and in cbreak mode its buffered
    read does not reliably return a single keystroke (it can block waiting to
    fill its buffer) — os.read returns each byte as it arrives. Test streams
    (io.StringIO) have no usable fd, so they keep the buffered path unchanged.
    """
    try:
        fd = stdin.fileno()
        use_fd = stdin.isatty()
    except (OSError, ValueError, AttributeError):
        use_fd = False

    if use_fd:
        read1 = lambda: os.read(fd, 1).decode("latin-1")  # noqa: E731 - byte→1:1 char
        waitable = fd
    else:
        read1 = lambda: stdin.read(1)  # noqa: E731
        waitable = stdin

    ch = read1()
    if ch == "\x03":
        # In cbreak mode ISIG stays on, so a real terminal delivers ^C as
        # SIGINT (KeyboardInterrupt) before we ever see a byte — this branch
        # is defense for raw-mode-like environments where \x03 does arrive.
        raise KeyboardInterrupt
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x1b":
        # ESC disambiguation (research: prompt_toolkit ttimeoutlen / ncurses
        # ESCDELAY): an arrow's "[X" tail arrives within milliseconds; a lone
        # Esc has none — without the timeout a bare Esc would block on read.
        try:
            ready = _select.select([waitable], [], [], 0.05)[0]
        except Exception:
            ready = [waitable]  # non-fd streams (tests): data is already buffered
        if not ready:
            return "esc"
        seq = read1()
        if seq == "[":
            final = read1()
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
    cancel_value: Optional[str] = None,
) -> str:
    """Interactive picker; returns the chosen option value.

    Caller contract: stdin is a tty (gate with ``sys.stdin.isatty()``); at
    most 9 options (the 1-9 quick-select invariant — asserted).
    cancel_value: what Esc returns at a top-level menu (e.g. "quit"); without
    it Esc restores the default option (wizard-question semantics, §5.2).
    """
    assert 1 <= len(options) <= 9, "menus are 1..9 options (§5.2 quick-select)"
    out = console or get_console()
    state = MenuState(default_index, len(options), default_index)

    unicode_ok = glyphs_unicode_ok()
    arrows = "↑↓" if unicode_ok else "arrows"
    sep = " · " if unicode_ok else " / "
    esc_hint = "Esc = cancel" if cancel_value is not None else "Esc = default"
    out.print(f"[ap.accent.bold]{label}[/] [ap.dim]({arrows}/jk{sep}Enter{sep}{esc_hint})[/]")
    pointer = "❯" if unicode_ok else ">"

    def render(first: bool) -> None:
        if not first:
            # Overwrite, don't clear (§3): move up over the option block.
            out.file.write(f"\x1b[{len(options)}A")
        for i, (_, text) in enumerate(options):
            marker = f"[ap.accent]{pointer}[/]" if i == state.index else " "
            style = "ap.em" if i == state.index else "ap.dim"
            out.print(f" {marker} [{style}]{i + 1}. {text}[/]", highlight=False)

    render(first=True)
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    action = CONFIRM
    try:
        # TCSANOW, not the default TCSAFLUSH: setcbreak's default discards
        # pending input, silently eating a keystroke typed in the window
        # between the menu rendering and cbreak engaging (found via pty test:
        # an early Esc vanished and the menu blocked forever).
        tty.setcbreak(fd, termios.TCSANOW)
        while True:
            action, state = apply_key(state, _read_key())
            if action == MOVE:
                render(first=False)
            elif action in (CONFIRM, CANCEL):
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    value, text = resolve_choice(action, state, options, cancel_value)
    # Collapse the menu into a single answer line (scrollback stays tidy).
    out.file.write(f"\x1b[{len(options) + 1}A\x1b[J")
    out.print(f"[ap.accent.bold]{label}[/]: {text}", highlight=False)
    return value
