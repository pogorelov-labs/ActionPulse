"""``actionpulse daemon`` — install / control the scheduled background ingestion tick.

A scheduled ``actionpulse daemon tick`` keeps the encrypted store fresh (Mattermost every
tick; Exchange when on-corp) without an open session. The scheduler is chosen per host —
launchd (macOS), a systemd **user** timer (Linux), or a marked cron block as the fallback
(ACTPULSE-99) — and every subcommand routes to whichever backend is actually installed, so
a host that gained systemd after a cron install can still remove its cron entry.

``tick`` / ``status`` / ``logs`` work everywhere and always did. No backend writes a secret
into its unit: the tick self-loads the store key from ``~/.config/actionpulse/env`` like the
MCP server, and every backend appends to the same ``var/logs/daemon.*.log`` pair so
``daemon logs`` reads one place.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import typer

from digest_core.config import Config
from digest_core.ui.glyphs import FAIL, OK, WARN

daemon_app = typer.Typer(
    help="Background ingestion daemon — keep the store fresh without an open session."
)


def _store_ready() -> Tuple[bool, str]:
    """(ready, message). The daemon needs the store enabled AND its key set."""
    from digest_core.ui.menu import load_env_file

    load_env_file()
    cfg = Config()
    if not cfg.store.enabled:
        return False, (
            "store is OFF — run `actionpulse store init`, enable it (store.enabled: true /"
            " DIGEST_STORE_ENABLED=1); the daemon has nothing to persist otherwise."
        )
    if not os.getenv(cfg.store.key_env):
        return False, f"{cfg.store.key_env} isn't set — run `actionpulse store init`."
    return True, ""


def _render_unit(backend, minutes: int) -> str:
    """The unit text a backend would write — for `--dry-run`.

    Each backend names its own renderer (a plist is bytes, a systemd unit is two files, a
    cron block is one line), so this is the one place that knows the difference.
    """
    if backend.NAME == "launchd":
        return backend.render_plist(minutes).decode()
    if backend.NAME == "systemd":
        return (
            f"--- {backend.service_path().name} ---\n{backend.render_service()}\n"
            f"--- {backend.timer_path().name} ---\n{backend.render_timer(minutes)}"
        )
    return backend.render(minutes)


def _reachability_word(ews_reachable: Optional[bool]) -> str:
    if ews_reachable is None:
        return "n/a"
    return "on-corp" if ews_reachable else "off-corp"


@daemon_app.command("status")
def daemon_status_cmd() -> None:
    """Show the daemon: installed?, last/next run, store counts, corp reachability, staleness."""
    from digest_core.daemon import status

    s = status.summarize()
    where = f" ({s['scheduler_backend']})" if s.get("scheduler_backend") else ""
    typer.echo(f"Scheduler: {'installed' if s.get('installed') else 'not installed'}{where}")
    if not s.get("last_run"):
        typer.echo("  never run yet — `actionpulse daemon install` (or `daemon tick` to try now)")
        return
    added = s.get("messages_added")
    added_str = f"  (+{added} msgs)" if isinstance(added, int) else ""
    typer.echo(f"  last run:  {s.get('last_run')}{added_str}")
    typer.echo(f"  next run:  {s.get('next_run') or '—'}")
    total = s.get("messages_total")
    if isinstance(total, int):
        by = s.get("by_source") or {}
        bysrc = ", ".join(f"{k}={v}" for k, v in by.items())
        typer.echo(f"  store:     {total} msgs" + (f"  ({bysrc})" if bysrc else ""))
    typer.echo(f"  exchange:  {_reachability_word(s.get('ews_reachable'))}")
    if s.get("is_stale"):
        typer.echo(
            f"  {FAIL} stale — no successful tick recently (staleness {s.get('staleness_days')}d)"
        )
    if not s.get("ok", True) and s.get("error"):
        typer.echo(f"  {FAIL} last error: {s.get('error')}")


@daemon_app.command("install")
def daemon_install_cmd(
    interval: Optional[int] = typer.Option(
        None, "--interval", help="Minutes between ticks (default: config daemon.interval_minutes)."
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the unit, write nothing."),
    backend_name: Optional[str] = typer.Option(
        None,
        "--backend",
        help="Force a scheduler: launchd | systemd | cron (default: best for this host).",
    ),
) -> None:
    """Install the scheduled tick using this host's scheduler (launchd/systemd/cron)."""
    from digest_core.daemon import scheduler
    from digest_core.daemon import tick as tick_mod

    cfg = Config()
    minutes = interval or cfg.daemon.interval_minutes
    try:
        backend = scheduler.select(backend_name)
    except ValueError as exc:
        # A typo in a flag is a user error, not a crash. Same reasoning as the tick's
        # one-line failure (ACTPULSE-101): a traceback here tells the reader nothing
        # they can act on and buries the list of valid names.
        typer.echo(f"{FAIL} {exc}")
        raise typer.Exit(1)
    if backend is None:
        typer.echo(
            f"{FAIL} no supported scheduler found (looked for launchd, systemd --user, cron)."
        )
        typer.echo("    `actionpulse daemon tick` still works; schedule it however you prefer.")
        raise typer.Exit(1)
    if dry_run:
        typer.echo(f"Backend: {backend.NAME}")
        typer.echo(f"Launch command: {' '.join(scheduler.tick_command())}")
        typer.echo(backend.describe(minutes))
        typer.echo(_render_unit(backend, minutes))
        return
    ready, msg = _store_ready()
    if not ready:
        typer.echo(f"{FAIL} {msg}")
        raise typer.Exit(1)
    # Refuse to schedule a unit that cannot possibly succeed. Installing one is how you
    # get a failure every 30 minutes forever, which trains you to ignore the log
    # (ACTPULSE-101) — the same "red nobody reads" trap as the nine silent CI nights.
    usable, skipped = tick_mod._partition_configured(cfg, cfg.daemon.source_list())
    if not usable:
        typer.echo(f"{FAIL} {tick_mod._unconfigured_message(skipped)}")
        typer.echo("    Nothing would be ingested, so the agent is not being installed.")
        raise typer.Exit(1)
    if skipped:
        for source, reason in sorted(skipped.items()):
            typer.echo(f"{WARN} {source} will be skipped every tick — {reason}")
    if not yes:
        typer.echo(f"This schedules a tick every {minutes} min via {backend.NAME}:")
        typer.echo("  " + backend.describe(minutes).replace("\n", "\n  "))
        typer.echo(f"  command: {' '.join(scheduler.tick_command())}")
        typer.echo(
            f"  sources: {', '.join(cfg.daemon.source_list())}  (MM every tick; EWS when on-corp)"
        )
        typer.echo("The unit carries no secrets; `actionpulse daemon uninstall` reverses it.")
        if not typer.confirm("Install the background ingestion agent?", default=False):
            typer.echo("No changes.")
            raise typer.Exit(0)
    res = backend.install(minutes)
    bak = f"  (backup: {res.backup.name})" if res.backup else ""
    typer.echo(f"{OK} {res.action}{bak} — {res.unit}  [{res.backend}]")
    typer.echo(
        f"  {'loaded' if res.loaded else 'already loaded / enabled'}; runs every {minutes} min"
        + ("." if backend.NAME == "cron" else " + at boot/login.")
    )
    # No silent caps: cron cannot express every interval, so say when it did something
    # other than what was asked for.
    note = getattr(backend, "schedule_for", lambda _m: (None, None))(minutes)[1]
    if note:
        typer.echo(f"  {WARN} {note}")
    typer.echo("  Check: `actionpulse daemon status`  ·  logs: `actionpulse daemon logs`")


