"""Progress renderers (TERMINAL_DESIGN.md §4, roadmap T3+T4).

Two renderers over the same ProgressSink events:

- ``PlainSink`` (T3) — append-only build log: one permanent line per stage
  transition, no cursor movement, no animation. The non-TTY/CI contract from
  the degradation matrix (§7).
- ``RichLiveSink`` (T4) — the split-region live display (§4.1): completed
  stages become the same permanent lines, printed into native scrollback
  *above* a single animated footer (spinner + current stage + elapsed). The
  footer warms to ``ap.warn`` after 10 s (§3), stays a few lines tall
  (§4.5 resize honesty), and is transient — it vanishes at run end leaving
  only the permanent history.

Progress goes to **stderr** (cargo/uv/gh convention): stdout stays clean for
data. Color follows the env contract automatically via the themed console.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Optional

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from digest_core.progress import NullSink, ProgressSink
from digest_core.ui.console import SPINNER, get_err_console
from digest_core.ui.glyphs import FAIL, OK, WARN

#: Footer color warms after this many seconds (§3 attention shift).
_WARM_AFTER_S = 10.0


def _fmt_duration(duration_ms: int) -> str:
    seconds = duration_ms / 1000
    if seconds >= 60:
        minutes = int(seconds // 60)
        return f"{minutes}m{int(seconds % 60):02d}s"
    return f"{seconds:.1f}s"


def _phrase(counts: Dict[str, Any]) -> str:
    """Human funnel phrase for the known count shapes (§4.2 vocabulary)."""
    keys = set(counts)
    if keys == {"messages"}:
        return f"{counts['messages']} messages"
    if keys == {"messages", "threads"}:
        return f"{counts['messages']} messages → {counts['threads']} threads"
    if keys == {"threads", "chunks"}:
        return f"{counts['threads']} threads → {counts['chunks']} chunks"
    if keys == {"selected", "of"}:
        return f"{counts['selected']}/{counts['of']} chunks selected"
    if keys == {"sections", "items"}:
        return f"{counts['sections']} sections · {counts['items']} items"
    if keys == {"items"}:
        return f"{counts['items']} items"
    return " · ".join(f"{k}={v}" for k, v in counts.items())


# --- permanent-line builders (shared by both renderers) ---------------------


def _ok_line(stage: str, counts: Dict[str, Any], duration_ms: int) -> str:
    body = f"{stage.upper():<9} {_phrase(counts)}".rstrip()
    return f"[ap.ok]{OK}[/] {body} [ap.dim]({_fmt_duration(duration_ms)})[/]"


def _fail_line(stage: str, error: str) -> str:
    return f"[ap.err]{FAIL}[/] {stage.upper():<9} failed — {error}"


def _delivery_line(target: str, ok: bool, detail: Optional[str]) -> str:
    if ok:
        return f"[ap.ok]{OK}[/] delivered → {target}"
    suffix = f" — {detail}" if detail else ""
    return f"[ap.warn]{WARN}[/] delivery to {target} failed{suffix}"


class PlainSink(ProgressSink):
    """Append-only build-log renderer; one line per event, scrollback-native."""

    def __init__(self, console: Optional[Console] = None):
        self._console = console or get_err_console()

    def on_stage_end(self, stage: str, counts: Dict[str, Any], duration_ms: int) -> None:
        self._console.print(_ok_line(stage, counts, duration_ms), highlight=False)

    def on_stage_failed(self, stage: str, error: str) -> None:
        self._console.print(_fail_line(stage, error), highlight=False)

    def on_llm_attempt(self, model: str, attempt: int, max_attempts: int) -> None:
        self._console.print(
            f"[ap.dim]· llm attempt {attempt}/{max_attempts} · {model}[/]", highlight=False
        )

    def on_delivery(self, target: str, ok: bool, detail: Optional[str] = None) -> None:
        self._console.print(_delivery_line(target, ok, detail), highlight=False)


class RichLiveSink(ProgressSink):
    """Split-region live renderer (§4.1): permanent history above, one
    animated footer below. Pinned Live parameters per §4.5."""

    def __init__(
        self,
        console: Optional[Console] = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self._console = console or get_err_console()
        self._now = now
        self._live: Optional[Live] = None
        self._stage: Optional[str] = None
        self._stage_started: float = 0.0
        self._llm_note: str = ""

    # -- lifecycle ------------------------------------------------------------

    def _ensure_live(self) -> None:
        if self._live is None:
            self._live = Live(
                get_renderable=self._footer,
                console=self._console,
                refresh_per_second=10,
                transient=True,
                vertical_overflow="ellipsis",
            )
            self._live.start()

    def _print(self, markup: str) -> None:
        # With Live active, console.print lands ABOVE the footer (rich erases,
        # prints, repaints) — the split-region mechanics from the design doc.
        self._console.print(markup, highlight=False)

    # -- footer (re-rendered by Live's refresh thread) -------------------------

    def _footer(self) -> RenderableType:
        if not self._stage:
            return Text("")
        elapsed = self._now() - self._stage_started
        style = "ap.warn" if elapsed > _WARM_AFTER_S else "ap.accent"
        label = Text.assemble(
            (f"{self._stage.upper():<9}", "ap.em"),
            (f" {elapsed:.1f}s", style),
        )
        line = Spinner(SPINNER, text=label, style=style)
        if self._llm_note:
            return Group(line, Text(f"  └ {self._llm_note}", style="ap.dim"))
        return line

    # -- events ---------------------------------------------------------------

    def on_stage_start(self, stage: str) -> None:
        self._ensure_live()
        self._stage = stage
        self._stage_started = self._now()
        self._llm_note = ""

    def on_stage_end(self, stage: str, counts: Dict[str, Any], duration_ms: int) -> None:
        self._print(_ok_line(stage, counts, duration_ms))
        self._stage = None
        self._llm_note = ""

    def on_stage_failed(self, stage: str, error: str) -> None:
        self._print(_fail_line(stage, error))
        self._stage = None
        self._llm_note = ""

    def on_llm_attempt(self, model: str, attempt: int, max_attempts: int) -> None:
        self._llm_note = f"attempt {attempt}/{max_attempts} · {model}"

    def on_delivery(self, target: str, ok: bool, detail: Optional[str] = None) -> None:
        self._print(_delivery_line(target, ok, detail))

    def on_run_end(self, status: str) -> None:
        # Release the terminal: stop the refresh thread, erase the footer
        # (transient=True), restore the cursor — on every exit path.
        if self._live is not None:
            self._stage = None
            self._live.stop()
            self._live = None


def resolve_sink(progress: str, stdout_is_tty: bool) -> ProgressSink:
    """Map the --progress flag to a sink (pure; unit-tested).

    auto: live footer on a real terminal, plain otherwise; CI always gets
    plain even on a TTY (clig.dev / degradation matrix §7).
    """
    if progress == "none":
        return NullSink()
    if progress == "plain":
        return PlainSink()
    if progress == "live":
        return RichLiveSink()
    # auto
    if stdout_is_tty and not os.environ.get("CI"):
        return RichLiveSink()
    return PlainSink()
