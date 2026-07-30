import json
import os
import shutil
import subprocess
import sys
import typer
from datetime import datetime
from pathlib import Path

import httpx

from digest_core import paths
from digest_core.diagnostics import export_diagnostics, _build_env_info
from digest_core.deliver.mattermost import ping_mattermost_webhook
from digest_core.run import run_digest, run_digest_dry_run
from digest_core.observability.logs import setup_logging
from digest_core.mcp.commands import mcp_app
from digest_core.daemon.commands import daemon_app
from digest_core.ui import resolve_sink
from digest_core.ui.glyphs import FAIL, OK, WARN
from digest_core.ui.menu import RunChoice, load_env_file, run_menu, stdin_is_tty
from digest_core.config import Config

app = typer.Typer(add_completion=False)


def _file_logging_enabled() -> bool:
    """observability.log_to_file with a safe default (U6 logging toggle)."""
    from digest_core.maintenance import file_logging_enabled

    return file_logging_enabled()


def _menu_run(dry: bool, choice: RunChoice | None = None) -> None:
    """Run the pipeline from the menu; the U3 selector decides period/force."""
    sink = resolve_sink("auto", sys.stdout.isatty())
    choice = choice or RunChoice()
    common = dict(
        from_date=choice.from_date,
        sources=["ews"],
        out=str(paths.out_dir()),
        model="qwen35-397b-a17b",
        window=choice.window,
        state=None,
        force=choice.force,
        sink=sink,
    )
    setup_logging(console=False, enabled=_file_logging_enabled())
    if dry:
        run_digest_dry_run(**common)
    else:
        run_digest(validate_citations=True, **common)


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    """ActionPulse — daily digest of actions from your corporate inbox.

    Run a subcommand, or run `actionpulse` with no arguments on a terminal to
    open the interactive menu.
    """
    # Auto-load ~/.config/actionpulse/env so secrets need no manual `source`.
    load_env_file()
    if ctx.invoked_subcommand is not None:
        return
    if not stdin_is_tty():
        typer.echo(ctx.get_help())
        raise typer.Exit(0)

    def _safe(fn):
        # In the menu, a subcommand's exit/abort ends that action, not the app.
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (typer.Exit, SystemExit):
                return None

        return wrapped

    # Surface Search/Ask in the menu only when the store is enabled (else they're dead rows).
    try:
        store_enabled = Config().store.enabled
    except Exception:  # noqa: BLE001 - a bad config must not break the launcher
        store_enabled = False

    code = run_menu(
        on_run=_safe(_menu_run),
        on_diagnose=_safe(diagnose),
        on_settings=_safe(lambda: setup(no_autodetect=False)),
        on_read=_safe(lambda date: read(date=date, out=None)),
        on_explain=_safe(lambda: explain(trace_id=None, date=None)),
        # All typer params passed explicitly (calling a command function directly leaves
        # unpassed Option/Argument defaults as their info objects — the read/explain pattern).
        on_search=_safe(
            lambda q: search(
                query=q,
                mode=None,
                keyword=False,
                semantic=False,
                hybrid=False,
                source=None,
                since=None,
                limit=None,
                json_out=False,
            )
        ),
        on_ask=_safe(
            lambda q: ask(question=q, k=8, mode=None, source=None, since=None, json_out=False)
        ),
        on_history=_safe(
            lambda q: history(
                query=q or None,
                since=None,
                until=None,
                section=None,
                limit=50,
                out=None,
                json_out=False,
            )
        ),
        store_enabled=store_enabled,
    )
    raise typer.Exit(code)


@app.command()
def run(
    from_date: str = typer.Option(
        "today", "--from-date", help="Date to process (YYYY-MM-DD or 'today')"
    ),
    sources: str = typer.Option(
        "ews", "--sources", help="Comma-separated source types (e.g., 'ews')"
    ),
    out: str = typer.Option(
        None, "--out", help="Output directory path (default: <data home>/var/out)"
    ),
    model: str = typer.Option("qwen35-397b-a17b", "--model", help="LLM model identifier"),
    window: str = typer.Option(
        "calendar_day", "--window", help="Time window: calendar_day or rolling_24h"
    ),
    state: str = typer.Option(
        None, "--state", help="State directory path (overrides config for SyncState)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run ingest+normalize only, skip LLM/assemble"
    ),
    force: bool = typer.Option(False, "--force", help="Bypass the T-48h idempotency check"),
    dump_ingest: str = typer.Option(
        None, "--dump-ingest", help="Write normalized ingest snapshot to JSON"
    ),
    replay_ingest: str = typer.Option(
        None,
        "--replay-ingest",
        help="Replay a normalized ingest snapshot instead of EWS",
    ),
    record_llm: str = typer.Option(None, "--record-llm", help="Record LLM responses to JSON file"),
    replay_llm: str = typer.Option(
        None, "--replay-llm", help="Replay LLM responses from a recorded JSON file"
    ),
    validate_citations: bool = typer.Option(
        True,
        "--validate-citations/--no-validate-citations",
        help="Build+validate citations (default on, PR11); exit 2 only when support recall < floor",
    ),
    collect_logs: bool = typer.Option(
        False,
        "--collect-logs",
        help="Automatically collect diagnostics after run (requires git checkout; no-op in wheel installs)",
    ),
    log_file: str = typer.Option(None, "--log-file", help="Specify log file path"),
    log_level: str = typer.Option(
        "INFO", "--log-level", help="Log level (DEBUG, INFO, WARNING, ERROR)"
    ),
    progress: str = typer.Option(
        "auto",
        "--progress",
        help=(
            "Progress display: auto (live footer on a TTY, plain otherwise/CI),"
            " live, plain (append-only build-log lines), none (JSON logs on"
            " console)"
        ),
        case_sensitive=False,
    ),
):
    """Run daily digest job."""
    try:
        out = out or str(paths.out_dir())  # U5: one data home, not cwd-relative ./out
        progress = progress.lower()
        if progress not in ("auto", "live", "plain", "none"):
            typer.echo(f"Invalid --progress value: {progress}", err=True)
            raise typer.Exit(1)
        # A progress renderer owns the terminal: JSON logs go to the file only.
        # U6: observability.log_to_file off -> no file; explicit --log-file wins.
        setup_logging(
            log_level=log_level,
            log_file=log_file,
            console=(progress == "none"),
            enabled=_file_logging_enabled() or bool(log_file),
        )
        sink = resolve_sink(progress, sys.stdout.isatty())

        if dry_run:
            typer.echo("Dry-run mode: ingest+normalize only")
            run_digest_dry_run(
                from_date,
                sources.split(","),
                out,
                model,
                window,
                state,
                validate_citations,
                force=force,
                dump_ingest=dump_ingest,
                replay_ingest=replay_ingest,
                record_llm=record_llm,
                replay_llm=replay_llm,
                sink=sink,
            )
            exit_code = 0  # Dry-run completed successfully
        else:
            run_result = run_digest(
                from_date,
                sources.split(","),
                out,
                model,
                window,
                state,
                validate_citations,
                force=force,
                dump_ingest=dump_ingest,
                replay_ingest=replay_ingest,
                record_llm=record_llm,
                replay_llm=replay_llm,
                sink=sink,
            )

            # Exit with code 2 if citation validation failed
            if validate_citations and not run_result.citation_validation_ok:
                typer.echo(f"{WARN} Citation validation failed", err=True)
                exit_code = 2
            else:
                exit_code = 0  # Success

        # Collect diagnostics if requested
        if collect_logs:
            typer.echo("Collecting diagnostics...")
            try:
                script_dir = Path(__file__).parent.parent.parent / "scripts"
                collect_script = script_dir / "collect_diagnostics.sh"
                if collect_script.exists():
                    subprocess.run([str(collect_script)], check=True)
                    typer.echo(f"{OK} Diagnostics collected successfully")
                else:
                    typer.echo(f"{WARN} Diagnostics script not found", err=True)
            except Exception as e:
                typer.echo(f"{WARN} Failed to collect diagnostics: {e}", err=True)

        sys.exit(exit_code)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        # U7: the run's telemetry is on disk — offer the one-call diagnosis.
        typer.echo("Hint: `actionpulse explain` asks the LLM what went wrong.", err=True)
        sys.exit(1)  # Error


