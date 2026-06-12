"""Terminal design-system tokens for ActionPulse (TERMINAL_DESIGN.md §2–§3, T1).

Structural enforcement layer: every terminal surface imports colors, glyphs,
the spinner, and the shared Console from here — feature code never constructs
styles, consoles, or spinner names itself. `tests/test_terminal_conformance.py`
guards this boundary.
"""

from digest_core.ui.console import SPINNER, get_console, get_err_console
from digest_core.ui.glyphs import ARROW, FAIL, OK, PULSE, WARN, glyphs_unicode_ok
from digest_core.ui.select import choose
from digest_core.ui.sinks import PlainSink, RichLiveSink, resolve_sink
from digest_core.ui.theme import THEME, gradient_text

__all__ = [
    "ARROW",
    "FAIL",
    "OK",
    "PULSE",
    "PlainSink",
    "RichLiveSink",
    "SPINNER",
    "THEME",
    "WARN",
    "choose",
    "get_console",
    "get_err_console",
    "glyphs_unicode_ok",
    "gradient_text",
    "resolve_sink",
]
