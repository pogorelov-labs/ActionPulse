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
from digest_core.ui.glyphs import FAIL, OK, RETRY, WARN

#: Footer color warms after this many seconds (§3 attention shift).
_WARM_AFTER_S = 10.0
#: Visible fleet lanes (§4.3): beyond this, aggregate into one "+N more" line.
_LANE_CAP = 4
#: PlainSink prints a "still running" reassurance at most this often (§3:
#: terraform's 10 s "Still creating…" model — progress without log spam).
_REASSURE_EVERY_S = 10.0
#: Retry reasons truncate end-ellipsis at this width (§6.2).
_REASON_MAX = 60


def _short_reason(reason: str) -> str:
    reason = " ".join((reason or "").split())
    return reason if len(reason) <= _REASON_MAX else reason[: _REASON_MAX - 1] + "…"


def _progress_qty(done: int, total: Optional[int], unit: str) -> str:
    qty = f"{done}/{total}" if total else f"{done}"
    return f"{qty} {unit}" if unit else qty


def _fmt_duration(duration_ms: int) -> str:
    seconds = duration_ms / 1000
    if seconds >= 60:
        minutes = int(seconds // 60)
        return f"{minutes}m{int(seconds % 60):02d}s"
    return f"{seconds:.1f}s"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _phrase(counts: Dict[str, Any]) -> str:
    """Human funnel phrase (§4.2): known count shapes + a warn suffix for the
    optional retries/errors keys (present only when nonzero)."""
    counts = dict(counts)
    retries = counts.pop("retries", 0)
    errors = counts.pop("errors", 0)
    parts = [_phrase_base(counts)] if counts else []
    if retries:
        parts.append(f"[ap.warn]{RETRY}{retries} {'retry' if retries == 1 else 'retries'}[/]")
    if errors:
        parts.append(f"[ap.warn]{WARN}{errors} {'error' if errors == 1 else 'errors'}[/]")
    return " · ".join(parts)


def _phrase_base(counts: Dict[str, Any]) -> str:
    keys = set(counts)
    if keys == {"sections", "items", "tokens_in", "tokens_out"}:
        return (
            f"{counts['sections']} sections · {counts['items']} items · "
            f"↑{_fmt_tokens(counts['tokens_in'])} ↓{_fmt_tokens(counts['tokens_out'])} tok"
        )
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
    """Append-only build-log renderer; one line per event, scrollback-native.

    Intra-stage progress is *throttled here, not at the producer*: a retry is
    always printed (rare and meaningful), data progress collapses into one
    "still running" reassurance line per ≥10 s (terraform model) so non-TTY/CI
    logs stay quiet.
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self._console = console or get_err_console()
        self._now = now
        self._stage_started: float = 0.0
        self._last_note: float = 0.0

    def on_stage_start(self, stage: str) -> None:
        self._stage_started = self._now()
        self._last_note = self._stage_started

    def on_stage_progress(
        self, stage: str, done: int, total: Optional[int] = None, unit: str = "", detail: str = ""
    ) -> None:
        now = self._now()
        if now - self._last_note < _REASSURE_EVERY_S:
            return
        self._last_note = now
        qty = _progress_qty(done, total, unit)
        extra = f" · {detail}" if detail else ""
        elapsed = _fmt_duration(int((now - self._stage_started) * 1000))
        self._console.print(
            f"[ap.dim]· {stage.upper():<9} still running — {qty}{extra} ({elapsed})[/]",
            highlight=False,
        )

    def on_stage_retry(self, stage: str, attempt: int, max_attempts: int, reason: str) -> None:
        self._console.print(
            f"[ap.warn]{RETRY} {stage.upper():<9} retry {attempt}/{max_attempts}"
            f" — {_short_reason(reason)}[/]",
            highlight=False,
        )

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
        self._progress: str = ""  # latest intra-stage data phrase (footer only)
        self._retry_note: str = ""  # active transient-retry note (warms the footer)
        self._lanes: "dict[str, Dict[str, Any]]" = {}  # §4.3 fleet lanes, by model

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
        # A pending retry warms the footer immediately — error responsiveness
        # must not wait for the 10 s attention shift.
        style = "ap.warn" if (self._retry_note or elapsed > _WARM_AFTER_S) else "ap.accent"
        label = Text.assemble((f"{self._stage.upper():<9}", "ap.em"))
        if self._progress:
            label.append(f" {self._progress}", "default")
            label.append(" ·", "ap.dim")
        label.append(f" {elapsed:.1f}s", style)
        line = Spinner(SPINNER, text=label, style=style)
        notes = []
        if self._retry_note:
            notes.append(Text(f"  └ {self._retry_note}", style="ap.warn"))
        if self._llm_note:
            notes.append(Text(f"  └ {self._llm_note}", style="ap.dim"))
        notes.extend(self._lane_lines())
        return Group(line, *notes) if notes else line

    def _lane_lines(self) -> "list[Text]":
        """§4.3: one line per model lane, capped at _LANE_CAP, aggregate beyond."""
        lanes = list(self._lanes.values())
        if not lanes:
            return []
        lines: "list[Text]" = []
        visible = lanes if len(lanes) <= _LANE_CAP else lanes[: _LANE_CAP - 1]
        for index, lane in enumerate(visible):
            last = index == len(visible) - 1 and len(lanes) <= _LANE_CAP
            branch = "└" if last else "├"
            bits = [str(lane.get("stage") or "fleet")]
            if lane.get("in_flight"):
                bits.append(f"{lane['in_flight']} in-flight")
            calls = lane.get("calls") or 0
            bits.append(f"{calls} {'call' if calls == 1 else 'calls'}")
            if lane.get("rpm_cap"):
                bits.append(f"RPM {lane.get('rpm_used', 0)}/{lane['rpm_cap']}")
            penalty = lane.get("penalty_remaining_s") or 0
            if penalty:
                bits.append(f"429 cool-down {penalty:.0f}s")
            style = "ap.warn" if penalty else ("default" if lane.get("in_flight") else "ap.dim")
            lines.append(
                Text(f"  {branch} {lane.get('model', '?'):<20} {' · '.join(bits)}", style=style)
            )
        if len(lanes) > _LANE_CAP:
            hidden = lanes[_LANE_CAP - 1 :]
            in_flight = sum(1 for lane in hidden if lane.get("in_flight"))
            lines.append(
                Text(
                    f"  └ +{len(hidden)} more" + (f" · {in_flight} in-flight" if in_flight else ""),
                    style="ap.dim",
                )
            )
        return lines

    # -- events ---------------------------------------------------------------

    def on_stage_start(self, stage: str) -> None:
        self._ensure_live()
        self._stage = stage
        self._stage_started = self._now()
        self._llm_note = ""
        self._progress = ""
        self._retry_note = ""
        self._lanes = {}

    def on_stage_progress(
        self, stage: str, done: int, total: Optional[int] = None, unit: str = "", detail: str = ""
    ) -> None:
        qty = _progress_qty(done, total, unit)
        self._progress = f"{qty} · {detail}" if detail else qty
        # Data flowing again means the retry succeeded — clear the warn note.
        self._retry_note = ""

    def on_stage_retry(self, stage: str, attempt: int, max_attempts: int, reason: str) -> None:
        self._retry_note = f"{RETRY} retry {attempt}/{max_attempts} — {_short_reason(reason)}"

    def on_stage_end(self, stage: str, counts: Dict[str, Any], duration_ms: int) -> None:
        self._print(_ok_line(stage, counts, duration_ms))
        self._stage = None
        self._llm_note = ""
        self._progress = ""
        self._retry_note = ""
        self._lanes = {}

    def on_stage_failed(self, stage: str, error: str) -> None:
        self._print(_fail_line(stage, error))
        self._stage = None
        self._llm_note = ""
        self._progress = ""
        self._retry_note = ""
        self._lanes = {}

    def on_llm_attempt(self, model: str, attempt: int, max_attempts: int) -> None:
        self._llm_note = f"attempt {attempt}/{max_attempts} · {model}"

    def on_lane_update(self, lane: str, state: Dict[str, Any]) -> None:
        # §4.3: lanes render only while their stage is live (cleared on stage
        # transitions); the permanent history line carries the totals.
        self._lanes[lane] = dict(state)

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
