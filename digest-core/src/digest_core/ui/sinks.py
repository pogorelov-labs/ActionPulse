"""Progress renderers (TERMINAL_DESIGN.md §4, roadmap T3).

``PlainSink`` is the append-only renderer — the terraform model: one permanent
line per stage transition, no cursor movement, no animation. It is the
non-TTY/CI contract from the degradation matrix (§7) and, until the live
footer lands (T4), also the interim TTY renderer behind ``--progress=auto``.

Progress goes to **stderr** (cargo/uv/gh convention): stdout stays clean for
data. Color follows the env contract automatically via the themed console.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from rich.console import Console

from digest_core.progress import NullSink, ProgressSink
from digest_core.ui.console import get_err_console
from digest_core.ui.glyphs import FAIL, OK, WARN


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


class PlainSink(ProgressSink):
    """Append-only build-log renderer; one line per event, scrollback-native."""

    def __init__(self, console: Optional[Console] = None):
        self._console = console or get_err_console()

    def on_stage_end(self, stage: str, counts: Dict[str, Any], duration_ms: int) -> None:
        phrase = _phrase(counts)
        body = f"{stage.upper():<9} {phrase}".rstrip()
        self._console.print(
            f"[ap.ok]{OK}[/] {body} [ap.dim]({_fmt_duration(duration_ms)})[/]",
            highlight=False,
        )

    def on_stage_failed(self, stage: str, error: str) -> None:
        self._console.print(
            f"[ap.err]{FAIL}[/] {stage.upper():<9} failed — {error}", highlight=False
        )

    def on_llm_attempt(self, model: str, attempt: int, max_attempts: int) -> None:
        self._console.print(
            f"[ap.dim]· llm attempt {attempt}/{max_attempts} · {model}[/]", highlight=False
        )

    def on_delivery(self, target: str, ok: bool, detail: Optional[str] = None) -> None:
        if ok:
            self._console.print(f"[ap.ok]{OK}[/] delivered → {target}", highlight=False)
        else:
            suffix = f" — {detail}" if detail else ""
            self._console.print(
                f"[ap.warn]{WARN}[/] delivery to {target} failed{suffix}", highlight=False
            )


def resolve_sink(progress: str, stdout_is_tty: bool) -> ProgressSink:
    """Map the --progress flag to a sink (pure; unit-tested).

    auto: plain everywhere for now — T4 upgrades the TTY branch to the live
    footer; non-TTY/CI stays plain per the degradation matrix.
    """
    if progress == "none":
        return NullSink()
    if progress == "plain":
        return PlainSink()
    # auto
    return PlainSink()
