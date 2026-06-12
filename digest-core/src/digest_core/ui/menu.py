"""Interactive launcher menu (TERMINAL_DESIGN.md §5, roadmap follow-up).

`actionpulse` with no subcommand opens this menu on a TTY — Run / Dry-run /
Diagnose / Settings / Show config / Quit — built on the §5.2 arrow-key
selector. Each action calls the same code paths as the corresponding
subcommand; the menu loops until Quit. Non-TTY callers never reach here
(the CLI prints help instead), so scripted use is unaffected.

"Run digest" opens ONE follow-up selector (U3): the daily time-period
decision (today / rolling 24h / yesterday / a date / --force / repeat last)
— smart defaults, no interrogation; Esc backs out without running anything.
The last accepted choice persists to ~/.config/actionpulse/last_run.json.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from digest_core.ui.console import get_console
from digest_core.ui.select import choose
from digest_core.ui.theme import gradient_text

ENV_PATH = Path.home() / ".config" / "actionpulse" / "env"
LAST_RUN_PATH = Path.home() / ".config" / "actionpulse" / "last_run.json"

# Keys whose values must never be shown in full (Show config view).
_SECRET_KEYS = {"EWS_PASSWORD", "LLM_TOKEN", "MM_WEBHOOK_URL"}


def _mask(key: str, value: str) -> str:
    if key not in _SECRET_KEYS or not value:
        return value
    if "/" in value and len(value) > 12:  # webhook URL: keep the host, hide the token path
        head, _, _tail = value.partition("/hooks/")
        return head + "/hooks/••••" if "/hooks/" in value else "••••" + value[-4:]
    return "••••" + value[-4:] if len(value) > 12 else "••••"


def _configured() -> bool:
    return ENV_PATH.exists()


# ---------------------------------------------------------------------------
# Run options (U3): one selector for the daily decision — period + force
# ---------------------------------------------------------------------------


@dataclass
class RunChoice:
    """Parameters the run submenu decides; everything else stays config/CLI."""

    from_date: str = "today"  # "today" or YYYY-MM-DD
    window: str = "calendar_day"  # calendar_day | rolling_24h
    force: bool = False


_WINDOW_WORDS = {"calendar_day": "calendar day", "rolling_24h": "rolling 24h"}


def load_last_run(path: Optional[Path] = None) -> Optional[RunChoice]:
    """Last accepted run params, or None when absent/invalid (defensive)."""
    path = path or LAST_RUN_PATH  # resolved at call time (tests monkeypatch it)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        choice = RunChoice(
            from_date=str(data["from_date"]),
            window=str(data["window"]),
            force=bool(data.get("force", False)),
        )
    except (json.JSONDecodeError, KeyError, OSError, TypeError):
        return None
    if choice.window not in _WINDOW_WORDS:
        return None
    return choice


def save_last_run(choice: RunChoice, path: Optional[Path] = None) -> None:
    """Persist the accepted params (no secrets; best-effort)."""
    path = path or LAST_RUN_PATH  # resolved at call time (tests monkeypatch it)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(choice), indent=2), encoding="utf-8")
    except OSError:
        pass


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _last_run_label(last: RunChoice) -> str:
    # Always the absolute stored date — no silent "yesterday drift".
    bits = [last.from_date, _WINDOW_WORDS.get(last.window, last.window)]
    if last.force:
        bits.append("force")
    return f"Repeat last run ({' · '.join(bits)})"


def _ask_date(console: Console) -> Optional[RunChoice]:
    """Validated YYYY-MM-DD prompt; empty input backs out (returns None)."""
    while True:
        try:
            raw = console.input(
                "[ap.accent.bold]Date (YYYY-MM-DD)[/] [ap.dim](Enter — back)[/]: "
            ).strip()
        except EOFError:
            return None
        if not raw:
            return None
        try:
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            console.print("  [ap.err]✗[/] Expected YYYY-MM-DD, e.g. 2026-06-11")
            continue
        return RunChoice(from_date=raw, window="calendar_day")


def choose_run_options(console: Console, last: Optional[RunChoice] = None) -> Optional[RunChoice]:
    """The U3 run selector: one menu, smart defaults, Esc backs out (never
    runs). Returns the chosen params, or None for "back"."""
    options = [
        ("today", "Today (calendar day)"),
        ("24h", "Today (rolling 24h window)"),
        ("yesterday", f"Yesterday ({_yesterday()})"),
        ("date", "Pick a date…"),
        ("force", "Re-run today (--force, bypass the idempotency skip)"),
    ]
    if last is not None:
        options.append(("last", _last_run_label(last)))
    options.append(("back", "Back"))

    selected = choose(
        "Run digest — time period",
        options,
        default_index=0,
        console=console,
        cancel_value="back",
    )
    if selected == "back":
        return None
    if selected == "today":
        return RunChoice()
    if selected == "24h":
        return RunChoice(window="rolling_24h")
    if selected == "yesterday":
        return RunChoice(from_date=_yesterday())
    if selected == "force":
        return RunChoice(force=True)
    if selected == "last":
        return last
    return _ask_date(console)


def _show_config(console: Console) -> None:
    """Print the env file with secrets masked (read-only)."""
    console.print()
    if not _configured():
        console.print("[ap.warn]⚠[/] Not configured yet — run Settings first.")
        return
    console.print(f"[ap.dim]{ENV_PATH}[/]")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        console.print(f"  [ap.dim]{key.strip()}[/] = {_mask(key.strip(), value.strip())}")


def _banner(console: Console) -> None:
    title = gradient_text("⌁ ActionPulse")
    console.print()
    console.print(title)
    if not _configured():
        console.print("[ap.warn]⚠ Not configured — start with Settings.[/]")
    console.print()


def run_menu(
    *,
    on_run: Callable[[bool, Optional[RunChoice]], None],
    on_diagnose: Callable[[], None],
    on_settings: Callable[[], None],
    on_read: Callable[[Optional[str]], None],
    console: Optional[Console] = None,
) -> int:
    """Drive the launcher menu loop. Callbacks isolate the menu from the CLI
    (and make it testable). Returns the process exit code.

    ``on_run(dry, choice)`` — choice is None for the one-shot dry run (today's
    defaults) and a RunChoice from the U3 selector for a full run.
    ``on_read(date)`` — open the digest reader (None = newest digest).
    """
    out = console or get_console()
    _banner(out)

    options = [
        ("run", "Run digest — pick period, full pipeline + delivery"),
        ("read", "Read digest — topics · authors · quotes"),
        ("dry", "Dry run — ingest only, no LLM"),
        ("diagnose", "Diagnose — check environment & config"),
        ("settings", "Settings — run the setup wizard"),
        ("config", "Show current config (masked)"),
        ("quit", "Quit"),
    ]
    while True:
        try:
            # Esc dismisses the menu (cancel_value): a cancel gesture must
            # never commit the highlighted action (§5.2).
            choice = choose(
                "What would you like to do?",
                options,
                default_index=0,
                console=out,
                cancel_value="quit",
            )
        except KeyboardInterrupt:
            # Abort contract (§5.5): Ctrl+C -> exit 130, no traceback.
            out.print("\n[ap.warn]⚠ Interrupted.[/]")
            return 130
        except EOFError:
            return 0

        if choice == "quit":
            return 0
        try:
            if choice == "run":
                run_choice = choose_run_options(out, last=load_last_run())
                if run_choice is None:
                    continue  # backed out — straight back to the menu
                on_run(False, run_choice)
                # Persist only an accepted, completed-without-crash choice.
                save_last_run(run_choice)
                # U4 bridge: the digest is on disk — offer to read it now.
                follow = choose(
                    "Read the digest now?",
                    [("read", "Read it now"), ("menu", "Back to the menu")],
                    default_index=0,
                    console=out,
                    cancel_value="menu",
                )
                if follow == "read":
                    on_read(run_choice.from_date if run_choice.from_date != "today" else None)
                continue
            elif choice == "read":
                on_read(None)
            elif choice == "dry":
                on_run(True, None)
            elif choice == "diagnose":
                on_diagnose()
            elif choice == "settings":
                on_settings()
            elif choice == "config":
                _show_config(out)
        except KeyboardInterrupt:
            out.print("\n[ap.warn]⚠ Interrupted — back to menu.[/]")
        except Exception as exc:  # noqa: BLE001 - keep the menu alive on action errors
            out.print(f"[ap.err]✗[/] {exc}")

        out.print()
        try:
            # One dim line instead of a degenerate one-option menu (P3:
            # scrollback stays tidy); Enter returns to the menu.
            out.input("[ap.dim]Enter — back to the menu …[/]")
        except KeyboardInterrupt:
            out.print("\n[ap.warn]⚠ Interrupted.[/]")
            return 130
        except EOFError:
            return 0


def load_env_file(path: Path = ENV_PATH) -> int:
    """Load ~/.config/actionpulse/env into os.environ for any keys not already
    set, so `actionpulse run` works without manually sourcing the file. Returns
    the number of keys loaded. Never overrides an explicit env var."""
    if not path.exists():
        return 0
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()
            loaded += 1
    return loaded


def stdin_is_tty() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())