@daemon_app.command("uninstall")
def daemon_uninstall_cmd(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Remove the scheduled tick (leaves a timestamped .bak where there is a file)."""
    from digest_core.daemon import scheduler

    # Whichever backend actually HAS a unit, not whichever we would pick now: a host that
    # gained systemd after a cron install must still be able to remove the cron entry.
    backend = scheduler.installed_backend()
    if backend is None:
        typer.echo("Not installed.")
        raise typer.Exit(0)
    if not yes and not typer.confirm(
        f"Remove the background ingestion agent ({backend.NAME})?", default=False
    ):
        typer.echo("No changes.")
        raise typer.Exit(0)
    res = backend.uninstall()
    bak = f"  (backup: {res.backup.name})" if res.backup else ""
    typer.echo(f"{OK} {res.action}{bak} — {res.unit}  [{res.backend}]")


@daemon_app.command("start")
def daemon_start_cmd() -> None:
    """Enable the schedule and run one tick now."""
    from digest_core.daemon import scheduler

    backend = scheduler.installed_backend()
    if backend is None:
        typer.echo(f"{FAIL} not installed — run `actionpulse daemon install` first.")
        raise typer.Exit(1)
    if backend.start():
        typer.echo(f"{OK} kicked a run now  [{backend.NAME}]")
        return
    # cron has no "run now", and launchctl kickstart can decline — either way the user
    # asked for a tick, so give them one instead of a shrug.
    typer.echo(f"  {backend.NAME} cannot trigger a run itself; running one tick inline…")
    daemon_tick_cmd(sources=None)


@daemon_app.command("stop")
def daemon_stop_cmd() -> None:
    """Stop the schedule (the unit stays on disk; `start` re-enables it)."""
    from digest_core.daemon import scheduler

    backend = scheduler.installed_backend()
    if backend is None:
        typer.echo("Not installed.")
        raise typer.Exit(0)
    if backend.NAME == "cron":
        typer.echo(
            f"{WARN} cron has no enable/disable — stopping means removing the entry."
            " Use `actionpulse daemon uninstall`."
        )
        raise typer.Exit(0)
    backend.stop()
    typer.echo(f"{OK} stopped  [{backend.NAME}]. Re-enable with `actionpulse daemon start`.")


@daemon_app.command("logs")
def daemon_logs_cmd(
    lines: int = typer.Option(20, "--lines", "-n", help="Tail this many lines from each log."),
) -> None:
    """Show the tail of the daemon's stdout/stderr logs (`var/logs/daemon.*.log`)."""
    from digest_core import paths

    logs = paths.logs_dir(create=False)
    found = False
    for name in ("daemon.out.log", "daemon.err.log"):
        p = logs / name
        if p.exists() and p.stat().st_size:
            found = True
            typer.echo(f"── {name} ──")
            typer.echo("\n".join(p.read_text(errors="replace").splitlines()[-lines:]))
    if not found:
        typer.echo("No daemon logs yet.")


@daemon_app.command("tick")
def daemon_tick_cmd(
    sources: Optional[str] = typer.Option(
        None, "--sources", help="Comma-separated override (default: config daemon.sources)."
    ),
) -> None:
    """Run one ingestion tick now — what the scheduler invokes each interval."""
    from digest_core.daemon import tick

    src = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    try:
        result = tick.ingest_once(sources=src)
    except tick.DaemonError as exc:
        typer.echo(f"{FAIL} {exc}")
        raise typer.Exit(1)
    except Exception as exc:  # noqa: BLE001 - see below
        # This process is launchd's, and its stderr is a LOG FILE, not a terminal. A
        # Rich traceback there is ~178 lines that repeat every interval forever
        # (ACTPULSE-101). The status file already carries the error for `daemon status`
        # and the MCP `health` tool, so one line is the right amount of output here.
        typer.echo(f"{FAIL} tick failed: {type(exc).__name__}: {exc}")
        raise typer.Exit(1)
    if result.sources_skipped:
        for source, reason in sorted(result.sources_skipped.items()):
            typer.echo(f"  skipped {source} — {reason}")
    if result.skipped:
        typer.echo(f"skipped ({result.skipped}) — another writer holds the store; will retry.")
        return
    added = f"+{result.messages_added}" if isinstance(result.messages_added, int) else "?"
    typer.echo(
        f"{OK} tick done: ingested {result.sources_ingested or '—'} · {added} msgs ·"
        f" exchange {_reachability_word(result.ews_reachable)}"
    )
