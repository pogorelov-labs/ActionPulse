"""Interactive launcher menu (TERMINAL_DESIGN.md §5, roadmap follow-up).

`actionpulse` with no subcommand opens this menu on a TTY — Run / Read /
History / Diagnose / Settings & tools / Quit — built on the §5.2
arrow-key selector. To stay within the 1–9 quick-select invariant the
lower-frequency actions (setup wizard, Mattermost DMs, MCP, message store
[when on], maintenance, show-config) live under the "Settings & tools"
submenu. Each action calls the same code paths as the corresponding
subcommand; the menu loops until Quit. Non-TTY callers never reach here (the
CLI prints help instead), so scripted use is unaffected.

"Run digest" opens ONE follow-up selector (U3): the daily time-period decision
(today / rolling 24h / yesterday / a date / preview / re-run / repeat last)
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

from digest_core import paths
from digest_core.config import PROJECT_ROOT
from digest_core.dm_consent import (
    DM_SCOPE_LABELS,
    DM_SCOPES,
    dm_consent_is_stale,
    normalize_partners,
    now_iso,
    update_mm_source_dm,
)
from digest_core.ui.console import get_console
from digest_core.ui.select import choose
from digest_core.ui.theme import gradient_text

ENV_PATH = Path.home() / ".config" / "actionpulse" / "env"
# Same config.yaml the setup wizard writes (configs/config.yaml). The DM-scope
# screen does an in-place read-modify-write on the mm_source.dm_* keys.
CONFIG_USER_PATH = PROJECT_ROOT / "configs" / "config.yaml"
# U5: run state lives in the data home; the pre-U5 location stays readable so
# an upgrade does not forget the user's "Repeat last run" params.
LAST_RUN_PATH = paths.state_dir(create=False) / "last_run.json"
LEGACY_LAST_RUN_PATH = Path.home() / ".config" / "actionpulse" / "last_run.json"

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
    dry: bool = False  # "Preview" — ingest+normalize only, no LLM/assemble/deliver


_WINDOW_WORDS = {"calendar_day": "calendar day", "rolling_24h": "rolling 24h"}


def load_last_run(path: Optional[Path] = None) -> Optional[RunChoice]:
    """Last accepted run params, or None when absent/invalid (defensive)."""
    path = path or LAST_RUN_PATH  # resolved at call time (tests monkeypatch it)
    if not path.exists() and path == LAST_RUN_PATH and LEGACY_LAST_RUN_PATH.exists():
        path = LEGACY_LAST_RUN_PATH  # pre-U5 location (read-only migration)
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
        ("today", "Today — fetch & deliver"),
        ("24h", "Today (rolling 24h) — fetch & deliver"),
        ("yesterday", f"Yesterday ({_yesterday()})"),
        ("date", "Pick a date…"),
        ("preview", "Preview today — fetch only, nothing delivered"),
        ("force", "Re-run today — rebuild even if nothing changed"),
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
    if selected == "preview":
        return RunChoice(dry=True)
    if selected == "force":
        return RunChoice(force=True)
    if selected == "last":
        return last
    return _ask_date(console)


def _show_config(console: Console) -> None:
    """Print the env file (secrets masked) + the full path map (read-only)."""
    console.print()
    if not _configured():
        console.print("[ap.warn]⚠[/] Not configured yet — run Settings first.")
    else:
        console.print(f"[ap.dim]{ENV_PATH}[/]")
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            console.print(f"  [ap.dim]{key.strip()}[/] = {_mask(key.strip(), value.strip())}")
    # U5: "where is everything" answered in one place (same map as
    # `actionpulse paths`); everything regenerable lives under the data home.
    console.print()
    console.print("[ap.dim]Paths:[/]")
    for key, value in paths.describe().items():
        console.print(f"  [ap.dim]{paths.LABELS.get(key, key):<12}[/] {value}")


def _maintenance(console: Console) -> None:
    """Maintenance screen (U6): usage read-out, cleanup, logging toggle.

    Cleaning only touches regenerable files (digests, logs — incl. the legacy
    log dirs); state/config/secrets are never auto-cleaned. Telemetry honesty:
    nothing phones home — logs and metrics ports are local, OTel is off by
    default — so the screen states facts instead of faking a kill switch.
    """
    from digest_core import maintenance

    while True:
        console.print()
        for entry in maintenance.collect_usage():
            console.print(
                f"  [ap.dim]{entry.label:<26}[/] {entry.files:>5} files"
                f"  {maintenance.format_bytes(entry.size_bytes):>10}  [ap.dim]{entry.path}[/]",
                highlight=False,
            )
        logging_on = maintenance.file_logging_enabled()
        console.print(
            "  [ap.dim]Local-only by design: no phone-home telemetry;"
            " OTel tracing off unless enabled in config.[/]"
        )
        console.print()
        action = choose(
            "Maintenance",
            [
                ("logs", "Clean logs (incl. legacy log dirs)"),
                ("old", f"Clean digests older than {maintenance.DEFAULT_KEEP_DAYS} days"),
                ("all", "Clean ALL digests + logs…"),
                (
                    "logging",
                    f"File logging: {'on' if logging_on else 'off'} — turn"
                    f" {'off' if logging_on else 'on'}",
                ),
                ("back", "Back"),
            ],
            default_index=4,
            console=console,
            cancel_value="back",
        )
        if action == "back":
            return
        if action == "logging":
            new_state = maintenance.set_file_logging(not logging_on)
            console.print(
                f"  [ap.ok]✓[/] File logging is now {'on' if new_state else 'off'}"
                " [ap.dim](observability.log_to_file in configs/config.yaml)[/]"
            )
            continue
        if action == "all":
            confirm = choose(
                "Delete ALL digests and logs?",
                [("no", "No, keep everything"), ("yes", "Yes, delete")],
                default_index=0,
                console=console,
                cancel_value="no",
            )
            if confirm != "yes":
                continue
            removed = freed = 0
            for n, b in (maintenance.clean_logs(), maintenance.clean_digests(None)):
                removed, freed = removed + n, freed + b
        elif action == "logs":
            removed, freed = maintenance.clean_logs()
        else:  # old
            removed, freed = maintenance.clean_digests(maintenance.DEFAULT_KEEP_DAYS)
        console.print(
            f"  [ap.ok]✓[/] Removed {removed} files, freed {maintenance.format_bytes(freed)}"
        )


# ---------------------------------------------------------------------------
# Mattermost DMs (ingest) — scope · partners (first in-menu config editor)
# ---------------------------------------------------------------------------

_DM_SCOPE_LABEL = {
    "off": "Off",
    "own_posts_only": "My posts only",
    "selected": "Selected",
    "all": "All DMs",
}


def _read_dm_state(path: Path = CONFIG_USER_PATH) -> tuple[str, list[str], bool, Optional[str]]:
    """Read (scope, allowlist, acknowledged, acknowledged_at) from config.yaml.

    Defensive: a missing/empty/garbage file or an unknown scope reads as the
    HARD-OFF default. Never raises (the menu must stay alive).
    """
    import yaml

    scope, allowlist, ack, ack_at = "off", [], False, None
    if path.exists():
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            mm = doc.get("mm_source", {}) or {}
            raw_scope = mm.get("dm_scope", "off")
            scope = raw_scope if raw_scope in DM_SCOPES else "off"
            allowlist = normalize_partners(mm.get("dm_allowlist", []) or [])
            ack = bool(mm.get("dm_consent_acknowledged", False))
            ack_at = mm.get("dm_consent_acknowledged_at")
            if ack_at is not None and not isinstance(ack_at, str):
                ack_at = str(ack_at)
        except Exception:  # noqa: BLE001 — a broken config must not crash the menu
            return "off", [], False, None
    return scope, allowlist, ack, ack_at


def _dm_consent_status(scope: str, ack: bool, ack_at: Optional[str]) -> str:
    """Header consent string; renders defensively (never crashes on a bad date).

    'off'/'own_posts_only' need no consent → '—'. A consent scope shows the ack
    date when parseable, '(unknown)' when missing/garbage, and flags staleness.
    """
    if scope not in ("selected", "all"):
        return "consent —"
    if not ack:
        return "[ap.warn]consent ✗ (required)[/]"
    date_str = "(unknown)"
    if isinstance(ack_at, str) and ack_at.strip():
        # Show just the calendar day; tolerate any ISO-ish prefix.
        date_str = ack_at.strip()[:10]
    stale = dm_consent_is_stale(ack_at, datetime.now(timezone.utc))
    flag = " [ap.warn](stale — re-affirm)[/]" if stale else ""
    return f"consent ✓ {date_str}{flag}"


def _dm_header(
    console: Console, scope: str, allowlist: list[str], ack: bool, ack_at: Optional[str]
):
    label = _DM_SCOPE_LABEL.get(scope, scope)
    if scope == "selected":
        label = f"{label} ({len(allowlist)} partner{'s' if len(allowlist) != 1 else ''})"
    console.print()
    console.print(
        f"  [ap.dim]Scope:[/] [ap.em]{label}[/]  [ap.dim]·[/]  "
        f"{_dm_consent_status(scope, ack, ack_at)}"
    )


def _dm_change_scope(
    console: Console, current_scope: str, allowlist: list[str], ack: bool, ack_at: Optional[str]
) -> None:
    """Run the ladder picker and persist a (loadable) scope change.

    off/own_posts_only save immediately (consent cleared on a downgrade to a
    no-PII scope). selected/all record the consent ack + a local timestamp —
    selecting the scope IS the consent; no panel, no extra gate. The write helper
    refuses to persist a consent scope without its ack, so this can never leave
    an unloadable config.
    """
    from rich.prompt import Prompt

    if sys.stdin.isatty():
        new_scope = choose(
            "DM ingest scope",
            list(DM_SCOPE_LABELS),
            default_index=DM_SCOPES.index(current_scope),
            console=console,
            cancel_value=current_scope,
        )
    else:
        new_scope = Prompt.ask(
            "[ap.accent.bold]DM ingest scope[/]",
            choices=list(DM_SCOPES),
            default=current_scope,
            console=console,
        )

    # No-PII scopes: persist immediately, clearing consent on the way down.
    if new_scope in ("off", "own_posts_only"):
        update_mm_source_dm(
            CONFIG_USER_PATH,
            dm_scope=new_scope,
            dm_consent_acknowledged=False,
            dm_consent_acknowledged_at=None,
        )
        console.print(f"  [ap.ok]✓[/] DM scope: [bold]{_DM_SCOPE_LABEL[new_scope]}[/]")
        return

    # selected/all — record consent on selection (no panel, no extra confirm).
    update_mm_source_dm(
        CONFIG_USER_PATH,
        dm_scope=new_scope,
        dm_consent_acknowledged=True,
        dm_consent_acknowledged_at=now_iso(),
    )
    if new_scope == "selected" and not allowlist:
        console.print(
            "  [ap.ok]✓[/] DM scope: [bold]Selected[/] "
            "[ap.warn](0 partners → effectively OFF; add partners next)[/]"
        )
    else:
        console.print(f"  [ap.ok]✓[/] DM scope: [bold]{_DM_SCOPE_LABEL[new_scope]}[/]")


def _dm_edit_partners(console: Console, allowlist: list[str]) -> None:
    """Add/remove sub-loop for the 'selected' allowlist. Never fires consent."""
    from rich.prompt import Prompt

    partners = list(allowlist)
    while True:
        current = ", ".join(partners) if partners else "[ap.dim](empty → effectively OFF)[/]"
        console.print()
        console.print(f"  [ap.dim]Partners:[/] {current}")
        action = choose(
            "Edit DM partners",
            [
                ("add", "Add a partner (@username · email · user_id)"),
                ("remove", "Remove a partner"),
                ("back", "Back"),
            ],
            default_index=2,
            console=console,
            cancel_value="back",
        )
        if action == "back":
            return
        if action == "add":
            raw = Prompt.ask("[ap.accent.bold]  Partner identity[/]", default="", console=console)
            added = normalize_partners(raw)
            partners.extend(added)
            update_mm_source_dm(CONFIG_USER_PATH, dm_allowlist=partners)
            if added:
                console.print(f"  [ap.ok]✓[/] Added {len(added)} — {len(partners)} total")
        else:  # remove
            if not partners:
                console.print("  [ap.dim]Nothing to remove.[/]")
                continue
            # Paged: a long allowlist must not exceed the 1–9 quick-select cap
            # (§5.2) — reuse the reader's paging helper (as cli.py does).
            from digest_core.ui.reader import _paged_choose

            picked = _paged_choose(
                console, "Remove which partner?", [(p, p) for p in partners], back_label="Cancel"
            )
            if picked is None:
                continue
            partners = [p for p in partners if p != picked]
            update_mm_source_dm(CONFIG_USER_PATH, dm_allowlist=partners)
            console.print(f"  [ap.ok]✓[/] Removed [bold]{picked}[/] — {len(partners)} total")


def _mm_dm_menu(console: Console) -> None:
    """Mattermost DMs (ingest) screen: change scope · edit partners.

    The easy on/off (and partner edits) without re-running the full wizard.
    Writes the same configs/config.yaml the wizard writes, touching only the
    mm_source.dm_* keys (read-modify-write). The write helper refuses to persist
    a consent scope without its ack, so this screen can never leave an
    unloadable config.
    """
    while True:
        scope, allowlist, ack, ack_at = _read_dm_state()
        _dm_header(console, scope, allowlist, ack, ack_at)

        partner_label = (
            f"Edit partners ({len(allowlist)})"
            if scope == "selected"
            else "Edit partners — (only under 'Selected')"
        )
        action = choose(
            "Mattermost DMs",
            [
                ("scope", "Change scope (off · my posts · selected · all)"),
                ("partners", partner_label),
                ("back", "Back"),
            ],
            default_index=2,
            console=console,
            cancel_value="back",
        )
        if action == "back":
            return
        try:
            if action == "scope":
                _dm_change_scope(console, scope, allowlist, ack, ack_at)
            elif action == "partners":
                if scope != "selected":
                    console.print(
                        "  [ap.dim]Partners apply only under the 'Selected' scope —"
                        " change scope to 'Selected' first.[/]"
                    )
                    continue
                _dm_edit_partners(console, allowlist)
        except Exception as exc:  # noqa: BLE001 — keep the screen alive on write errors
            console.print(f"  [ap.err]✗[/] {exc}")


def _banner(console: Console) -> None:
    title = gradient_text("⌁ ActionPulse")
    console.print()
    console.print(title)
    if not _configured():
        console.print("[ap.warn]⚠ Not configured — start with Settings.[/]")
    console.print()


def _mcp_menu(console: Console) -> None:
    """Register the MCP server into AI coding CLIs (Claude Code / opencode / qwen-code)."""
    from digest_core.mcp.commands import offer_install

    console.print()
    offer_install(console)


def _open_store_for_menu(console: Console):
    """Open the message store for an in-menu action, or print why-not and return
    None — never exits (the menu must stay alive). Mirrors `cli._open_store_or_exit`
    without the `typer.Exit`."""
    from digest_core.config import Config
    from digest_core.store import HAS_SQLCIPHER, INSTALL_HINT, MessageStore, StoreError

    cfg = Config().store
    if not HAS_SQLCIPHER:
        console.print(f"  [ap.warn]⚠[/] {INSTALL_HINT}")
        return None
    if not cfg.enabled:
        console.print(
            "  [ap.warn]⚠[/] Store is off — enable it via Settings → Run the setup wizard."
        )
        return None
    try:
        return MessageStore.open(cfg)
    except (StoreError, ValueError) as exc:
        console.print(f"  [ap.err]✗[/] {exc}")
        return None


def _store_status(console: Console) -> None:
    """Read-only store stats (messages by source, chunks/embeddings, window, size)."""
    from digest_core import maintenance

    store = _open_store_for_menu(console)
    if store is None:
        return
    try:
        st = store.stats()
        db_path = Path(store.config.resolved_db_path())
        size = db_path.stat().st_size if db_path.exists() else 0
    finally:
        store.close()
    by_source = ", ".join(f"{k}={v}" for k, v in st["by_source"].items()) or "—"
    console.print(f"  [ap.dim]messages[/]   {st['messages']}  ({by_source})")
    console.print(f"  [ap.dim]chunks[/]     {st['chunks']}")
    console.print(f"  [ap.dim]embeddings[/] {st['embeddings']}")
    console.print(f"  [ap.dim]window[/]     {st['oldest'] or '—'} … {st['newest'] or '—'}")
    console.print(f"  [ap.dim]db size[/]    {maintenance.format_bytes(size)}  [ap.dim]{db_path}[/]")


def _store_reembed(console: Console) -> None:
    """Fill the embedding backlog (needs the corp gateway; degrades with a message)."""
    from digest_core.api import InboxAPI
    from digest_core.config import Config

    store = _open_store_for_menu(console)
    if store is None:
        return
    api = InboxAPI(store, Config())  # owns the embeddings wiring; api.close() closes the store
    try:
        with console.status("[ap.accent]Embedding the backlog (corp network)…"):
            result = api.reembed(force=False)
        console.print(
            f"  [ap.ok]✓[/] Embedded {result['embedded']} chunk(s);"
            f" {result['pending']} still pending."
        )
    except Exception as exc:  # noqa: BLE001 — the gateway is corp-only; report, don't crash
        console.print(f"  [ap.err]✗[/] Re-embed failed: {exc}")
    finally:
        api.close()


def _store_purge(console: Console) -> None:
    """Apply the retention TTL now (default-No confirm), then vacuum."""
    from rich.prompt import Confirm

    store = _open_store_for_menu(console)
    if store is None:
        return
    try:
        days = store.config.ttl_days
        if Confirm.ask(
            f"[ap.accent.bold]Delete store messages older than {days} days?[/]", default=False
        ):
            deleted = store.sweep_ttl(days)
            store.vacuum()
            console.print(f"  [ap.ok]✓[/] Purged {deleted} message(s) older than {days} days.")
        else:
            console.print("  [ap.dim]Cancelled.[/]")
    finally:
        store.close()


def _store_menu(console: Console) -> None:
    """Message store submenu: status · re-embed (corp) · purge expired. Shown only
    when the store is enabled; each action opens the store fresh and
    degrades-not-crashes if the driver/key is missing."""
    while True:
        action = choose(
            "Message store",
            [
                ("status", "Status — messages · chunks · embeddings · size"),
                ("reembed", "Re-embed — fill the embedding backlog (corp network)"),
                ("purge", "Purge — delete messages past the retention window"),
                ("back", "Back"),
            ],
            default_index=3,
            console=console,
            cancel_value="back",
        )
        if action == "back":
            return
        try:
            if action == "status":
                _store_status(console)
            elif action == "reembed":
                _store_reembed(console)
            elif action == "purge":
                _store_purge(console)
        except KeyboardInterrupt:
            console.print("\n[ap.warn]⚠ Interrupted — back to the store menu.[/]")
        except Exception as exc:  # noqa: BLE001 - keep the submenu alive on errors
            console.print(f"  [ap.err]✗[/] {exc}")
        console.print()
        try:
            console.input("[ap.dim]Enter — back to the store menu …[/]")
        except (EOFError, KeyboardInterrupt):
            return


def _settings_menu(
    console: Console, on_settings: Callable[[], None], store_enabled: bool = False
) -> None:
    """Settings & tools submenu — keeps the main menu inside the 1–9 quick-select
    invariant (§5.2). Hosts the lower-frequency actions: run the setup wizard ·
    edit Mattermost DM scope/partners · register the MCP server · (when the store
    is on) manage the message store · maintenance · show config. Each row reuses
    the existing screen/callback."""
    while True:
        rows = [
            ("setup", "Run the setup wizard"),
            ("mm_dm", "Mattermost DMs — choose whose to include"),
            ("mcp", "MCP server — register into AI coding CLIs"),
            ("maintenance", "Maintenance — disk usage · cleanup · logging"),
        ]
        if store_enabled:
            rows.append(("store", "Message store — status · re-embed · purge"))
        rows += [("config", "Show current config (masked)"), ("back", "Back")]
        action = choose(
            "Settings & tools",
            rows,
            default_index=len(rows) - 1,
            console=console,
            cancel_value="back",
        )
        if action == "back":
            return
        try:
            if action == "mm_dm":
                _mm_dm_menu(console)
                continue  # has its own loop — no pause
            if action == "maintenance":
                _maintenance(console)
                continue  # has its own loop — no pause
            if action == "store":
                _store_menu(console)
                continue  # has its own loop — no pause
            if action == "setup":
                on_settings()
            elif action == "mcp":
                _mcp_menu(console)
            elif action == "config":
                _show_config(console)
        except KeyboardInterrupt:
            console.print("\n[ap.warn]⚠ Interrupted — back to settings.[/]")
        except Exception as exc:  # noqa: BLE001 - keep the submenu alive on errors
            console.print(f"[ap.err]✗[/] {exc}")
        # setup/mcp/config print and return at once — pause so the result is
        # readable before the submenu re-renders.
        console.print()
        try:
            console.input("[ap.dim]Enter — back to settings …[/]")
        except (EOFError, KeyboardInterrupt):
            return


def _main_menu_options(store_enabled: bool) -> list[tuple[str, str]]:
    """The launcher rows — kept within the 1–9 quick-select invariant (§5.2): the
    primary actions stay top-level; setup / Mattermost DMs / MCP / maintenance /
    show-config live under the "Settings & tools" submenu (`_settings_menu`).
    Search/Ask appear ONLY when the encrypted store is enabled — they are
    meaningless without it, and a dead row would mislead."""
    options = [
        ("run", "Run digest — choose a day, then fetch & deliver"),
        ("read", "Read digest — browse the latest, with source quotes"),
        ("history", "History — search what was delivered before"),
    ]
    if store_enabled:
        options += [
            ("search", "Search messages — find any email or chat"),
            ("ask", "Ask your inbox — a cited answer from your messages"),
        ]
    options += [
        ("diagnose", "Diagnose — check your setup & connections"),
        ("settings", "Settings & tools — setup · DMs · MCP · cleanup"),
        ("quit", "Quit"),
    ]
    return options


def _prompt_query(console: Console, label: str) -> Optional[str]:
    """One-line query prompt for the search/ask rows; empty/EOF/Ctrl+C backs out."""
    try:
        raw = console.input(f"[ap.accent.bold]{label}[/] [ap.dim](Enter — back)[/]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return raw or None


def run_menu(
    *,
    on_run: Callable[[bool, Optional[RunChoice]], None],
    on_diagnose: Callable[[], None],
    on_settings: Callable[[], None],
    on_read: Callable[[Optional[str]], None],
    on_explain: Optional[Callable[[], None]] = None,
    on_search: Optional[Callable[[str], None]] = None,
    on_ask: Optional[Callable[[str], None]] = None,
    on_history: Optional[Callable[[str], None]] = None,
    store_enabled: bool = False,
    console: Optional[Console] = None,
) -> int:
    """Drive the launcher menu loop. Callbacks isolate the menu from the CLI
    (and make it testable). Returns the process exit code.

    ``on_run(dry, choice)`` — choice is None for the one-shot dry run (today's
    defaults) and a RunChoice from the U3 selector for a full run.
    ``on_read(date)`` — open the digest reader (None = newest digest).
    ``on_explain()`` — U7: offered after a run crashes (the run's telemetry is
    on disk; one LLM call explains it). Optional so render-only callers and
    older tests stay valid; without it the failure path just prints the error.
    """
    out = console or get_console()
    _banner(out)

    # Search/Ask are gated on the store being enabled AND the callbacks being wired.
    show_retrieval = store_enabled and on_search is not None and on_ask is not None
    options = _main_menu_options(show_retrieval)
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
                if run_choice.dry:
                    # Preview: ingest+normalize only — nothing delivered, no artifact
                    # to read, and not persisted as "last run".
                    on_run(True, None)
                    continue
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
            elif choice == "search" and on_search is not None:
                query = _prompt_query(out, "Search messages")
                if query:
                    on_search(query)
            elif choice == "ask" and on_ask is not None:
                query = _prompt_query(out, "Ask your inbox")
                if query:
                    on_ask(query)
            elif choice == "history" and on_history is not None:
                # History works with or without a query (Enter = browse all); the reader-style
                # drill-down inside it handles navigation.
                query = _prompt_query(out, "History — keyword (Enter = browse all)")
                on_history(query or "")
            elif choice == "diagnose":
                on_diagnose()
            elif choice == "settings":
                _settings_menu(out, on_settings, store_enabled=store_enabled)
                continue  # the submenu has its own loop + pauses
        except KeyboardInterrupt:
            out.print("\n[ap.warn]⚠ Interrupted — back to menu.[/]")
        except Exception as exc:  # noqa: BLE001 - keep the menu alive on action errors
            out.print(f"[ap.err]✗[/] {exc}")
            # U7: a failed run (incl. a Preview) leaves telemetry behind — offer the diagnosis.
            if choice == "run" and on_explain is not None:
                follow = choose(
                    "Ask the LLM what went wrong?",
                    [("explain", "Explain it now (one LLM call)"), ("menu", "Back to the menu")],
                    default_index=0,
                    console=out,
                    cancel_value="menu",
                )
                if follow == "explain":
                    try:
                        on_explain()
                    except KeyboardInterrupt:
                        out.print("\n[ap.warn]⚠ Interrupted — back to menu.[/]")
                    except Exception as explain_exc:  # noqa: BLE001 - same liveness rule
                        out.print(f"[ap.err]✗[/] {explain_exc}")

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