@app.command()
def read(
    date: str = typer.Option(
        None, "--date", help="Digest date to read (YYYY-MM-DD; default: newest)"
    ),
    out: str = typer.Option(
        None, "--out", help="Digest output directory (default: <data home>/var/out)"
    ),
):
    """Browse a digest interactively: topics, authors, distilled items, quotes.

    Drill down digest → section → item; every opened card stays in scrollback
    with its evidence trace. Non-TTY invocations print the markdown digest
    instead (scriptable).
    """
    from digest_core.ui.reader import read_digest_interactive, render_digest_plain

    out_dir = Path(out).expanduser() if out else paths.out_dir(create=False)
    if not stdin_is_tty() or not sys.stdout.isatty():
        text = render_digest_plain(out_dir, date)
        if text is None:
            typer.echo(f"No digest found in {out_dir} — run `actionpulse run` first.", err=True)
            raise typer.Exit(1)
        typer.echo(text)
        raise typer.Exit(0)
    try:
        raise typer.Exit(read_digest_interactive(out_dir, date=date))
    except KeyboardInterrupt:
        # §5.5 abort contract: typed message, no traceback, exit 130.
        typer.echo("\nInterrupted.")
        raise typer.Exit(130)


@app.command()
def history(
    query: str = typer.Argument(
        None, help="Keyword to filter items (optional; omit to browse all)"
    ),
    since: str = typer.Option(None, "--since", help="Only digests on/after YYYY-MM-DD"),
    until: str = typer.Option(None, "--until", help="Only digests on/before YYYY-MM-DD"),
    section: str = typer.Option(
        None, "--section", help="Section key: my_actions | urgent | fyi | status | unconfirmed"
    ),
    limit: int = typer.Option(50, "--limit", help="Max items (newest first)"),
    out: str = typer.Option(
        None, "--out", help="Digest output directory (default: <data home>/var/out)"
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """Search & browse across ALL past digests — the curated output history.

    Unlike `search` (raw message store) and `read` (one day), this scans every digest artifact
    in the out dir and lists matching items chronologically. Fully offline — no store needed.
    On a TTY it then offers to open a day in the reader.
    """
    from digest_core.history import search_history
    from digest_core.ui.reader import _ellipsis, read_digest_interactive

    for flag, value in (("--since", since), ("--until", until)):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                typer.echo(f"{FAIL} {flag} expects YYYY-MM-DD, e.g. 2026-06-11", err=True)
                raise typer.Exit(1)

    out_dir = Path(out).expanduser() if out else paths.out_dir(create=False)
    hits = search_history(out_dir, query, since=since, until=until, section=section, limit=limit)

    if json_out:
        import json as _json

        payload = [
            {
                "digest_date": h.digest_date,
                "section": h.section_key or h.section_title,
                "title": h.item.title,
                "due": h.item.due,
                "source_from": h.item.source_from,
                "source_subject": h.item.source_subject,
                "evidence_id": h.item.evidence_id,
                "weak_evidence": bool(getattr(h.item, "weak_evidence", False)),
                "seen_before": bool(getattr(h.item, "seen_before", False)),
            }
            for h in hits
        ]
        typer.echo(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not hits:
        scope = f" matching {query!r}" if query else ""
        typer.echo(f"{FAIL} No items{scope} across past digests in {out_dir}.")
        return

    for h in hits:
        sec = (h.section_key or h.section_title or "")[:11]
        who = h.item.source_from or ""
        flags = f"  {WARN} weak" if getattr(h.item, "weak_evidence", None) else ""
        typer.echo(f"  {h.digest_date}  {sec:<11}  {_ellipsis(h.item.title, 46):<46}  {who}{flags}")
    days = len({h.digest_date for h in hits})
    typer.echo(f"\n  {len(hits)} item(s) across {days} digest(s).")

    # On a TTY, offer to open one of the matching days in the reader (the §5.2 drill-down).
    if stdin_is_tty() and sys.stdout.isatty():
        from digest_core.ui.console import get_console
        from digest_core.ui.reader import _paged_choose

        console = get_console()
        counts: dict[str, int] = {}
        for h in hits:  # newest-first; keep first-seen order for the picker
            counts[h.digest_date] = counts.get(h.digest_date, 0) + 1
        entries = [(d, f"{d}  ({n} item{'s' if n != 1 else ''})") for d, n in counts.items()]
        picked = _paged_choose(console, "Open a digest day?", entries, back_label="Done")
        if picked:
            read_digest_interactive(out_dir, date=picked, console=console)


@app.command()
def explain(
    trace_id: str = typer.Option(None, "--trace-id", help="Trace ID of the run to explain"),
    date: str = typer.Option(None, "--date", help="Digest date of the run to explain"),
):
    """Ask the corp LLM to explain what went wrong in a run (short card).

    Collects the run's own telemetry — trace-*.meta.json (status, stage
    health, LLM trace, budget) plus the tail of the redacted structured log —
    and makes ONE compact LLM call on its own call budget (never the
    pipeline's). Newest run by default. Needs the corp network (ADR-012).
    """
    from rich.panel import Panel
    from rich.text import Text

    from digest_core.explain import ExplainUnavailable, explain_run
    from digest_core.ui import get_console

    console = get_console()
    try:
        with console.status("[ap.accent]Asking the LLM about this run…"):
            result = explain_run(trace_id=trace_id, date=date)
    except ExplainUnavailable as exc:
        typer.echo(f"{FAIL} {exc}", err=True)
        raise typer.Exit(1)

    body = Text()
    body.append(result.likely_cause, style="ap.em")
    if result.explanation:
        body.append("\n\n")
        body.append(result.explanation)
    if result.next_steps:
        body.append("\n")
        for step in result.next_steps:
            body.append(f"\n  → {step}")
    body.append(
        f"\n\nrun {result.digest_date} · status {result.status} · {result.model}"
        f" · trace {result.trace_id[:8]}",
        style="ap.dim",
    )
    console.print(Panel(body, title="Run explained", border_style="ap.rule", expand=False))


@app.command()
def clean(
    logs: bool = typer.Option(False, "--logs", help="Delete run logs (incl. legacy dirs)"),
    digests: bool = typer.Option(
        False, "--digests", help="Delete digest artifacts older than --older-than days"
    ),
    all_files: bool = typer.Option(
        False, "--all", help="Delete ALL digests and logs (state/config/secrets untouched)"
    ),
    older_than: int = typer.Option(
        14, "--older-than", help="Retention for --digests, in days (by digest date)"
    ),
):
    """Free disk space in the data home; with no flags, just show usage.

    Cleaning only ever touches regenerable files (digests, logs) — never the
    EWS sync state, config, or secrets.
    """
    from digest_core import maintenance

    if not (logs or digests or all_files):
        for entry in maintenance.collect_usage():
            typer.echo(
                f"  {entry.label:<26} {entry.files:>5} files"
                f"  {maintenance.format_bytes(entry.size_bytes):>10}  {entry.path}"
            )
        typer.echo("\nNothing deleted. Use --logs / --digests / --all.")
        return
    removed = freed = 0
    if logs or all_files:
        n, b = maintenance.clean_logs()
        removed, freed = removed + n, freed + b
    if digests or all_files:
        n, b = maintenance.clean_digests(None if all_files else older_than)
        removed, freed = removed + n, freed + b
    typer.echo(f"{OK} Removed {removed} files, freed {maintenance.format_bytes(freed)}.")


@app.command("paths")
def paths_command():
    """Show where ActionPulse keeps its files (one data home + the exceptions).

    Everything regenerable lives under <data home>/var (digests, logs, state).
    Secrets stay in ~/.config/actionpulse/env on purpose: inside the checkout,
    `git clean -xdf` would delete them and an accidental commit could leak
    them. Override the data home with the ACTIONPULSE_HOME env var.
    """
    for key, value in paths.describe().items():
        mark = OK if Path(value).exists() else " "
        typer.echo(f"  {mark} {paths.LABELS.get(key, key):<12} {value}")


def _open_store_or_exit():
    """Open the message store or exit(1) with an actionable message."""
    from digest_core.config import Config
    from digest_core.store import HAS_SQLCIPHER, INSTALL_HINT, MessageStore, StoreError

    cfg = Config().store
    if not HAS_SQLCIPHER:
        typer.echo(f"{FAIL} {INSTALL_HINT}", err=True)
        raise typer.Exit(1)
    if not cfg.enabled:
        typer.echo(
            f"{FAIL} Message store is off. Run `actionpulse store init`, set store.enabled: "
            "true (or DIGEST_STORE_ENABLED=1), then run a digest.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        return MessageStore.open(cfg)
    except (StoreError, ValueError) as exc:
        typer.echo(f"{FAIL} {exc}", err=True)
        raise typer.Exit(1)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search text"),
    mode: str = typer.Option(None, "--mode", help="keyword | semantic | hybrid"),
    keyword: bool = typer.Option(False, "--keyword", help="Keyword (FTS5) only"),
    semantic: bool = typer.Option(False, "--semantic", help="Semantic (vector) only"),
    hybrid: bool = typer.Option(False, "--hybrid", help="Hybrid (RRF); needs embeddings"),
    source: str = typer.Option(None, "--source", help="Filter by source: ews | mm"),
    since: str = typer.Option(None, "--since", help="Only on/after YYYY-MM-DD"),
    limit: int = typer.Option(None, "--limit", help="Max results"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """Search the encrypted message store (FTS5 keyword / cosine semantic / hybrid).

    Keyword search is fully offline. Semantic/hybrid embed the query via the corp
    gateway and need the corp network (or recorded fleet calls).
    """
    from digest_core.api import GatewayUnavailable, InboxAPI
    from digest_core.config import Config

    chosen = "keyword" if keyword else "semantic" if semantic else "hybrid" if hybrid else mode
    api = InboxAPI(_open_store_or_exit(), Config())  # InboxAPI owns the embeddings wiring
    try:
        eff_mode = chosen or api.store.config.search_default_mode
        try:
            # strict: an explicit semantic/hybrid request errors if the gateway is down,
            # rather than silently degrading to keyword (the CLI's prior behavior).
            hits = api.search(
                query, mode=eff_mode, source=source, since=since, limit=limit, strict=True
            )
        except GatewayUnavailable as exc:
            typer.echo(f"{FAIL} {eff_mode} search needs the embeddings gateway: {exc}", err=True)
            raise typer.Exit(1)
        except Exception as exc:  # noqa: BLE001 - clean CLI error, never a traceback
            typer.echo(f"{FAIL} search failed: {exc}", err=True)
            raise typer.Exit(1)
    finally:
        api.close()

    if json_out:
        import json as _json

        payload = [
            {
                "message_id": h.message_id,
                "score": round(h.score, 6),
                "subject": h.subject,
                "snippet": h.snippet,
                "received_at": h.received_at,
                "source": h.source,
                "author": h.author_email,
                "provenance": h.provenance,
            }
            for h in hits
        ]
        typer.echo(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not hits:
        typer.echo(
            f"{FAIL} No matches. The store may be empty (run a digest with the store enabled) "
            "or lack vectors for semantic search (`actionpulse store reembed`)."
        )
        return
    for h in hits:
        subj = (h.subject or "")[:48]
        snippet = " ".join((h.snippet or "").split())[:80]
        typer.echo(f"  {h.received_at[:10]}  {h.source:<5}  {subj:<48}  {snippet}")


@app.command()
def ask(
    question: str = typer.Argument(..., help="A question about your stored messages"),
    k: int = typer.Option(8, "--k", "-k", help="How many passages to ground the answer on"),
    mode: str = typer.Option(None, "--mode", help="retrieval: keyword | semantic | hybrid"),
    source: str = typer.Option(None, "--source", help="Filter retrieval: ews | mm"),
    since: str = typer.Option(None, "--since", help="Only on/after YYYY-MM-DD"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
):
    """Ask a grounded, cited question about your stored messages (RAG over the store).

    Retrieves with hybrid search, then asks the corp gateway ONE question that may only
    answer from your retrieved messages (Extract-over-Generate). Needs the corp network;
    keyword `search` stays offline. DMs are never retrievable (redacted at rest).
    """
    from digest_core.api import GatewayUnavailable, InboxAPI
    from digest_core.config import Config

    api = InboxAPI(_open_store_or_exit(), Config())  # InboxAPI owns retrieval + the gateway call
    try:
        eff_mode = mode or api.store.config.search_default_mode
        try:
            result = api.ask(question, mode=eff_mode, top_k=k, source=source, since=since)
        except GatewayUnavailable as exc:
            typer.echo(f"{FAIL} {exc}", err=True)
            raise typer.Exit(1)
    finally:
        api.close()

    if json_out:
        import json as _json

        typer.echo(
            _json.dumps(
                {
                    "question": result.question,
                    "answer": result.answer,
                    "answered": result.answered,
                    "citations": [
                        {"message_id": c.message_id, "quote": c.quote} for c in result.citations
                    ],
                    "model": result.model,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    from digest_core.ui import get_console

    console = get_console()
    console.print(result.answer, style="ap.em" if result.answered else "ap.dim")
    if result.citations:
        console.print("\n[ap.dim]sources[/]")
        for c in result.citations:
            quote = (" — " + c.quote) if c.quote else ""
            console.print(f"  [ap.accent]{c.message_id}[/]{quote}", style="ap.dim")


store_app = typer.Typer(help="Manage the encrypted message store (opt-in).")
app.add_typer(store_app, name="store")


@store_app.command("init")
def store_init():
    """Generate the encryption key (if missing) and show how to enable the store.

    The key is written to ~/.config/actionpulse/env (chmod 600), the same secret
    channel as EWS_PASSWORD / MM_PAT. Losing it makes the store unreadable.
    """
    import os
    import secrets

    from digest_core.ui.menu import ENV_PATH

    existing = os.getenv("DIGEST_STORE_KEY")
    if not existing and ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("DIGEST_STORE_KEY="):
                existing = line.split("=", 1)[1].strip()
                break
    if existing:
        typer.echo(f"{OK} DIGEST_STORE_KEY already set ({ENV_PATH}).")
    else:
        key = secrets.token_hex(32)
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(ENV_PATH.parent, 0o700)
        # Create/append with 0600 from the start (os.open with mode) so the 256-bit
        # key never lands in a world-readable file, even momentarily, before chmod.
        fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(f"\nDIGEST_STORE_KEY={key}\n")
        os.chmod(ENV_PATH, 0o600)  # tighten if the file pre-existed with looser perms
        typer.echo(f"{OK} Generated DIGEST_STORE_KEY in {ENV_PATH} (chmod 600).")
        typer.echo("    Keep it safe — losing the key makes the encrypted store unreadable.")
    typer.echo(
        "Enable the store: add `store:\\n  enabled: true` to configs/config.yaml "
        "(or export DIGEST_STORE_ENABLED=1), then run a digest to populate it."
    )


@store_app.command("stats")
def store_stats():
    """Show store contents (rows by source, chunks/embeddings, age) and disk size."""
    from digest_core import maintenance

    store = _open_store_or_exit()
    try:
        st = store.stats()
    finally:
        store.close()
    by_source = ", ".join(f"{k}={v}" for k, v in st["by_source"].items()) or "—"
    db_path = Path(store.config.resolved_db_path())
    size = db_path.stat().st_size if db_path.exists() else 0
    typer.echo(f"  messages    {st['messages']}  ({by_source})")
    typer.echo(f"  chunks      {st['chunks']}")
    typer.echo(f"  embeddings  {st['embeddings']}")
    typer.echo(f"  window      {st['oldest'] or '—'} … {st['newest'] or '—'}")
    typer.echo(f"  db size     {maintenance.format_bytes(size)}  {db_path}")


@store_app.command("purge")
def store_purge(
    ttl_days: int = typer.Option(None, "--ttl-days", help="Override the configured TTL"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt"),
):
    """Apply the TTL now — delete messages older than the retention window."""
    store = _open_store_or_exit()
    try:
        days = ttl_days if ttl_days is not None else store.config.ttl_days
        if not yes:
            typer.confirm(f"Delete store messages older than {days} days?", abort=True)
        deleted = store.sweep_ttl(days)
        store.vacuum()
    finally:
        store.close()
    typer.echo(f"{OK} Purged {deleted} message(s) older than {days} days.")


@store_app.command("reembed")
def store_reembed(
    force: bool = typer.Option(
        False, "--force", help="Drop existing vectors first (use after changing the model/dtype)"
    ),
):
    """Fill the embedding backlog (chunks without a vector). Needs the corp network.

    After changing store.embedding_model or vector_dtype, pass --force: every chunk
    still has its stale vector, so a plain reembed finds no work and semantic search
    would return empty.
    """
    from digest_core.api import InboxAPI

    api = InboxAPI(_open_store_or_exit(), Config())  # owns the embeddings wiring (from_config)
    try:
        try:
            result = api.reembed(force=force)
        except Exception as exc:  # noqa: BLE001 - surface a clean message, not a traceback
            typer.echo(f"{FAIL} reembed failed: {exc}", err=True)
            raise typer.Exit(1)
    finally:
        api.close()
    typer.echo(f"{OK} Embedded {result['embedded']} chunk(s); {result['pending']} still pending.")


@store_app.command("vacuum")
def store_vacuum():
    """Reclaim free space in the encrypted DB after large deletes."""
    store = _open_store_or_exit()
    try:
        store.vacuum()
    finally:
        store.close()
    typer.echo(f"{OK} Vacuumed the store.")


@store_app.command("drop")
def store_drop(yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt")):
    """Delete the entire store database file (irreversible)."""
    from digest_core.config import Config

    db_path = Path(Config().store.resolved_db_path())
    if not db_path.exists():
        typer.echo(f"{OK} No store database at {db_path}.")
        return
    if not yes:
        typer.confirm(f"Permanently delete the store at {db_path}?", abort=True)
    removed = 0
    for p in (
        db_path,
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    ):
        try:
            if p.exists():
                p.unlink()
                removed += 1
        except OSError:
            continue
    typer.echo(f"{OK} Deleted the store ({removed} file(s)).")


reactions_app = typer.Typer(help="EP-15 reaction feedback on delivered digests (corp-only).")
app.add_typer(reactions_app, name="reactions")

app.add_typer(mcp_app, name="mcp")
app.add_typer(daemon_app, name="daemon")


@reactions_app.command("harvest")
def reactions_harvest(
    date: str = typer.Option(None, "--date", help="Only posts for this digest date (YYYY-MM-DD)"),
    out: str = typer.Option(
        None, "--out", help="Write the per-evidence ack/nack summary JSON here"
    ),
    gold_out: str = typer.Option(
        None,
        "--gold-out",
        help="Write a gold-label JSONL here, ready for `eval-gold --reactions` (the flywheel bridge)",
    ),
    lang: str = typer.Option("ru", "--lang", help="Language stratum stamped on gold rows (ru|en)"),
):
    """Harvest Mattermost reactions on delivered digest posts → ack/nack per evidence id.

    Reads the delivered-posts ledger (written by api-mode delivery) and queries the MM
    API — corp network only (ADR-012). Feeds EP-15 calibration of the citation gate.

    ``--gold-out`` closes the flywheel: it writes the per-reaction JSONL that
    ``eval-gold --reactions`` / ``eval-calibrate`` consume, so the harvest feeds
    calibration directly with no hand-built file.
    """
    import httpx

    from digest_core.config import Config
    from digest_core.feedback.delivered_ledger import read_ledger
    from digest_core.feedback.reactions import harvest_reactions, summarize, to_gold_rows
    from digest_core.ingest.mattermost import MattermostReadClient

    config = Config()
    entries = read_ledger(config.resolved_state_dir(), digest_date=date)
    if not entries:
        typer.echo(
            f"{FAIL} No delivered posts in the ledger. Deliver a digest with "
            "deliver.mattermost.auth_mode=api first (only api mode captures post ids).",
            err=True,
        )
        raise typer.Exit(1)
    mm = config.mm_source
    base_url = mm.get_base_url()
    if not base_url:
        typer.echo(f"{FAIL} Mattermost base URL not set (${mm.base_url_env}).", err=True)
        raise typer.Exit(1)
    try:
        token = mm.get_token()
    except ValueError as exc:
        typer.echo(f"{FAIL} {exc}", err=True)
        raise typer.Exit(1)
    client = MattermostReadClient(
        base_url,
        token,
        http_client=httpx.Client(timeout=httpx.Timeout(mm.timeout_s), verify=mm.verify_ssl),
        per_page=mm.per_page,
    )
    try:
        records = harvest_reactions(client, entries)
    except Exception as exc:  # noqa: BLE001 - corp-only; surface a clean message
        typer.echo(f"{FAIL} reaction harvest failed (needs the corp network): {exc}", err=True)
        raise typer.Exit(1)
    summary = summarize(records)
    t = summary["totals"]
    typer.echo(
        f"{OK} {summary['reactions']} reaction(s) over {len(entries)} post(s): "
        f"{t['ack']} ack / {t['nack']} nack / {t['other']} other; "
        f"{len(summary['by_evidence'])} evidence id(s) with signal."
    )
    if out:
        Path(out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"  wrote per-evidence summary → {out}")
    if gold_out:
        rows = to_gold_rows(records, lang=lang)
        Path(gold_out).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )
        typer.echo(
            f"  wrote {len(rows)} gold row(s) → {gold_out} (feed to `eval-gold --reactions`)"
        )


@app.command()
def diagnose():
    """Run environment diagnostics.

    Attempts to run shell-based scripts from digest-core/scripts/ (available in
    a git checkout).  When scripts are not present (e.g. wheel install), falls
    back to a Python-only environment report.
    """
    try:
        typer.echo("Running ActionPulse diagnostics...")

        script_dir = Path(__file__).parent.parent.parent / "scripts"
        ran_shell = False

        env_script = script_dir / "print_env.sh"
        if env_script.exists():
            typer.echo("Running environment diagnostics (shell)...")
            result = subprocess.run([str(env_script)], capture_output=True, text=True)
            typer.echo(result.stdout)
            if result.stderr:
                typer.echo(result.stderr, err=True)
            ran_shell = True

        collect_script = script_dir / "collect_diagnostics.sh"
        if collect_script.exists():
            typer.echo("Collecting comprehensive diagnostics (shell)...")
            result = subprocess.run([str(collect_script)], capture_output=True, text=True)
            typer.echo(result.stdout)
            if result.stderr:
                typer.echo(result.stderr, err=True)
            ran_shell = True

        if not ran_shell:
            # Shell scripts not available (wheel install or scripts/ absent).
            # Provide a Python-only environment report.
            typer.echo("Shell diagnostics scripts not found — running Python-based report.")
            typer.echo("")
            typer.echo(_build_env_info())
            typer.echo("Required ENV vars:")
            for var in (
                "EWS_USER_UPN",
                "EWS_PASSWORD",
                "LLM_TOKEN",
                "EWS_ENDPOINT",
                "LLM_ENDPOINT",
            ):
                value = os.environ.get(var)
                status = f"set ({len(value)} chars)" if value else "NOT SET"
                mark = OK if value else FAIL
                typer.echo(f"  {mark} {var}: {status}")
            typer.echo("")
            typer.echo("Tools:")
            for tool in ("uv", "docker", "pytest", "ruff"):
                path = shutil.which(tool)
                mark = OK if path else FAIL
                typer.echo(f"  {mark} {tool}: {path or 'not found'}")
            typer.echo("")
            typer.echo(
                "Note: full shell-based diagnostics require a git checkout (digest-core/scripts/)."
            )

        typer.echo(f"{OK} Diagnostics completed")

    except Exception as e:
        typer.echo(f"Error running diagnostics: {e}", err=True)
        sys.exit(1)


@app.command("mm-ping")
def mm_ping(
    message: str | None = typer.Option(
        None,
        "--message",
        "-m",
        help="Markdown text to send (default: short built-in ping string)",
    ),
):
    """Send one test POST to the Mattermost incoming webhook (MM_WEBHOOK_URL).

    Use from the same host/network as the digest runner to verify connectivity
    to e.g. mattermost.raiffeisen.ru before a full pipeline run.
    """
    try:
        setup_logging()
        config = Config().deliver.mattermost
        status = ping_mattermost_webhook(config, text=message)
        typer.echo(f"Mattermost webhook OK (HTTP {status}).")
    except ValueError as e:
        typer.echo(f"Configuration error: {e}", err=True)
        raise typer.Exit(1) from e
    except httpx.HTTPStatusError as e:
        typer.echo(
            f"Mattermost webhook HTTP {e.response.status_code}",
            err=True,
        )
        raise typer.Exit(1) from e
    except httpx.RequestError as e:
        typer.echo(f"Mattermost webhook request failed: {e}", err=True)
        raise typer.Exit(1) from e


@app.command("export-diagnostics")
def export_diagnostics_command(
    trace_id: str = typer.Option(None, "--trace-id", help="Trace ID of the run to export"),
    out: str = typer.Option(
        ..., "--out", help="Directory where the diagnostic bundle will be written"
    ),
    date: str = typer.Option(None, "--date", help="Digest date to export if trace ID is unknown"),
    send_mm: bool = typer.Option(
        False, "--send-mm", help="Send a Mattermost notification for the bundle"
    ),
):
    """Export a redacted diagnostic bundle."""
    try:
        if not trace_id and not date:
            raise typer.BadParameter("Either --trace-id or --date is required")
        archive_path = export_diagnostics(
            trace_id=trace_id,
            out_dir=Path(out),
            date=date,
            send_mm=send_mm,
        )
        typer.echo(str(archive_path))
    except Exception as e:
        typer.echo(f"Error exporting diagnostics: {e}", err=True)
        sys.exit(1)


@app.command()
def setup(
    no_autodetect: bool = typer.Option(
        False,
        "--no-autodetect",
        help="Skip local autodetection (login, RealName, Keychain emails, network domains).",
    ),
):
    """Interactive setup: configure ActionPulse in ~7 core questions (plus optional steps), no text editor needed.

    On first run the wizard auto-detects the machine login, real name, corp
    email candidates (Keychain metadata scan, local-only) and network domains,
    pre-filling or auto-confirming validated values; everything detected is
    reviewed on the final summary screen before files are written.

    Generates:
      - ~/.config/actionpulse/env   (secrets, systemd-compatible)
      - configs/config.yaml         (pipeline config with your values)

    Safe to re-run — reads existing values as defaults.
    """
    from digest_core.setup_wizard import run_setup

    run_setup(no_autodetect=no_autodetect)


@app.command("eval-prompt", hidden=True)
def eval_prompt(
    digest: str = typer.Option(..., "--digest", help="Path to digest-YYYY-MM-DD.json to evaluate"),
    ingest_snapshot: str = typer.Option(
        None,
        "--ingest-snapshot",
        help="Path to ingest or LLM-replay snapshot for evidence_id cross-validation",
    ),
    output_json: str = typer.Option(
        None, "--output-json", help="Write JSON eval report to this file"
    ),
    show_changelog: bool = typer.Option(
        False, "--show-changelog", help="Print the prompt changelog and exit"
    ),
    prompt_file: str = typer.Option(
        None,
        "--prompt-file",
        help="Path to prompt txt file for changelog display (default: prompts/extract_actions.v1.txt)",
    ),
):
    """Evaluate a digest output for prompt quality (COMMON-12 iteration tooling).

    Scores the digest on evidence_id validity, confidence calibration,
    section assignment rules, and structural contract compliance.
    Returns exit code 0 (all OK) or 1 (errors found).

    Examples:

    \\b
        # Basic eval on a saved digest
        python -m digest_core.cli eval-prompt --digest out/digest-2026-03-31.json

        # With evidence_id validation using an ingest snapshot
        python -m digest_core.cli eval-prompt \\\\
            --digest out/digest-2026-03-31.json \\\\
            --ingest-snapshot /tmp/ews-snapshot.json

        # With LLM replay snapshot
        python -m digest_core.cli eval-prompt \\\\
            --digest out/digest-2026-03-31.json \\\\
            --ingest-snapshot /tmp/llm-replay.json

        # Show prompt changelog
        python -m digest_core.cli eval-prompt --show-changelog
    """
    from digest_core.eval.prompt_eval import evaluate_digest_file
    from digest_core.eval.changelog import (
        parse_prompt_changelog,
        format_changelog,
        get_current_version,
    )
    from digest_core.config import PROJECT_ROOT

    # Resolve default prompt file path
    if prompt_file:
        prompt_path = Path(prompt_file)
    else:
        prompt_path = PROJECT_ROOT / "prompts" / "extract_actions.v1.txt"

    # --show-changelog mode
    if show_changelog:
        if not prompt_path.exists():
            typer.echo(f"Prompt file not found: {prompt_path}", err=True)
            raise typer.Exit(1)
        versions = parse_prompt_changelog(prompt_path)
        typer.echo(f"Prompt: {prompt_path}")
        typer.echo(format_changelog(versions))
        raise typer.Exit(0)

    # Validate inputs
    digest_path = Path(digest)
    if not digest_path.exists():
        typer.echo(f"Digest file not found: {digest_path}", err=True)
        raise typer.Exit(1)

    snapshot_path = Path(ingest_snapshot) if ingest_snapshot else None
    if snapshot_path and not snapshot_path.exists():
        typer.echo(f"Snapshot file not found: {snapshot_path}", err=True)
        raise typer.Exit(1)

    # Run evaluation
    report = evaluate_digest_file(digest_path, ingest_snapshot_path=snapshot_path)

    # Print summary
    typer.echo(report.summary())

    # Optionally append prompt version from changelog
    if prompt_path.exists():
        current = get_current_version(prompt_path)
        if current:
            typer.echo(f"\nPrompt changelog current version: {current}")

    # Write JSON report if requested
    if output_json:
        out_path = Path(output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        typer.echo(f"\nJSON report written to: {out_path}")

    # Exit 1 if any errors found
    if report.errors:
        raise typer.Exit(1)


@app.command("eval-contract-parity", hidden=True)
def eval_contract_parity(
    snapshot: str = typer.Option(..., "--snapshot", help="Shared --dump-ingest snapshot"),
    baseline_recording: str = typer.Option(
        ..., "--baseline-recording", help="--record-llm capture taken with extract.contract=v1"
    ),
    candidate_recording: str = typer.Option(
        ..., "--candidate-recording", help="--record-llm capture taken with extract.contract=v3"
    ),
    digest_date: str = typer.Option(..., "--date", help="Digest date of the capture (YYYY-MM-DD)"),
    out_dir: str = typer.Option(None, "--out-dir", help="Working dir (default: a temp dir)"),
    json_out: str = typer.Option(None, "--json-out", help="Write the report as JSON"),
):
    """Compare the v1 and v3 extraction contracts over one captured snapshot (A1.6).

    The offline half of the A1.7 decision. A corp session only has to CAPTURE —
    one ingest snapshot plus one LLM recording per contract (brief task T3) — and
    this replays both here, forever, with no gateway access.

    Exit 0 when v3 is at parity, 2 when it regressed. Section redistribution and
    item counts the adapter accounts for are reported as differences/explained,
    not failures: v3 is *meant* to route differently and to drop items citing an
    evidence_id the pipeline never issued.
    """
    import tempfile

    from digest_core.eval.contract_parity import evaluate_parity

    work_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="contract-parity-"))
    report = evaluate_parity(
        snapshot=Path(snapshot),
        baseline_recording=Path(baseline_recording),
        candidate_recording=Path(candidate_recording),
        digest_date=digest_date,
        out_dir=work_dir,
    )
    typer.echo(report.render())
    if json_out:
        Path(json_out).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        typer.echo(f"report written to {json_out}")
    raise typer.Exit(0 if report.ok else 2)


@app.command("eval-replay", hidden=True)
def eval_replay(
    corpus_dir: str = typer.Option(
        None, "--corpus-dir", help="Corpus dir (default: digest_core/eval/corpus)"
    ),
    out_dir: str = typer.Option(
        None, "--out-dir", help="Working dir for produced digests (default: a temp dir)"
    ),
    update_baseline: bool = typer.Option(
        False, "--update-baseline", help="Write current metrics as the new committed baseline"
    ),
):
    """Replay the synthetic corpus and assert digest metrics vs the committed baseline.

    Offline and deterministic (no EWS/LLM). Exit code 0 (within tolerance / baseline
    updated) or 2 (a metric regressed). Wire into CI via `make eval-replay`.
    """
    import tempfile

    from digest_core.eval.corpus import CORPUS_DIR, load_corpus
    from digest_core.eval.replay_harness import evaluate_corpus

    cases = load_corpus(Path(corpus_dir) if corpus_dir else CORPUS_DIR)
    if not cases:
        typer.echo("No corpus cases found.", err=True)
        raise typer.Exit(1)

    work_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="eval-replay-"))
    ok, reports = evaluate_corpus(cases, work_dir, update_baseline=update_baseline)

    for report in reports:
        typer.echo(f"\n[{report['case']}] {json.dumps(report['metrics'], ensure_ascii=False)}")
        if report.get("updated_baseline"):
            typer.echo("  baseline updated")
        elif report.get("ok"):
            typer.echo("  OK")
        else:
            for regression in report.get("regressions", []):
                typer.echo(f"  REGRESSION: {regression}", err=True)

    if update_baseline:
        raise typer.Exit(0)
    raise typer.Exit(0 if ok else 2)


@app.command("eval-gold", hidden=True)
def eval_gold(
    reactions: str = typer.Option(..., "--reactions", help="Exported MM reactions JSONL"),
):
    """Ingest an exported Mattermost reactions JSONL into a gold-set and print stats."""
    from digest_core.eval.gold_set import load_gold_jsonl

    path = Path(reactions)
    if not path.exists():
        typer.echo(f"Reactions file not found: {path}", err=True)
        raise typer.Exit(1)
    gold = load_gold_jsonl(path)
    typer.echo(json.dumps(gold.stats(), ensure_ascii=False))


@app.command("eval-judge", hidden=True)
def eval_judge(
    records: str = typer.Option(
        ..., "--records", help="Judged records JSONL (predicted/gold/prob/lang)"
    ),
):
    """Compute per-stratum judge P/R/F1, hallucination rate, and Brier from records."""
    from digest_core.eval.judge import compute_judge_metrics

    path = Path(records)
    if not path.exists():
        typer.echo(f"Records file not found: {path}", err=True)
        raise typer.Exit(1)
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    typer.echo(json.dumps(compute_judge_metrics(rows), ensure_ascii=False, indent=2))


@app.command("eval-best-of-n", hidden=True)
def eval_best_of_n(
    corpus_dir: str = typer.Option(
        None, "--corpus-dir", help="Corpus dir (default: digest_core/eval/corpus)"
    ),
    out: str = typer.Option(None, "--out", help="Write the proof report JSON here"),
):
    """EP-10 offline proof: the gate-as-selector never loses to single-shot.

    Builds controlled candidate sets over the frozen replay corpus and asserts
    support-recall(selected) >= support-recall(N=1) per case. Offline and
    deterministic (no LLM). Exit 0 (proof holds) or 2 (selector regressed).
    Live sampling quality requires corp validation (EP-14).
    """
    from digest_core.eval.best_of_n_harness import evaluate_corpus_best_of_n
    from digest_core.eval.corpus import CORPUS_DIR, load_corpus

    cases = load_corpus(Path(corpus_dir) if corpus_dir else CORPUS_DIR)
    if not cases:
        typer.echo("No corpus cases found.", err=True)
        raise typer.Exit(1)

    ok, reports = evaluate_corpus_best_of_n(cases)
    payload = json.dumps(reports, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(payload, encoding="utf-8")
        typer.echo(f"Proof report written to {out}")
    typer.echo(payload)
    typer.echo(
        (
            "PROOF OK: selected candidate never loses to N=1."
            if ok
            else "PROOF FAILED: a case selected a worse candidate than N=1."
        ),
        err=True,
    )
    raise typer.Exit(0 if ok else 2)


@app.command("eval-judge-run", hidden=True)
def eval_judge_run(
    digest: str = typer.Option(..., "--digest", help="Digest artifact JSON to judge"),
    gold: str = typer.Option(..., "--gold", help="Exported MM reactions JSONL (gold labels)"),
    mode: str = typer.Option(
        None, "--mode", help="Judge architecture override (default: eval.judge_mode from config)"
    ),
    out: str = typer.Option(None, "--out", help="Write judge records JSONL here"),
):
    """Run the reference-anchored judge over a digest vs gold rows (EP-5 step 3, D5).

    Calibration records (judge-vs-human κ/α via the agreement block) plus a
    regression report against human-approved exemplars. REPORT-ONLY: exit 0 on
    success regardless of metrics — nothing gates CI until reactions-based
    calibration clears κ >= 0.41 with the CI floor (D2/EP-15). Requires the
    corp gateway (judge model) — offline runs fail fast with a clear error.
    """
    from digest_core.config import Config
    from digest_core.eval.gold_set import load_gold_jsonl
    from digest_core.eval.judge import JUDGE_MODES, make_judge, reference_eval
    from digest_core.llm.gateway import LLMGateway
    from digest_core.llm.rate_broker import RateBroker

    digest_path, gold_path = Path(digest), Path(gold)
    for path, label in ((digest_path, "Digest"), (gold_path, "Gold")):
        if not path.exists():
            typer.echo(f"{label} file not found: {path}", err=True)
            raise typer.Exit(1)

    config = Config()
    judge_mode = (mode or config.eval.judge_mode).strip().lower()
    if judge_mode not in JUDGE_MODES:
        typer.echo(f"Unknown judge mode '{judge_mode}' (expected one of {JUDGE_MODES})", err=True)
        raise typer.Exit(1)
    if judge_mode != "reference":
        typer.echo(
            "eval.judge_mode is 'pointwise' — the advisory dashboard path (use eval-judge"
            " over pre-judged records). eval-judge-run implements the reference-anchored"
            " architecture (D5); flip eval.judge_mode or pass --mode reference.",
            err=True,
        )
        raise typer.Exit(1)

    gold_set = load_gold_jsonl(gold_path)
    if not len(gold_set):
        typer.echo("Gold set is empty (no usable reactions).", err=True)
        raise typer.Exit(1)

    # The judge rides the gateway with a model override (R1) on its own RPM
    # bucket and stage budget; broker limits match the live run's ceilings.
    judge_llm = config.llm.model_copy(update={"model": config.judge.model})
    broker = RateBroker.from_config(
        config.llm, stage_call_budgets={"judge": max(len(gold_set) * 2, 8)}
    )
    gateway = LLMGateway(judge_llm, rate_broker=broker, stage="judge")
    try:
        digest_json = json.loads(digest_path.read_text(encoding="utf-8"))
        report = reference_eval(digest_json, gold_set, make_judge(judge_mode, gateway))
    except Exception as exc:  # corp-only endpoint: offline runs land here
        typer.echo(
            f"Judge run failed ({type(exc).__name__}): the judge model needs the corp"
            " gateway — run inside the corp network (EP-14).",
            err=True,
        )
        raise typer.Exit(1)
    finally:
        gateway.close()

    if out:
        Path(out).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in report["records"]) + "\n",
            encoding="utf-8",
        )
        typer.echo(f"Judge records written to {out}")
    typer.echo(
        json.dumps(
            {k: v for k, v in report.items() if k != "records"}, ensure_ascii=False, indent=2
        )
    )
    agreement = (report.get("metrics") or {}).get("agreement")
    if agreement:
        typer.echo(agreement["verdict"], err=True)
    typer.echo(
        "Report-only: the no-gate rule holds until κ >= 0.41 with the CI floor (EP-15).",
        err=True,
    )


@app.command("eval-agreement", hidden=True)
def eval_agreement(
    labels: str = typer.Option(..., "--labels", help="CSV with human and judge label columns"),
    human_col: str = typer.Option("human", "--human", help="Human-label column name"),
    judge_col: str = typer.Option("judge", "--judge", help="Judge-label column name"),
    bootstrap: int = typer.Option(2000, "--bootstrap", help="Bootstrap iterations for the κ CI"),
    seed: int = typer.Option(42, "--seed", help="Bootstrap seed (fixed for reproducibility)"),
):
    """Cohen's κ / Krippendorff's α between human and judge labels (EP-5).

    Drift trackers and the may-gate floor (κ ≥ 0.41), NOT the gate itself —
    the gate architecture is an open owner decision (ENHANCEMENT_PROGRAM.md D5).
    Deterministic for a fixed seed; offline.
    """
    from digest_core.eval.agreement import compute_agreement, read_pairs_csv

    path = Path(labels)
    if not path.exists():
        typer.echo(f"Labels file not found: {path}", err=True)
        raise typer.Exit(1)
    try:
        pairs = read_pairs_csv(path, human_col, judge_col)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    if not pairs:
        typer.echo("No usable rows (all missing?)", err=True)
        raise typer.Exit(1)
    report = compute_agreement(pairs, bootstrap=bootstrap, seed=seed)
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    typer.echo(report["verdict"], err=True)


@app.command("eval-calibrate", hidden=True)
def eval_calibrate(
    scored: str = typer.Option(..., "--scored", help="JSONL of {score, gold, lang} rows"),
    output_json: str = typer.Option(None, "--out", help="Write calibration.json here"),
    target_recall: float = typer.Option(0.90, "--target-recall"),
    min_samples: int = typer.Option(20, "--min-samples"),
):
    """Calibrate per-stratum tau at the target recall and emit calibration.json."""
    from digest_core.eval.calibrate import calibrate

    path = Path(scored)
    if not path.exists():
        typer.echo(f"Scored file not found: {path}", err=True)
        raise typer.Exit(1)
    strata: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        strata.setdefault((row.get("lang") or "ru").lower(), []).append(
            (float(row["score"]), bool(row["gold"]))
        )
    result = calibrate(strata, target_recall=target_recall, min_samples=min_samples)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output_json:
        Path(output_json).write_text(payload, encoding="utf-8")
        typer.echo(f"Calibration written to {output_json}")
    typer.echo(payload)


if __name__ == "__main__":
    app()
