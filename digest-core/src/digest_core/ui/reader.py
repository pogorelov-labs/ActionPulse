"""Interactive digest reader (TERMINAL_DESIGN.md §5.1, roadmap U4).

Line-oriented drill-down on the §5.2 selector — digest → section → item →
a detail card *printed into scrollback*: every card the user opens stays
behind as copyable evidence (scrollback-as-evidence is a product principle,
which is why the alt-screen browser lost the U4 posture comparison). Esc
walks back up one level; Ctrl+C aborts with the §5.5 contract.

Reader chrome is English (terminal surface); digest content renders exactly
as stored in the artifact (`report.language` decides that at build time).
Works for any ``out/digest-*.json`` — today's run or history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from digest_core.assemble.labels import confidence_text
from digest_core.llm.schemas import Digest, Item
from digest_core.ui.console import get_console
from digest_core.ui.glyphs import WARN
from digest_core.ui.select import choose

#: A selector page: 6 entries + at most "More…" + "Previous…" + back = 9,
#: holding the §5.2 quick-select invariant (menus are 1..9 options) even on
#: middle pages where both nav rows appear.
PAGE_SIZE = 6

_TITLE_MAX = 56  # item titles end-ellipse in lists (§6.2); full title on the card


def list_digests(out_dir: Path) -> List[Path]:
    """All digest artifacts in ``out_dir``, newest first (by the file's date)."""
    return sorted(out_dir.glob("digest-????-??-??.json"), key=lambda p: p.name, reverse=True)


def load_digest(path: Path) -> Digest:
    """Parse a digest artifact; older artifacts (no enrichment fields) load fine."""
    return Digest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _ellipsis(text: str, width: int = _TITLE_MAX) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _item_list_label(item: Item) -> str:
    """One selector row per item: title + due + honesty badges."""
    bits = [_ellipsis(item.title)]
    if item.due:
        bits.append(f"due {item.due}")
    if getattr(item, "weak_evidence", None):
        bits.append(f"{WARN} weak")
    if getattr(item, "seen_before", None):
        bits.append("seen before")
    return " · ".join(bits)


def item_card(item: Item, section_title: str) -> Panel:
    """The scrollback detail card: distilled content + provenance (P2)."""
    body = Text()
    body.append(item.title, style="ap.em")
    body.append("\n")

    def row(label: str, value: str, style: str = "default") -> None:
        body.append(f"\n{label:<12}", style="ap.dim")
        body.append(value, style=style)

    row("Section", section_title)
    if item.due:
        row("Due", item.due)
    row("Confidence", f"{item.confidence:.2f} ({confidence_text(item.confidence, 'en')})")
    if item.source_from:
        row("From", item.source_from)
    if item.source_subject:
        row("Subject", item.source_subject)
    if getattr(item, "weak_evidence", None):
        row("Evidence", f"{WARN} weak — not offset-verified", style="ap.warn")
    if getattr(item, "seen_before", None):
        row("Dedup", "evidence already backed a delivered item", style="ap.dim")

    quotes = list(getattr(item, "evidence_spans", None) or [])
    for span in quotes[:3]:
        body.append("\n\n")
        body.append(f"“{_ellipsis(span.quote, 200)}”", style="italic")
        body.append(f"\n  — msg {span.msg_id}", style="ap.dim")
    if len(quotes) > 3:
        body.append(f"\n[+{len(quotes) - 3} more quotes]", style="ap.dim")

    msg_id = (item.source_ref or {}).get("msg_id", "")
    trace = f"evidence {item.evidence_id}" + (f" · msg {msg_id}" if msg_id else "")
    body.append(f"\n\n{trace}", style="ap.dim")

    return Panel(body, box=box.ROUNDED, border_style="ap.rule", expand=False, padding=(0, 1))


def _paged_choose(
    console: Console,
    label: str,
    entries: Sequence[Tuple[str, str]],
    back_label: str = "Back",
) -> Optional[str]:
    """choose() over arbitrarily many entries, paged at PAGE_SIZE (§5.2 holds:
    ≤9 visible options). Returns the chosen value, or None for back/Esc."""
    page = 0
    while True:
        start = page * PAGE_SIZE
        chunk = list(entries[start : start + PAGE_SIZE])
        options: List[Tuple[str, str]] = list(chunk)
        if start + PAGE_SIZE < len(entries):
            options.append(("__next__", f"More… ({len(entries) - start - PAGE_SIZE} more)"))
        if page > 0:
            options.append(("__prev__", "Previous…"))
        options.append(("__back__", back_label))
        selected = choose(label, options, default_index=0, console=console, cancel_value="__back__")
        if selected == "__next__":
            page += 1
        elif selected == "__prev__":
            page -= 1
        elif selected == "__back__":
            return None
        else:
            return selected


def _browse_section(console: Console, digest: Digest, section_index: int) -> None:
    section = digest.sections[section_index]
    entries = [(str(i), _item_list_label(item)) for i, item in enumerate(section.items)]
    while True:
        value = _paged_choose(console, f"{section.title} — items", entries)
        if value is None:
            return
        item = section.items[int(value)]
        console.print()
        console.print(item_card(item, section.title))
        console.print()


def _browse_digest(console: Console, digest: Digest, path: Path) -> None:
    total = sum(len(section.items) for section in digest.sections)
    console.print()
    console.print(
        f"[ap.em]Digest {digest.digest_date}[/] [ap.dim]· {len(digest.sections)} sections"
        f" · {total} items · {path}[/]",
        highlight=False,
    )
    if not digest.sections:
        console.print("[ap.dim]No items for this day.[/]")
        return
    while True:
        entries = [
            (str(i), f"{section.title} ({len(section.items)})")
            for i, section in enumerate(digest.sections)
        ]
        value = _paged_choose(console, "Sections", entries)
        if value is None:
            return
        _browse_section(console, digest, int(value))


def read_digest_interactive(
    out_dir: Path, date: Optional[str] = None, console: Optional[Console] = None
) -> int:
    """Entry point for `actionpulse read` / the menu item. Returns exit code."""
    out = console or get_console()
    digests = list_digests(out_dir)
    if date:
        target = out_dir / f"digest-{date}.json"
        if not target.exists():
            out.print(
                f"[ap.err]✗[/] No digest for {date} in {out_dir} — run `actionpulse run` first."
            )
            return 1
        _browse_digest(out, load_digest(target), target)
        return 0
    if not digests:
        out.print(f"[ap.err]✗[/] No digests found in {out_dir} — run `actionpulse run` first.")
        return 1
    if len(digests) == 1:
        _browse_digest(out, load_digest(digests[0]), digests[0])
        return 0
    while True:
        entries = [(str(path), path.stem.replace("digest-", "")) for path in digests]
        value = _paged_choose(out, "Read a digest — pick a day", entries, back_label="Quit")
        if value is None:
            return 0
        target = Path(value)
        _browse_digest(out, load_digest(target), target)


def render_digest_plain(out_dir: Path, date: Optional[str] = None) -> Optional[str]:
    """Non-TTY degradation: the markdown artifact (append-only, scriptable).
    Returns the text, or None when nothing matches."""
    if date:
        md_path = out_dir / f"digest-{date}.md"
        return md_path.read_text(encoding="utf-8") if md_path.exists() else None
    digests = list_digests(out_dir)
    if not digests:
        return None
    md_path = digests[0].with_suffix(".md")
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")
    # JSON exists but the .md sibling does not — render a minimal text view.
    digest = load_digest(digests[0])
    lines = [f"Digest {digest.digest_date}"]
    for section in digest.sections:
        lines.append(f"\n## {section.title}")
        for item in section.items:
            lines.append(f"- {item.title}" + (f" (due {item.due})" if item.due else ""))
    return "\n".join(lines) + "\n"
