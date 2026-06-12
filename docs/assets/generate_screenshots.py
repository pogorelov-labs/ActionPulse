"""Generate terminal-styled SVG screenshots for the README.

Renders the *real* ActionPulse UI components (the progress line builders, the
gradient banner, the menu markup) into recording rich Consoles and exports
each as an SVG with terminal window chrome — the same technique the rich /
textual docs use, so the screenshots are authentic, not mockups.

Run:  cd digest-core && uv run python ../docs/assets/generate_screenshots.py
Regenerate whenever the UI surfaces change.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich import box

from digest_core.ui.sinks import _ok_line
from digest_core.ui.theme import THEME, gradient_text

ASSETS = Path(__file__).resolve().parent
WIDTH = 74


def _console() -> Console:
    import io

    return Console(record=True, width=WIDTH, theme=THEME, file=io.StringIO())


def _save(console: Console, name: str, title: str) -> None:
    svg = console.export_svg(title=title, font_aspect_ratio=0.61)
    (ASSETS / name).write_text(svg, encoding="utf-8")
    print(f"  wrote {name}")


def screenshot_run_progress() -> None:
    """The live run display: permanent funnel history + the animated footer.

    The ingest line carries the U2 health suffix (retries render only when
    nonzero); the footer shows intra-stage data progress + the LLM note.
    """
    c = _console()
    c.print(gradient_text("⌁ ActionPulse"))
    c.print()
    c.print(
        _ok_line("ingest", {"messages": 124, "retries": 1}, 14_300), highlight=False
    )
    c.print(_ok_line("normalize", {"messages": 124}, 410), highlight=False)
    c.print(_ok_line("threads", {"messages": 124, "threads": 37}, 230), highlight=False)
    c.print(_ok_line("evidence", {"threads": 37, "chunks": 41}, 820), highlight=False)
    c.print(_ok_line("select", {"selected": 28, "of": 41}, 110), highlight=False)
    # The live footer, mid-stage (spinner first frame + warming elapsed + note
    # + the §4.3 model lane with the broker's trailing-60s RPM).
    c.print("[ap.warn]⠹[/] [ap.em]LLM      [/] [ap.warn]12.4s[/]", highlight=False)
    c.print("  [ap.dim]└ attempt 1/2 · qwen35-397b-a17b[/]", highlight=False)
    c.print(
        "  └ qwen35-397b-a17b   extractor · 1 in-flight · 1 call · RPM 3/15",
        highlight=False,
    )
    _save(c, "run-progress.svg", "actionpulse run")


def screenshot_menu() -> None:
    """The bare `actionpulse` launcher menu (§5.2 selector)."""
    c = _console()
    c.print(gradient_text("⌁ ActionPulse"))
    c.print()
    c.print(
        "[ap.accent.bold]What would you like to do?[/] "
        "[ap.dim](↑↓/jk · Enter · Esc = cancel)[/]",
        highlight=False,
    )
    options = [
        "Run digest — pick period, full pipeline + delivery",
        "Read digest — topics · authors · quotes",
        "Dry run — ingest only, no LLM",
        "Diagnose — check environment & config",
        "Maintenance — disk usage · cleanup · logging",
        "Settings — run the setup wizard",
        "Show current config (masked)",
        "Quit",
    ]
    for i, text in enumerate(options):
        if i == 0:
            c.print(f" [ap.accent]❯[/] [ap.em]{i + 1}. {text}[/]", highlight=False)
        else:
            c.print(f"   [ap.dim]{i + 1}. {text}[/]", highlight=False)
    _save(c, "menu.svg", "actionpulse")


def screenshot_run_options() -> None:
    """The U3 run selector: one menu for the daily decision."""
    c = _console()
    c.print(
        "[ap.accent.bold]Run digest — time period[/] "
        "[ap.dim](↑↓/jk · Enter · Esc = cancel)[/]",
        highlight=False,
    )
    options = [
        "Today (calendar day)",
        "Today (rolling 24h window)",
        "Yesterday (2026-06-11)",
        "Pick a date…",
        "Re-run today (--force, bypass the idempotency skip)",
        "Repeat last run (2026-06-10 · rolling 24h)",
        "Back",
    ]
    for i, text in enumerate(options):
        if i == 0:
            c.print(f" [ap.accent]❯[/] [ap.em]{i + 1}. {text}[/]", highlight=False)
        else:
            c.print(f"   [ap.dim]{i + 1}. {text}[/]", highlight=False)
    _save(c, "run-options.svg", "actionpulse · run")


def screenshot_setup() -> None:
    """The setup wizard: banner + a step + the review panel."""
    c = _console()
    title = gradient_text("⌁ ActionPulse")
    title.append(" · setup", style="bold default")
    c.print(
        Panel(
            Text.assemble(
                title, "\n", ("7 questions · secrets hidden · safe to re-run", "dim")
            ),
            box=box.ROUNDED,
            border_style="ap.accent",
            padding=(0, 2),
            expand=False,
        )
    )
    c.print(
        "[ap.ok]✓[/] EWS login [bold]ruapgr2[/] [dim](NTLM · machine login)[/] "
        "· domain [bold]megacorp.ru[/]",
        highlight=False,
    )
    c.print(
        "[ap.ok]✓[/] email (UPN) [bold]Ruslan.POGORELOV@megacorp.ru[/]", highlight=False
    )
    c.print()
    rows = [
        ("Email (UPN)", "Ruslan.POGORELOV@megacorp.ru"),
        ("EWS login (NTLM)", "ruapgr2"),
        ("EWS endpoint", "https://owa.megacorp.ru/EWS/Exchange.asmx"),
        ("EWS password", "••••"),
        ("LLM token", "••••a1b2"),
        ("Report language", "en"),
    ]
    body = Group(*[Text.assemble((f"{k:<17}", "dim"), (v, "default")) for k, v in rows])
    c.print(
        Panel(
            body,
            title="[bold]Review the values[/]",
            box=box.ROUNDED,
            border_style="ap.accent",
            expand=False,
        )
    )
    _save(c, "setup.svg", "actionpulse setup")


def screenshot_reader_card() -> None:
    """A reader detail card — built by the REAL item_card renderer (U4)."""
    from digest_core.llm.schemas import Item
    from digest_core.ui.reader import item_card

    item = Item(
        title="Prepare the project status update for the steering committee",
        due="2026-06-13",
        evidence_id="ev-7f3a91c2",
        confidence=0.86,
        source_ref={"type": "email", "msg_id": "caf-1129@megacorp.ru"},
        email_subject="RE: Project status — steering committee",
        email_from="Ivan Petrov <ivan.petrov@megacorp.ru>",
        evidence_spans=[
            {
                "msg_id": "caf-1129@megacorp.ru",
                "quote": "Пожалуйста, подготовь обновление статуса проекта к четвергу",
            }
        ],
    )
    c = _console()
    c.print(item_card(item, "My actions"))
    _save(c, "reader-card.svg", "actionpulse read")


def main() -> None:
    print("Generating README screenshots…")
    screenshot_run_progress()
    screenshot_menu()
    screenshot_run_options()
    screenshot_reader_card()
    screenshot_setup()
    print("Done.")


if __name__ == "__main__":
    main()
