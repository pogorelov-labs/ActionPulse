"""``actionpulse daemon`` — install / control the background ingestion LaunchAgent.

The LaunchAgent runs ``actionpulse daemon tick`` on an interval to keep the encrypted store
fresh (Mattermost every tick; Exchange when on-corp). ``install`` / ``uninstall`` / ``start``
/ ``stop`` are macOS launchd operations (macOS-gated; ``install --dry-run`` prints the plist
anywhere); ``tick`` / ``status`` / ``logs`` work everywhere. The plist carries no secrets —
the tick self-loads the store key from ``~/.config/actionpulse/env`` like the MCP server.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

import typer

from digest_core.config import Config
from digest_core.ui.glyphs import FAIL, OK

daemon_app = typer.Typer(
    help="Background ingestion daemon — keep the store fresh without an open session (macOS)."
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


def _reachability_word(ews_reachable: Optional[bool]) -> str:
    if ews_reachable is None:
        return "n/a"
    return "on-corp" if ews_reachable else "off-corp"


@daemon_app.command("status")
def daemon_status_cmd() -> None:
    """Show the daemon: installed?, last/next run, store counts, corp reachability, staleness."""
    from digest_core.daemon import status

    s = status.summarize()
    typer.echo(f"LaunchAgent: {'installed' if s.get('installed') else 'not installed'}")
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
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plist, write nothing."),
) -> None:
    """Install (and load) the launchd LaunchAgent. macOS only (`--dry-run` works anywhere)."""
    from digest_core.daemon import launchd

    cfg = Config()
    minutes = interval or cfg.daemon.interval_minutes
    if dry_run:
        typer.echo(f"Would write: {launchd.plist_path()}")
        typer.echo(f"Launch command: {' '.join(launchd.tick_command())}")
        typer.echo(launchd.render_plist(minutes).decode())
        return
    if sys.platform != "darwin":
        typer.echo(f"{FAIL} `daemon install` is macOS-only for now. Try `--dry-run`.")
        raise typer.Exit(0)
    ready, msg = _store_ready()
    if not ready:
        typer.echo(f"{FAIL} {msg}")
        raise typer.Exit(1)
    if not yes:
        typer.echo(f"This installs a launchd LaunchAgent that ingests every {minutes} min:")
        typer.echo(f"  plist:   {launchd.plist_path()}")
        typer.echo(f"  command: {' '.join(launchd.tick_command())}")
        typer.echo(
            f"  sources: {', '.join(cfg.daemon.source_list())}  (MM every tick; EWS when on-corp)"
        )
        typer.echo("The plist carries no secrets; `actionpulse daemon uninstall` reverses it.")
        if not typer.confirm("Install the background ingestion agent?", default=False):
            typer.echo("No changes.")
            raise typer.Exit(0)
    res = launchd.install(minutes)
    bak = f"  (backup: {res.backup.name})" if res.backup else ""
    typer.echo(f"{OK} {res.action}{bak} — {res.plist}")
    typer.echo(
        f"  {'loaded' if res.loaded else 'already loaded / enabled'};"
        f" runs every {minutes} min + at login."
    )
    typer.echo("  Check: `actionpulse daemon status`  ·  logs: `actionpulse daemon logs`")


@daemon_app.command("uninstall")
def daemon_uninstall_cmd(
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Unload + remove the LaunchAgent (leaves a timestamped .bak)."""
    from digest_core.daemon import launchd

    if not launchd.is_installed():
        typer.echo("Not installed.")
        raise typer.Exit(0)
    if not yes and not typer.confirm("Remove the background ingestion agent?", default=False):
        typer.echo("No changes.")
        raise typer.Exit(0)
    res = launchd.uninstall()
    bak = f"  (backup: {res.backup.name})" if res.backup else ""
    typer.echo(f"{OK} {res.action}{bak} — {res.plist}")


@daemon_app.command("start")
def daemon_start_cmd() -> None:
    """Load the agent and kick a run now (macOS)."""
    from digest_core.daemon import launchd

    if sys.platform != "darwin":
        typer.echo(f"{FAIL} macOS only.")
        raise typer.Exit(0)
    if not launchd.is_installed():
        typer.echo(f"{FAIL} not installed — run `actionpulse daemon install` first.")
        raise typer.Exit(1)
    kicked = launchd.start()
    typer.echo(
        f"{OK} kicked a run now" if kicked else f"{OK} agent loaded (kickstart not confirmed)"
    )


@daemon_app.command("stop")
def daemon_stop_cmd() -> None:
    """Unload the agent (reloads on next install/login) (macOS)."""
    from digest_core.daemon import launchd

    if sys.platform != "darwin":
        typer.echo(f"{FAIL} macOS only.")
        raise typer.Exit(0)
    launchd.stop()
    typer.echo(f"{OK} stopped (unloaded). Re-enable with `actionpulse daemon start`.")


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
    """Run one ingestion tick now — this is what the LaunchAgent invokes each interval."""
    from digest_core.daemon import tick

    src = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    try:
        result = tick.ingest_once(sources=src)
    except tick.DaemonError as exc:
        typer.echo(f"{FAIL} {exc}")
        raise typer.Exit(1)
    if result.skipped:
        typer.echo(f"skipped ({result.skipped}) — another writer holds the store; will retry.")
        return
    added = f"+{result.messages_added}" if isinstance(result.messages_added, int) else "?"
    typer.echo(
        f"{OK} tick done: ingested {result.sources_ingested or '—'} · {added} msgs ·"
        f" exchange {_reachability_word(result.ews_reachable)}"
    )
