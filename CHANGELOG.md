# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Terminal Design System** (`docs/development/TERMINAL_DESIGN.md`): research-grounded rules for every terminal surface — semantic color tokens + env contract pinned to rich 14.3.3 behavior (`NO_COLOR` > `FORCE_COLOR`; `FORCE_COLOR=0` forces ON), motion budgets (one brand spinner, 4–10 fps Live, 30 fps ceiling, amber-after-10s), the split-region live work display spec for `cli run` and the fleet (history funnel lines + one ≤8-line animated footer, lane cap 4, `ProgressSink` event seam, `--progress=live|plain|none`), input model (arrows/j-k/Esc-cancels-question; **no mouse** on line-oriented surfaces — evidence-backed), truncation and resize-honesty rules, full degradation matrix. Every rule carries an evidence tier (★ adversarially verified / ◐ sourced / ◆ house rule); provenance: deep-research run over 24 primary sources + 3 targeted source-reading passes (cargo, uv, BuildKit, Claude Code, opencode, terraform, bazel, gh, 8 prompt libraries, rich 14.3.3 wheel).
- Wizard post-save **live Mattermost check**: offers to send a fixed test message to the incoming webhook — the one endpoint reachable from outside the corp perimeter (ADR-012). TTY-gated, so piped/scripted runs keep a stable answer protocol; failures are soft (hint to re-check the URL). When the detected machine (AD) login differs from the email local part, the env file gains a commented `EWS_USER_LOGIN=<login>` override next to the credentials — the classic NTLM failure cause, fix at hand where the user looks first. `mm-ping` added to the wizard's next-steps panel.
- Setup wizard **local autodetection** (skip with `setup --no-autodetect`): machine login, RealName (gecos/`dscl`), corp-email candidates from a Keychain metadata scan (the file's unencrypted labels only — keychain secrets stay encrypted; candidates are never logged or persisted), AD/DNS-search domain hints, EWS host from `login@host` artifacts, and a short DNS reachability probe. Cross-validated values are auto-confirmed (email matching the real name + domain, EWS endpoint with DNS ✓); weaker findings become prompt defaults. The UPN is *discovered*, never synthesized from the name (local parts are not always `name.surname`). The final summary still gates every write; declining it re-asks all questions with detected values as defaults.
- **One-command bootstrap** for fresh macOS: repo-root `install.sh` (`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)"`) checks macOS/git (tarball fallback without CLT), installs `uv` (astral.sh installer with GitHub-releases fallback), clones the repo, runs `uv sync --native-tls` with plain-sync fallback, and hands off to the setup wizard **in the same terminal session**; animated step UI degrading to plain output (non-TTY / `NO_COLOR` / non-UTF-8); flags `--dir`, `--ref`, `--no-wizard`; idempotent re-runs (`git pull --ff-only`). `digest-core/.python-version` pins Python 3.11 so `uv` provisions the interpreter regardless of the system Python.
- [`MIGRATION.md`](./MIGRATION.md) at repo root — clarifies V2→V3 field removals vs the default `digest_core.cli run` output (`Digest` schema `1.0` + `extract_actions` prompts).
- **`RunDigestResult`** from `run_digest()` (`pipeline_succeeded`, `citation_validation_ok`); CLI exit **2** when `--validate-citations` and post-LLM citation build/validation fails; `trace-*.meta.json` includes `citation_validation_ok` ([PR #43](https://github.com/ruspg/ActionPulse/pull/43)).
- Post-LLM **citation pass** (`CitationBuilder` / `CitationValidator`) when `validate_citations` is set; metric `citation_validation_failures_total` on failure (`post_llm_offsets`).
- Optional **`DigestRanker`** in `run.py` when **`ranker.enabled`**; `Item.rank_score`; `rank_items` uses `model_copy` for Pydantic models.

### Changed
- `CORP_SESSION_RUNBOOK.md`: §1 leads with the fresh-Mac one-liner (replaces §1.1+§1.2+§0.3 in one command); new **§8 — one-time corp validation checklist** for the bootstrap + autodetection (one-liner behind the corp proxy, UPN pick correctness, NTLM email-local vs `EWS_USER_LOGIN`, Keychain CA chain, MM live-check, full run); quick-ref checklist updated.
- Setup wizard restyled with **`rich`** (promoted to an explicit dependency): gradient banner, per-step rules, input validation with re-prompting instead of exiting, masked secrets with confirmation and **Enter-keeps-existing** on re-runs, review panel before any file is written, Keychain CA export under a spinner. Tested helpers and generated file formats unchanged; Ctrl+C exits 130 without writing. Fresh-install defaults no longer read `config.example.yaml` placeholders (they shadowed the EWS endpoint derived from the user's email). Quick-start docs lead with the one-liner; clone URLs migrated `ruspg` → `pogorelov-labs`; `QUICK_START.md` uses `uv run` instead of `python3.11`.
- Interactive setup wizard via **`make setup`** or **`python -m digest_core.cli setup`** (from `digest-core/`) — 6 questions, 0 text editors. `make setup` runs `uv sync` then the same wizard. Generates `~/.config/actionpulse/env` (chmod 600, systemd-compatible) and `configs/config.yaml`. Safe to re-run (PR #32).
- All setup documentation now points at the interactive wizard as the canonical path; manual `cp deploy/env.example` kept only as an explicit headless / CI fallback (ACTPULSE-60).
- Consolidated all utility scripts under `digest-core/scripts/` and refreshed documentation links.
- Reconciled docs vs code: corrected `max LLM calls per run` (1 → 2) in `README.md` and `ARCHITECTURE.md` diagram; rewrote `ARCHITECTURE.md` ADR-009 prose in past tense; converted `docs/development/TECHNICAL.md` to a redirect to the SoT; added status banner to `docs/planning/MATTERMOST_INTEGRATION.md` clarifying that bot/multi-channel features are not yet implemented; corrected `TROUBLESHOOTING.md` env-file path (`~/.config/actionpulse/env`) (ACTPULSE-61).
- Added "Phase 1+ design — not yet implemented" status banners to `docs/reference/COST_MANAGEMENT.md`, `docs/reference/KPI.md`, and `docs/reference/QUALITY_METRICS.md`, distinguishing instrumented metrics (per `observability/metrics.py` and `ARCHITECTURE.md §6.1`) from aspirational quality KPIs and budget enforcement. Added historical banner to `digest-core/docs/PHASE0_PROMPT.md` and an "illustrative — not actual signatures" banner to `docs/development/CODE_EXAMPLES.md` (ACTPULSE-62).
- **Closed TD-003** in `ARCHITECTURE.md`: every nested-config section in `config.py` has both `env_field_map` and `env_prefix`, so every field has a valid `DIGEST_<PREFIX>_<FIELD>` ENV-override path. Moved from §13.2 (open) to §13.1 (done) and tightened §5.2 prose accordingly (ACTPULSE-62).
- Archived historical implementation reports in `docs/legacy/` for easier navigation. The 2026-04-06 sweep added `E2E_TESTING_GUIDE.md`, `IMPLEMENTATION_SUMMARY.md`, `DOCUMENTATION_VALIDATION.md` (referenced shell scripts that never existed in the repo).
- Merged `digest-core/docs/` content into the main `docs/` structure.
- Introduced versioned prompt directories and a registry for template lookups.
- Operations and developer docs reconciled with `observability/metrics.py`, `healthz.py`, and `digest-core/deploy/*` (systemd user units, cron example); fixed broken doc index links (ACTPULSE-63). Shipped in [PR #36](https://github.com/ruspg/ActionPulse/pull/36), merge commit `7689baf`.
- `digest-core/docs/ARCHITECTURE.md`: **§4.3** documents `select/ranker.py` (`DigestRanker`, `RankerConfig`) and states that **`run.py` does not call the ranker** ([PR #38](https://github.com/ruspg/ActionPulse/pull/38), merge `1f0cd64`).
- `digest-core/docs/ARCHITECTURE.md`: expanded **Stage 3 (THREADS)** to match `ThreadBuilder` (`threads/build.py`); new **§4.4** documents `threads/subject_normalizer.py` / `SubjectNormalizer` (ACTPULSE-39).
- `digest-core/docs/ARCHITECTURE.md`: Tier-1 reconciliation with code — **Stage 4** output is token-truncated (default `max_total_tokens` **7000**); **`EvidenceChunk`** documented as `@dataclass`; **Stage 5** token/bucket behavior matches `select/context.py`; diagram + §8 error rows (Select empty, LLM invalid JSON vs `extract_actions` / `degrade.py`); qwen context + LLM JSON example include `response_format`; §11 / §15 token-budget wording aligned with defaults ([PR #40](https://github.com/ruspg/ActionPulse/pull/40), merge `5321cfe`).
- `digest-core/docs/ARCHITECTURE.md`: Tier-2 doc accuracy — §6.1 Prometheus vs `metrics.py` (remove fictitious `delivery_*`; document real series); §7 `Digest`/`Item`/`Citation` vs `llm/schemas.py`; §5.2 **CTX_BUDGET** env prefix; §5.3 remove non-functional `DIGEST_OUT_DIR` / `DIGEST_STATE_DIR` / `DIGEST_LOG_LEVEL` rows (use `--out` / `--state` / `--log-level`); evidence package tree; jinja2 in §11 + ADR-009/010 wording; ADR-011 delivery metrics; Appendix B `--force`; glossary budget owner; assemble truncation marker; `docs/development/RANKING.md` status banner; `digest-core/README.md` Mattermost + test count ([PR #41](https://github.com/ruspg/ActionPulse/pull/41), merge `12d19f4`).
- **Tier 3 (doc polish):** `ARCHITECTURE.md` — §4.2/§4.4 cross-refs for threading; Stage 4 note on `_detect_structural_breaks`; **§4.5** `llm/models.py` vs default pipeline; §6.1 explicit `run.py` / `record_*` vs unused helpers; §8 rows for `--validate-citations` + `process_digest`+`custom_input` degrade; ADR-008 scope vs `hierarchical/`; `docs/development/CITATIONS.md` link to ARCH §8; root `CLAUDE.md` Makefile `uv sync` fallback; `digest-core/CLAUDE.md` default token budget wording.
- **`docs/reference/COST_MANAGEMENT.md`** переписан под фактический Phase 0 и TD-006 (ACTPULSE-41). `docs/development/CITATIONS.md`, `RANKING.md`, `CODE_EXAMPLES.md`, `QUALITY_METRICS.md`, `MANUAL_TESTING_CHECKLIST.md`; `ARCHITECTURE.md` — пост-LLM шаги, §4.3 ранкер, §7 `RunDigestResult`, §8 CLI по цитатам; root `CLAUDE.md` / `memory.md` / `docs/README.md`. `.gitignore`: `.claude/`.

### Fixed
- Post-merge doc correction: `digest_core.cli setup` **does** exist (wizard); README/CHANGELOG no longer claim otherwise (follow-up to ACTPULSE-63 text).

## [1.1.0] - 2024-10-15

### ⚠️ BREAKING CHANGES
- **Removed: PII detection and masking functionality** - All privacy-related code has been removed
- **Schema Migration: V2 → V3** - `EnhancedDigestV3` now uses plain text fields instead of masked fields
- **Removed fields:** `owners_masked`, all `*_masked` fields
- **Pipeline version bumped to 1.1.0** due to breaking changes

### Added
- `EnhancedDigestV3` schema with neutral fields (`owners`, `participants`)
- New prompt version `mvp.5` for V3 schema
- `MIGRATION.md` documenting schema migration from V2 to V3
- End-to-end tests for pipeline without PII handling (`test_end2end_no_pii.py`)
- Backward compatibility support for V2 schema rendering

### Changed
- **Prompt version: mvp.5** (default for new digests)
- **Schema version: 3.0** for EnhancedDigestV3
- Markdown renderer now displays plain names instead of masked tokens
- LLM Gateway dynamically uses V3 schema when `prompt_version="mvp.5"`
- Simplified JSON schema validation without PII checks

### Removed
- **PII detection and masking** - Complete removal of privacy module
- `digest_core/privacy/` directory (masking.py, detectors, regex patterns)
- PII-related metrics (`pii_violations_total`, `masking_violations`)
- `MaskingConfig` from configuration
- `enforce_input_masking` and `enforce_output_masking` options
- `[[REDACT:*]]` token handling in renderers
- `test_masking.py` and PII-related test assertions
- `owners_masked` field from all schemas

### Migration Guide
- See [`MIGRATION.md`](./MIGRATION.md) for detailed migration instructions
- Existing V2 digests will continue to render correctly
- New digests use V3 schema with plain text fields

**Clarification (documentation, 2026-04):** The **default daily CLI** (`python -m digest_core.cli run`) assembles a `Digest` with `schema_version="1.0"` and extraction prompts `extract_actions*.v1`. The **`EnhancedDigestV3` / `mvp.5`** pair applies to the LLM gateway’s **separate** summarization path (`process_digest` with Jinja `summarize/mvp/v5`), not to that default run output. Package-level constants in `digest_core/__init__.py` describe the V3/mvp.5 contract for that gateway path; avoid assuming they describe the JSON shape written by `run` without checking `run.py` and `llm/schemas.py`.

---

### Previous Releases

### Added (Pre-1.1.0)
- One-command systemd installer (`digest-core/deploy/install-systemd.sh`).
  _Note (corrected 2026-04-06): earlier drafts of this entry referenced
  `install.sh` and `quick-install.sh`, which were never committed to the repo._
- Comprehensive documentation restructure with organized docs/ directory
- Monitoring and observability guides
- Project structure cleanup with proper .gitignore and .editorconfig
- Detailed roadmap and planning documentation
- Quality metrics and KPI documentation
- Mattermost integration planning
- Development guides and code examples

### Changed (Pre-1.1.0)
- Documentation organization: moved all docs to docs/ directory with logical structure
- README structure: minimized root README, added comprehensive documentation links
- Project structure: created scripts/ directory for utility scripts
- Enhanced troubleshooting documentation with EWS connection details

### Fixed (Pre-1.1.0)
- Missing root .gitignore file
- Inconsistent documentation structure
- Lack of development and planning documentation

## [0.1.0] - 2024-01-15

### Added
- Initial release
- EWS integration with NTLM authentication
- LLM-powered digest generation
- Privacy-first design with PII handling via LLM Gateway
- Idempotent processing with T-48h rebuild window
- Dry-run mode for testing
- Prometheus metrics and health checks
- Structured JSON logs with PII handling
- Schema validation with Pydantic
- Docker support with non-root container
- Interactive setup wizard
- Comprehensive test suite
