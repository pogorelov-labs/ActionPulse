# digest-core

Python 3.11 package. Daily email digest pipeline: EWS → normalize → threads → evidence → LLM → assemble → deliver (Mattermost incoming webhook).

## Commands

```bash
# Git preflight
git fetch origin --prune
git status --short --branch

# Setup — canonical: interactive wizard (7 questions, no text editor)
make setup                           # uv sync --native-tls + uv run python -m digest_core.cli setup
actionpulse                          # Global command (after install): bare = interactive menu
actionpulse run --dry-run            # Subcommands work too; secrets auto-loaded from ~/.config/actionpulse/env
uv run python -m digest_core.cli setup  # Re-run wizard (reads existing values as defaults)
uv run python -m digest_core.cli setup --no-autodetect  # Skip local autodetection (Keychain/dscl/DNS)
uv sync --native-tls                 # Deps only, no wizard (headless / CI)
# Fresh Mac, one-liner (repo-root install.sh: uv + clone + sync + wizard):
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)"

# Development
make test                            # Run pytest (all mocked, no network needed)
make lint                            # Ruff + black
make format                          # Auto-fix lint issues
make smoke                           # Smoke test (dry-run)
make clean                           # Remove __pycache__, .pytest_cache, etc.

# Run
python -m digest_core.cli run                          # Full run (today, EWS + LLM + MM delivery)
python -m digest_core.cli run --dry-run                # Ingest + normalize only, no LLM
python -m digest_core.cli run --from-date 2026-03-28   # Specific date
python -m digest_core.cli run --window rolling_24h     # Last 24h instead of calendar day
python -m digest_core.cli run --out /tmp/digest --state /tmp/state  # Custom paths
python -m digest_core.cli diagnose                     # Environment diagnostics

# Docker
make docker                           # Build image
make docker-run                       # Run with env vars and volume mounts
```

## Architecture (8-stage pipeline)

```
1.INGEST (ews.py) → 2.NORMALIZE (html.py, quotes.py) → 3.THREADS (build.py)
→ 4.EVIDENCE (split.py, BUDGET OWNER ≤3000 tokens) → 5.SELECT (context.py)
→ 6.LLM (gateway.py, qwen35-397b-a17b, max 2 calls/run) → 7.ASSEMBLE (jsonout.py, markdown.py)
→ 8.DELIVER (mattermost.py, webhook/bot)
```

Full contracts in `docs/ARCHITECTURE.md §4`.

## Key Files

| File | Purpose |
|------|---------|
| `src/digest_core/cli.py` | Typer CLI (`run`, `diagnose`, `export-diagnostics`, replay/dump flags) |
| `src/digest_core/run.py` | Pipeline orchestration (unified path; dry-run, MM delivery, partial digest) |
| `src/digest_core/config.py` | Pydantic config; YAML merged into models without clobbering ENV |
| `src/digest_core/llm/gateway.py` | LLM HTTP client (JSON retry, 429/5xx retry, rate-limit spacing) |
| `src/digest_core/llm/schemas.py` | Pydantic output schemas: Digest, Section, Item |
| `prompts/extract_actions.v1.txt` | RU extraction prompt (plain text, not Jinja2) |
| `prompts/extract_actions.en.v1.txt` | EN extraction prompt |
| `src/digest_core/deliver/mattermost.py` | Mattermost incoming webhook delivery |
| `src/digest_core/diagnostics.py` | Diagnostic bundle export (`export-diagnostics`) |
| `configs/config.example.yaml` | Reference config |
| `docs/ARCHITECTURE.md` | **Source of truth** — contracts & roadmap (§13 may lag vs code; verify in tests) |

## Code Style

- Python 3.11, ruff (line-length=100), black, isort
- Typer for CLI, httpx for HTTP, structlog for JSON logs, pydantic for validation
- Prefer small testable modules. Each pipeline stage = separate file.
- Terminal output (CLI, wizard, future live displays) follows `../docs/development/TERMINAL_DESIGN.md`; roadmap & reviewer checklist: `../docs/development/TERMINAL_DESIGN_ROADMAP.md` + CONTRIBUTING.md.

## Testing

```bash
make test    # All tests use mocks, run anywhere
```

- Tests in `tests/test_*.py` (40+ modules; `make test` is the checklist)
- Fixtures in `tests/fixtures/emails/` (10 email samples)
- Mock LLM in `tests/mock_llm_gateway.py`
- **Real EWS/LLM tests**: corp network only. Use replay mode for offline dev.

## Branching Preflight

- Before any edits, confirm the branch is based on current `origin/main`.
- If `git status --short --branch` shows detached `HEAD`, stop and create a real branch first.
- If this worktree cannot fetch, use a fresh clone/worktree inside the writable workspace rather than continuing on stale git state.
- Only move Plane issues or open a PR after the branch base and `make test` baseline are verified on that branch.

## CLI Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success — full run or `--dry-run` completed without errors |
| `1` | Error — unhandled exception, missing required ENV, pipeline crash, `KeyboardInterrupt` |
| `2` | Citation gate failed — **`--validate-citations` is default ON (PR11)**; exit 2 fires only when **measured support recall < `reranker.recall_floor`** (default `0.0` → never, until a real floor is set from PR10 calibration). A single bad citation or a `weak_evidence` item no longer trips exit 2 (degrade-not-drop). Use `--no-validate-citations` to opt out. `RunDigestResult` exposes `support_recall`/`recall_floor`/`items_weak`/`items_repaired`. |

