# ActionPulse — digest-core

> **This file is a pointer.** The user-facing overview lives in the **[repo-root README](../README.md)**;
> the canonical developer reference is **[CLAUDE.md](CLAUDE.md)** (commands, exit codes, gotchas) and
> **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** (the source of truth for the pipeline, contracts, and
> ADRs). The old quick-start that lived here drifted — it predated the `actionpulse` launcher, the
> encrypted store, `search`/`ask`, Mattermost ingest, and the MCP server — so it was reduced to this
> redirect rather than maintained in parallel (the `TECHNICAL.md` precedent).

The Python package (`digest_core`) is the product. Setup is the interactive wizard:

```bash
cd digest-core && make setup      # uv sync + the wizard → ~/.config/actionpulse/env + configs/config.yaml
# then, from anywhere:
actionpulse                        # interactive menu (run · read · diagnose · store · mcp · settings)
actionpulse run                    # full digest; --dry-run / --from-date / --force
```

- **Sources:** Exchange (EWS) always; Mattermost @-mentions / allowlisted channels / DMs optionally
  (`--sources ews,mm`, `MM_PAT`, `mm_source.dm_scope` — consent-gated, default `off`).
- **Optional encrypted store** (`uv sync --extra store` + `actionpulse store init`): `search` / `ask`,
  cross-day open-loops / pending sections, and the **MCP server** (`uv sync --extra mcp`,
  `actionpulse mcp install`).
- **Network:** EWS + LLM gateway are corp-only; Mattermost delivery works anywhere. Offline dev via
  `--dump-ingest` / `--replay-ingest` (ADR-012).

See [CLAUDE.md](CLAUDE.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · [docs/RUNBOOK.md](docs/RUNBOOK.md).
