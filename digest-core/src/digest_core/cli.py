import json
import os
import shutil
import subprocess
import sys
import typer
from pathlib import Path

import httpx

from digest_core.diagnostics import export_diagnostics, _build_env_info
from digest_core.deliver.mattermost import ping_mattermost_webhook
from digest_core.run import run_digest, run_digest_dry_run
from digest_core.observability.logs import setup_logging
from digest_core.config import Config

app = typer.Typer(add_completion=False)


@app.command()
def run(
    from_date: str = typer.Option(
        "today", "--from-date", help="Date to process (YYYY-MM-DD or 'today')"
    ),
    sources: str = typer.Option(
        "ews", "--sources", help="Comma-separated source types (e.g., 'ews')"
    ),
    out: str = typer.Option("./out", "--out", help="Output directory path"),
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
):
    """Run daily digest job."""
    try:
        # Setup logging
        setup_logging(log_level=log_level, log_file=log_file)

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
            )

            # Exit with code 2 if citation validation failed
            if validate_citations and not run_result.citation_validation_ok:
                typer.echo("⚠ Citation validation failed", err=True)
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
                    typer.echo("✓ Diagnostics collected successfully")
                else:
                    typer.echo("⚠ Diagnostics script not found", err=True)
            except Exception as e:
                typer.echo(f"⚠ Failed to collect diagnostics: {e}", err=True)

        sys.exit(exit_code)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)  # Error


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
                mark = "✓" if value else "✗"
                typer.echo(f"  {mark} {var}: {status}")
            typer.echo("")
            typer.echo("Tools:")
            for tool in ("uv", "docker", "pytest", "ruff"):
                path = shutil.which(tool)
                mark = "✓" if path else "✗"
                typer.echo(f"  {mark} {tool}: {path or 'not found'}")
            typer.echo("")
            typer.echo(
                "Note: full shell-based diagnostics require a git checkout (digest-core/scripts/)."
            )

        typer.echo("✓ Diagnostics completed")

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
def setup():
    """Interactive setup: configure ActionPulse in 6 questions, no text editor needed.

    Generates:
      - ~/.config/actionpulse/env   (secrets, systemd-compatible)
      - configs/config.yaml         (pipeline config with your values)

    Safe to re-run — reads existing values as defaults.
    """
    from digest_core.setup_wizard import run_setup

    run_setup()


@app.command("eval-prompt")
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


@app.command("eval-replay")
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


@app.command("eval-gold")
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


@app.command("eval-judge")
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


@app.command("eval-calibrate")
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
