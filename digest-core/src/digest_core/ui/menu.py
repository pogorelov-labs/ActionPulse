"""Interactive launcher menu (TERMINAL_DESIGN.md §5, roadmap follow-up).

`actionpulse` with no subcommand opens this menu on a TTY — Run / Dry-run /
Diagnose / Settings / Show config / Quit — built on the §5.2 arrow-key
selector. Each action calls the same code paths as the corresponding
subcommand; the menu loops until Quit. Non-TTY callers never reach here
(the CLI prints help instead), so scripted use is unaffected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from digest_core.ui.console import get_console
from digest_core.ui.select import choose
from digest_core.ui.theme import gradient_text

ENV_PATH = Path.home() / ".config" / "actionpulse" / "env"

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
    on_run: Callable[[bool], None],
    on_diagnose: Callable[[], None],
    on_settings: Callable[[], None],
    console: Optional[Console] = None,
) -> int:
    """Drive the launcher menu loop. Callbacks isolate the menu from the CLI
    (and make it testable). Returns the process exit code."""
    out = console or get_console()
    _banner(out)

    options = [
        ("run", "Run digest — full pipeline + delivery"),
        ("dry", "Dry run — ingest only, no LLM"),
        ("diagnose", "Diagnose — check environment & config"),
        ("settings", "Settings — run the setup wizard"),
        ("config", "Show current config (masked)"),
        ("quit", "Quit"),
    ]
    while True:
        try:
            choice = choose("What would you like to do?", options, default_index=0, console=out)
        except (KeyboardInterrupt, EOFError):
            out.print("\n[ap.dim]Bye.[/]")
            return 0

        if choice == "quit":
            return 0
        try:
            if choice == "run":
                on_run(False)
            elif choice == "dry":
                on_run(True)
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
            choose("—", [("back", "Back to menu")], default_index=0, console=out)
        except (KeyboardInterrupt, EOFError):
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
