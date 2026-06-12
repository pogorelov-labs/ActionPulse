# ActionPulse Deployment Guide

## Prerequisites

- Python 3.11+ with `uv` package manager
- Access to corp network (EWS + LLM Gateway)
- Mattermost incoming webhook URL

## 1. Installation

```bash
cd digest-core
uv sync --native-tls
```

## 2. Configuration

### Interactive (recommended)

```bash
python -m digest_core.cli setup
```

7 questions: corp email, EWS endpoint, EWS password, LLM endpoint, LLM token, Mattermost webhook, report language (`en` default / `ru`). Auto-derives `user_login`, `user_domain`, and name aliases from the email address. Writes `~/.config/actionpulse/env` (chmod 600, systemd-compatible) and `configs/config.yaml`. Safe to re-run — existing values are used as defaults. Secrets are hidden on input with confirmation prompts.

Or run `make setup` from `digest-core/` to chain `uv sync --native-tls` with the wizard in one command.

### Manual (fallback for headless / CI / no-TTY)

If no interactive TTY is available (CI seeds, pre-provisioned systemd units):

```bash
mkdir -p ~/.config/actionpulse
cp deploy/env.example ~/.config/actionpulse/env
chmod 600 ~/.config/actionpulse/env
# Edit with real values; required: EWS_PASSWORD, LLM_TOKEN, MM_WEBHOOK_URL

cp configs/config.example.yaml configs/config.yaml
# Edit EWS endpoint, user_upn, user_domain, aliases, timezone
```

Required environment variables: `EWS_PASSWORD`, `LLM_TOKEN`, `MM_WEBHOOK_URL`.

## 3. Manual test run

```bash
source ~/.config/actionpulse/env
python -m digest_core.cli run --from-date today --sources ews --dry-run
```

## 4. Daily schedule

### Option A: systemd timer (recommended for Linux)

```bash
./deploy/install-systemd.sh
```

This installs a user-level systemd timer that runs at 08:00 daily.

**Verify:**
```bash
systemctl --user status actionpulse-digest.timer
systemctl --user list-timers
```

**Manual trigger:**
```bash
systemctl --user start actionpulse-digest@$(whoami).service
```

**View logs:**
```bash
journalctl --user -u actionpulse-digest@$(whoami) -f
```

**Change schedule:** edit `~/.config/systemd/user/actionpulse-digest.timer`, then:
```bash
systemctl --user daemon-reload
systemctl --user restart actionpulse-digest.timer
```

### Option B: crontab (any Unix)

```bash
crontab -l > /tmp/crontab.bak  # backup
cat deploy/crontab.example >> <(crontab -l)  # or edit manually
crontab -e  # paste the line from deploy/crontab.example
```

Default: weekdays at 08:00. Logs to `~/actionpulse-cron.log`.

### Option C: Docker

```bash
make docker
make docker-run  # one-shot; combine with host cron/systemd for scheduling
```

## 5. CI Pipeline

GitHub Actions workflow (`.github/workflows/ci.yml`) runs on push/PR to `main`:

1. **lint** — ruff + black format check
2. **test** — pytest (all 463 tests, mocked, no network)
3. **docker** — build image (after lint + test pass)

## 6. Monitoring

- Prometheus metrics: port 9108
- Health check: port 9109 (`/healthz`, `/readyz`)
- Structured logs: JSON via structlog
- Digest artifacts: `out/digest-YYYY-MM-DD.json` + `.md`

## 7. Troubleshooting

```bash
# Environment check
python -m digest_core.cli diagnose

# Export diagnostic bundle
python -m digest_core.cli export-diagnostics --trace-id <id>

# Replay without EWS (offline)
python -m digest_core.cli run --replay-ingest snapshot.json
```
