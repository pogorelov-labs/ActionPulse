"""Unit tests for the digest_core.ui token module (T1)."""

from digest_core import ui
from digest_core.ui.theme import GRAD_END, GRAD_START


class TestTheme:
    def test_semantic_tokens_present(self):
        for token in ("ap.ok", "ap.warn", "ap.err", "ap.accent", "ap.dim", "ap.em", "ap.rule"):
            assert token in ui.THEME.styles, token

    def test_tokens_use_named_ansi_colors(self):
        # §2.1: semantics via terminal-palette names, never RGB.
        for token, style in ui.THEME.styles.items():
            if not token.startswith("ap."):
                continue  # Theme.styles also carries rich's own defaults
            assert "rgb" not in str(style), f"{token} uses RGB"
            assert "#" not in str(style), f"{token} uses hex"


class TestGradient:
    def test_preserves_text(self):
        out = ui.gradient_text("ActionPulse")
        assert out.plain == "ActionPulse"

    def test_spans_interpolate_between_stops(self):
        out = ui.gradient_text("ab")
        styles = [str(span.style) for span in out.spans]
        assert f"rgb({GRAD_START[0]},{GRAD_START[1]},{GRAD_START[2]})" in styles[0]
        assert f"rgb({GRAD_END[0]},{GRAD_END[1]},{GRAD_END[2]})" in styles[-1]


class TestConsoleFactory:
    def test_singleton(self):
        assert ui.get_console() is ui.get_console()

    def test_theme_attached(self):
        console = ui.get_console()
        # A themed console resolves our tokens instead of erroring.
        assert console.get_style("ap.ok") is not None


class TestGlyphsAndSpinner:
    def test_spinner_is_dots(self):
        assert ui.SPINNER == "dots"

    def test_glyphs_nonempty(self):
        for glyph in (ui.OK, ui.FAIL, ui.WARN, ui.PULSE, ui.ARROW):
            assert glyph
