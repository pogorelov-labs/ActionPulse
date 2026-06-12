"""Semantic color tokens (TERMINAL_DESIGN.md §2.1).

Named ANSI colors only — they inherit the user's terminal palette, so light
and dark backgrounds both work without runtime background detection. The
brand gradient is the single sanctioned RGB use; rich auto-downsamples it on
256/16-color terminals (corp Terminal.app has no truecolor).
"""

from __future__ import annotations

from rich.text import Text
from rich.theme import Theme

# Semantic tokens — reference these in markup ("[ap.ok]✓[/]"), never raw colors.
THEME = Theme(
    {
        "ap.ok": "green",
        "ap.warn": "yellow",
        "ap.err": "red",
        "ap.accent": "cyan",
        "ap.accent.bold": "bold cyan",
        "ap.dim": "dim",
        "ap.em": "bold",
        "ap.rule": "dim cyan",
        "ap.rule.attn": "dim yellow",
    }
)

# Brand gradient stops (cyan -> violet) — the banner pulse line only.
GRAD_START = (34, 211, 238)
GRAD_END = (167, 139, 250)


def gradient_text(s: str) -> Text:
    """Brand gradient across a string (banner/title use only)."""
    text = Text()
    n = max(len(s) - 1, 1)
    for i, ch in enumerate(s):
        t = i / n
        r = int(GRAD_START[0] + (GRAD_END[0] - GRAD_START[0]) * t)
        g = int(GRAD_START[1] + (GRAD_END[1] - GRAD_START[1]) * t)
        b = int(GRAD_START[2] + (GRAD_END[2] - GRAD_START[2]) * t)
        text.append(ch, style=f"bold rgb({r},{g},{b})")
    return text
