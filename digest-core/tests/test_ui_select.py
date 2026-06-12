"""Arrow-key selector (T6): pure keymap + key decoding."""

import io

import pytest

from digest_core.ui.select import CANCEL, CONFIRM, MOVE, NOOP, MenuState, _read_key, apply_key


class TestKeymap:
    def setup_method(self):
        self.state = MenuState(index=0, count=3, default_index=0)

    def test_down_and_wrap(self):
        action, s = apply_key(self.state, "down")
        assert action == MOVE and s.index == 1
        action, s = apply_key(MenuState(2, 3, 0), "j")
        assert s.index == 0  # wraps

    def test_up_and_wrap(self):
        action, s = apply_key(self.state, "up")
        assert action == MOVE and s.index == 2  # wraps backward
        action, s = apply_key(MenuState(2, 3, 0), "k")
        assert s.index == 1

    def test_enter_confirms_current(self):
        action, s = apply_key(MenuState(1, 3, 0), "enter")
        assert action == CONFIRM and s.index == 1

    def test_esc_cancels_to_default(self):
        action, s = apply_key(MenuState(2, 3, 1), "esc")
        assert action == CANCEL and s.index == 1  # the default, not the cursor

    def test_digit_quick_select(self):
        action, s = apply_key(self.state, "2")
        assert action == CONFIRM and s.index == 1

    def test_digit_out_of_range_is_noop(self):
        action, s = apply_key(self.state, "9")
        assert action == NOOP and s.index == 0

    def test_unknown_key_is_noop(self):
        action, s = apply_key(self.state, "x")
        assert action == NOOP


class TestReadKey:
    def test_decodes_arrows_enter_esc(self):
        assert _read_key(io.StringIO("\x1b[A")) == "up"
        assert _read_key(io.StringIO("\x1b[B")) == "down"
        assert _read_key(io.StringIO("\r")) == "enter"
        assert _read_key(io.StringIO("\n")) == "enter"
        assert _read_key(io.StringIO("\x1b")) == "esc"
        assert _read_key(io.StringIO("j")) == "j"

    def test_ctrl_c_raises(self):
        with pytest.raises(KeyboardInterrupt):
            _read_key(io.StringIO("\x03"))