`--dry-run` exits `0` (not `2`) — it is a complete success for its stated purpose (ingest + normalize only).

**Programmatic API:** `run_digest(...)` returns **`RunDigestResult`**, not `bool`. Use `if run_digest(...):` or `assert run_digest(...)` for pipeline success; inspect `.citation_validation_ok` when using `--validate-citations`.

## Gotchas

- **CLI from repo root**: Top-level `digest_core/` package extends the path into `digest-core/src`; use `python3 -m digest_core.cli` from the monorepo root or `cd digest-core` and the same module name.
- **Setup auto-CA behavior**: wizard first auto-detects CA in `configs/config.yaml`, `/etc/ssl/corp-ca.pem`, `~/.ssl/corp-ca.pem`, `./certs/corp-ca.pem`; on macOS it can export Root CA from Keychain into `~/.ssl/corp-ca.pem`.
- **EWS TLS is per-session (PR3)**: `ews.verify_ssl=false` now disables verification **for EWS only** (via exchangelib's `BaseProtocol.SSL_CONTEXT`). It no longer monkey-patches `requests`/`httpx` process-globally, so the LLM gateway and Mattermost httpx clients always verify on their own. Consequence: if you run with `verify_ssl=false` and the **corp LLM gateway or Mattermost certs are not CA-trusted**, those calls will now fail (previously they silently rode the EWS bypass). Trust the corp CA (`verify_ca` / system store) rather than disabling EWS verification.
- **Dry-run still hits EWS** unless you pass `--replay-ingest <snapshot.json>`; missing/invalid EWS env fails fast with a clear error.
- **NormalizedMessage naming**: Output of Stage 1 (INGEST) is named `NormalizedMessage` but body is still raw HTML. Actual normalization happens in Stage 2. Don't be confused.
- **Idempotency** (config+content aware, PR1): a run skips only when nothing relevant changed. Two skip paths, both bypassed by `run --force`: (1) **pre-ingest** — fresh artifacts (<48h) **and** matching `config_sha256` + `pipeline_version` (cheap, no EWS); (2) **post-ingest** — `config_sha256` + `content_sha256` + `pipeline_version` all match (skips the scarce extractor LLM call when ingested content is unchanged, even past 48h). State lives in a `digest-{date}.idem.json` sidecar next to the artifacts. A config or pipeline-version change always forces a rebuild.
- **Stale LLM recordings (pre-PR1)**: any `--record-llm` capture made before PR1 is unusable — it stored `uuid4()` `evidence_id`s that won't match the new deterministic content-hash ids, so every item is dropped on replay (empty digest). **Re-record inside the corp network** after PR1. Recordings are now request-keyed (`request_hash` per entry); legacy files without it still replay positionally.
- **Token estimation**: `words * 1.3` approximation, NOT tiktoken. Off by ~10% but fine for typical `context_budget.max_total_tokens` (default 7000).
- **LLM timeout**: Default `timeout_s` is 120s for qwen35-397b-a17b (see `LLMConfig`).
- **Extraction prompts**: `extract_actions*.txt` are plain text (ADR-009). Some `.j2` summarize paths remain registered in `llm/prompt_registry.py` but are unused by `run.py`.
- **`hierarchical/` was deleted** (cleanup PR): the dormant per-thread-summarize→aggregate processor violated ADR-002 and would exhaust the 15 RPM cap. Its good ideas (must-include chunks, merge-with-citations, skip-if-empty) live in the P2 gate (PR8) and fused scoring (PR9). The dormant `HierarchicalConfig` in `config.py` is now dead config (left as a harmless no-op; safe to remove later).

## Environment Variables

```bash
# Required
EWS_PASSWORD=...          # Exchange NTLM password
LLM_TOKEN=...             # LLM Gateway bearer token

# Required for MM delivery
MM_WEBHOOK_URL=...        # Mattermost incoming webhook URL

# Optional
DIGEST_REPORT_LANGUAGE=ru # Report language override (default en)
DIGEST_CONFIG_PATH=...    # Custom config YAML path
ACTIONPULSE_CA_CERT_NAME=...  # macOS setup helper: Keychain certificate alias for auto-export
# Output/state: use CLI flags --out and --state (not separate DIGEST_* env vars in current code)
```

## Offline Development (outside corp network)

EWS and LLM Gateway are only accessible from corp network.

```bash
# Inside corp: capture snapshot
python -m digest_core.cli run --dump-ingest /tmp/ews-snapshot.json

# Outside corp: replay without EWS
python -m digest_core.cli run --replay-ingest /tmp/ews-snapshot.json

# Diagnostics: export and send via MM
python -m digest_core.cli export-diagnostics --trace-id <id> --send-mm
```

## Active Tech Debt

Phase 0 hardening (prompts, path resolution, config precedence, LLM retry/degradation, Mattermost delivery, replay/diagnostics, E2E tests) is implemented on `main` as of 2026-03.

Authoritative checklist: `docs/ARCHITECTURE.md §13`. The table was reconciled with the codebase 2026-04-06 — see ACTPULSE-61 (PR #34) for the broad sweep and ACTPULSE-62 (PR #35) which closed TD-003 after verifying that `config.py` `_merge_model()` honors both `env_field_map` and `env_prefix` for every nested section. Still verify behavior with `make test` and source before treating any §13.2 row as currently open.
