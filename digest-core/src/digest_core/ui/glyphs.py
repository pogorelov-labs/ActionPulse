"""State glyphs with ASCII fallbacks (TERMINAL_DESIGN.md §2.1, §7).

Color is never the sole carrier: every state pairs a glyph with a word. On
non-UTF-8 locales the glyphs degrade to ASCII (same contract as install.sh).
"""

from __future__ import annotations

import sys


def glyphs_unicode_ok() -> bool:
    """True when stdout can encode the unicode glyph set."""
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


if glyphs_unicode_ok():
    OK = "✓"
    FAIL = "✗"
    WARN = "⚠"
    PULSE = "⌁"
    ARROW = "→"
    RETRY = "↻"
else:  # pragma: no cover — exercised only on non-UTF-8 locales
    OK = "OK"
    FAIL = "X"
    WARN = "!"
    PULSE = "~"
    ARROW = "->"
    RETRY = "~"
