# ActionPulse

Daily corporate email digest with LLM-powered action extraction. Processes Exchange inbox, extracts actionable items via LLM, delivers a structured digest to **Mattermost** (incoming webhook to the configured channel; Bot API optional in later phases).

**Single-tenant CLI tool.** One user, one inbox, daily cron.

## Quick Start

```bash
# 1. Clone & install
git clone https://github.com/pogorelov-labs/ActionPulse.git
cd ActionPulse/digest-core
uv sync --native-tls

# 2. Interactive setup (6 questions, generates env + config.yaml)
python -m digest_core.cli setup

# 3. Verify
set -a && source ~/.config/actionpulse/env && set +a
python -m digest_core.cli diagnose
```

Or as a single command: `make setup` (installs deps + runs interactive setup).

## Usage

```bash
# Load secrets (once per shell session)
set -a && source ~/.config/actionpulse/env && set +a

# Full digest for today (EWS + LLM + Mattermost delivery)
python -m digest_core.cli run

# Dry-run: ingest + normalize only, no LLM calls
python -m digest_core.cli run --dry-run

# Force rebuild (ignore 48h idempotency window)
python -m digest_core.cli run --force

# Specific date
python -m digest_core.cli run --from-date 2026-03-28

# Capture EWS snapshot for offline replay
python -m digest_core.cli run --dry-run --dump-ingest /tmp/snapshot.json

# Replay without EWS access
python -m digest_core.cli run --replay-ingest /tmp/snapshot.json

# Record + replay LLM responses
python -m digest_core.cli run --record-llm /tmp/llm.json
python -m digest_core.cli run --replay-ingest /tmp/snapshot.json --replay-llm /tmp/llm.json

# Evaluate prompt quality
python -m digest_core.cli eval-prompt --digest out/digest-2026-03-28.json

# Test Mattermost webhook
python -m digest_core.cli mm-ping
```

## Output

Each run produces in `./out/`:
- `digest-YYYY-MM-DD.json` — structured digest (sections, items, evidence_ids)
- `digest-YYYY-MM-DD.md` — markdown for Mattermost
- `trace-*.meta.json` — run metadata (timing, LLM stats, config)

## Deployment

```bash
# Install systemd timer (daily at 08:00)
bash deploy/install-systemd.sh

# Or use cron
crontab -l  # see deploy/crontab.example
```

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for Docker and production setup.

## Development

```bash
make test       # ~526 tests, all mocked
make lint       # ruff + black
make format     # auto-fix
make smoke      # dry-run smoke test
```

## Architecture

8-stage pipeline: `INGEST -> NORMALIZE -> THREADS -> EVIDENCE -> SELECT -> LLM -> ASSEMBLE -> DELIVER`

Full specification: [ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Network Requirements

| Service | Network | Used for |
|---------|---------|----------|
| Exchange (EWS) | Corp only | Email ingest |
| LLM Gateway | Corp only | Action extraction |
| Mattermost | Anywhere | Digest delivery |

Offline development via `--dump-ingest` / `--replay-ingest`. See [Corp Session Runbook](docs/CORP_SESSION_RUNBOOK.md).

## Documentation

- [Architecture & Contracts](docs/ARCHITECTURE.md) — single source of truth
- [Corp Session Runbook](docs/CORP_SESSION_RUNBOOK.md) — first real run instructions
- [Deployment Guide](docs/DEPLOYMENT.md) — Docker, systemd, cron
