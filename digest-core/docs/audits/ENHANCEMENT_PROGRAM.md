# Enhancement program — ActionPulse digest-core

> Status: **PROPOSAL** — Wave 1 (EP-1..EP-3) pre-approved by the owner for immediate execution;
> everything else is unexecuted until the owner reads DECISIONS-NEEDED.
> Synthesized from `docs/audits/2026-06-11-frontier-audit.md` (alignment matrix; currently on
> branch `feat/llm-output-cap`) + `docs/CORP_VALIDATION_FINDINGS_2026-06.md` (reconcile verdicts
> for the corp-session findings; same branch).
> Generated: 2026-06-11 · Base commit: `b287a3e` (origin/main) · Author: quality-loop / enhancement-program
> Trust: all file:line anchors below were **re-verified against `origin/main`** at synthesis time
> (the audit cited the `feat/llm-output-cap` working tree — line numbers differ there); re-verify
> before editing.

Path note: written to `docs/audits/` (operator instruction) rather than the template's repo root.

---

## Tracks table

| Track | Current state on main (file:line / verdict) | Frontier bar | Actions | Wave |
|---|---|---|---|---|
| F1 Orchestration | Linear pipeline; RateBroker buckets exist, judge hardcoded `None` (`run.py:556`); fleet dormant | verification on separate capacity | — (PC-2 gated → D4) | W3 |
| F2 Context engineering | No JIT retrieval — `ContextSelector` built without `relevance_scorer` (`run.py:339`); verbose per-chunk header | smallest high-signal set, JIT | — (PC-2 gated → D4; header diet folded into EP-5 eval) | W3 |
| F3 Evals / judge | Single-call pointwise rubric (`eval/judge.py:15-20,37-44`); no κ/α; CI runs pytest only; `recall_floor=0.0` inert (`config.py:476`) | pairwise/reference-anchored gate + agreement drift | EP-5 | W2 |
| F4 Injection safety | Untrusted bodies after non-unique `---`, no spotlighting (`gateway.py` `_prepare_evidence_text`); no adversarial fixtures; gate shadow-only (`citation_gate.py:10` "NEVER drops") | spotlighted untrusted content + containment | EP-4 (flip → D1) | W2 |
| F5 Reliability | 401 falls into bare `raise` (`gateway.py:339`) → generic partial digest; degradation per stage good (PR4) | actionable credential-expiry errors | EP-3 | **W1** |
| F6 Observability | `start_http_server` failure swallowed to warning (`metrics.py:339-343`); no OTel/spans; quality metrics good | exporter provably up + OTel GenAI semconv | EP-2 (bind), EP-8 (OTel) | **W1** / W2 |
| F7 Process | Metric-regression harness only (`eval/replay_harness.py`); no traces→taxonomy→gold loop | closed error-analysis loop | EP-11 | W4 |
| F8 Memory | No cross-run item state; idem sidecar dedups same-day rebuilds only (`run.py:655,694,794`) | cross-run dedup with TTL | EP-7 (flip → D3) | W2 |
| F9 Test-time compute | N=1 always; citation gate is an unused hard verifier (`citation_gate.py:48-66`) | best-of-N where a verifier exists | EP-10 | W3 |
| F10 Provenance | `run_meta` (`run.py:217-235`) lacks code SHA / prompt id / config hash; `_config_sha256` exists but only feeds the idem sidecar (`run.py:855,655`) | per-run provenance manifest | EP-1 | **W1** |
| F11 Agentic security | No OWASP-agentic mapping; HTML normalizer example-tested only, no property/fuzz tests (no `hypothesis` in pyproject) | fuzzed untrusted-parser surface | EP-9 | W2 |
| F12 Supply chain | Dockerfile ignores `uv.lock` (`docker/Dockerfile`: `pip install -e .`); no pip-audit/SBOM/checksums; ad-hoc zip carry-in | reproducible, attested air-gap bundle | EP-6 | W2 |

Already-addressed (from the reconcile artifact — cited so this program provably does not re-implement
them): output-cap truncation P0, EN/RU taxonomy drift, validation-crash observability, mock-gateway
port, corp-data gitignore — all fixed on `feat/llm-output-cap` (commits `e4f5ad3`, `cf593c6`,
`2d1723b`, `e99d35d`, `a4790a4`; unmerged at synthesis time). The corp session's FIX-001/002/006/007/008
were classed fabricated/obsolete — not items.

---

## Wave model + sequencing rule

**Sequencing rule (non-negotiable): measure before you change · offline before restricted-env ·
flags before flips.**

| Wave | Name | Gate | Character |
|---|---|---|---|
| W0 | Reconcile | done | `CORP_VALIDATION_FINDINGS_2026-06.md` is the reconcile artifact |
| **W1** | Measure & harden | baseline measurable offline | offline-verifiable only: manifest, exporter surfacing, auth-error classification |
| W2 | Quality climbs | W1 merged; offline baseline frozen (EP-5 step 1) | offline refactors, each with a before/after eval delta |
| W3 | Restricted-env validation | validation pack + sign-offs + D4 | the only wave that needs the corp door |
| W4 | Continuous loop | program steady-state | failure→gold→issue cadence |

W1 executes now (owner pre-approval); W2 starts only after its baseline is frozen; W3 is planning-only
in this cycle.

---

## Wave 1 items (execute now)

### EP-1 — Per-run provenance manifest   [W1] [F10]

- **Problem:** a trace cannot answer "which code/prompt/config produced this digest" — `run_meta`
  carries pipeline_version + sanitized config but no code SHA, no prompt id/hash, no config hash,
  no flag summary. The corp-validation audit showed how costly that is forensically.
- **Evidence:** `run.py:217-235` (run_meta init); `run.py:855` (`_config_sha256` exists, idem-only);
  `run.py:372,1003-1006` (prompt id resolved but not recorded in run_meta); audit F10 rows.
- **Proposal (file-level):** new `src/digest_core/provenance.py` — `build_provenance(config) ->
  dict` returning `{code_sha, code_sha_source (git|env|unknown), pipeline_version, model_extractor,
  config_sha256, flags{validate_citations, ranker_enabled, degrade_enabled, mattermost_enabled}}`;
  code SHA via `git rev-parse HEAD` (subprocess, 2 s timeout, never raises) → fallback
  `ACTIONPULSE_CODE_SHA` env (Docker build arg) → `unknown`. Seed `run_meta["provenance"]` in
  `_build_run_context`; enrich at the LLM stage with `prompt_id` + `prompt_sha256` (SHA-256 of the
  loaded prompt file). No payload data.
- **Acceptance criteria:** every `trace-*.meta.json` contains `provenance` with the keys above; unit
  tests assert presence + prompt hash equals hashlib of the file + git-sha helper degrades to env/
  `unknown` without raising; full suite green.
- **Verification path:** offline-verifiable (pytest + replay-ingest run writes the manifest).
- **Flag + rollback:** no flag — additive trace metadata, no behavior change. Rollback = revert commit.
- **Depends on:** —

### EP-2 — Surface metrics-exporter bind failure   [W1] [F6]

- **Problem:** `start_http_server` failure is downgraded to a warning and swallowed — the April
  "metrics not available" incident was undiagnosable because the run looked healthy.
- **Evidence:** `metrics.py:332-343` (`except Exception` → `logger.warning`, no state kept).
- **Proposal (file-level):** `observability/metrics.py` — record the failure
  (`self.exporter_error`, `exporter_status` property), log at **error**; new config knob
  `observability.fail_on_exporter_error: bool = False` — when true, re-raise (oneshot batch keeps
  degrade-not-drop default). `run.py` — write `run_meta["metrics_exporter"] = {status, error,
  port}` so the trace shows it.
- **Acceptance criteria:** test occupies a port → collector reports `status=failed`, run_meta gets
  the failure, no crash; with flag true → raises; default behavior otherwise unchanged; suite green.
- **Verification path:** offline-verifiable (bind-conflict test). Whether the corp scrape can reach
  the port at all stays `requires corp validation` (W3 checklist row).
- **Flag + rollback:** `observability.fail_on_exporter_error` (default false = today's behavior);
  set false / revert to roll back.
- **Depends on:** —

### EP-3 — Classify LLM credential expiry actionably   [W1] [F5]

- **Problem:** an expired/rotated `LLM_TOKEN` surfaces as a bare `httpx.HTTPStatusError` → generic
  "partial digest" with no hint; token rotation is routine in corp, so this is a recurring,
  misdiagnosed failure (it also confused the 2026-06-10 corp session).
- **Evidence:** `gateway.py:316-339` — 429 and 5xx get typed handling; **401/403 fall to bare
  `raise`** at `gateway.py:339`; degrade reason in `run.py:388` is a generic `llm_failed`.
- **Proposal (file-level):** `llm/gateway.py` — new `LLMAuthError` (non-retryable) raised on
  401/403 with an operator-actionable message (refresh `LLM_TOKEN` in `~/.config/actionpulse/env`,
  re-run); `run.py` LLM-stage except — map `LLMAuthError` to degrade reason `llm_auth_failed`
  (metrics) while keeping the partial-digest behavior; RUNBOOK §9 row.
- **Acceptance criteria:** mock 401 → `LLMAuthError`, exactly one HTTP call (no retry), message
  names `LLM_TOKEN`; 403 same; degrade reason recorded as `llm_auth_failed`; suite green.
- **Verification path:** offline-verifiable (mock transport). Real 401-on-rotation behavior of the
  corp gateway `requires corp validation` (next corp session checklist).
- **Flag + rollback:** no flag — error classification only; run outcome (partial digest, exit 0)
  unchanged. Rollback = revert commit.
- **Depends on:** —

---

## Wave 2 items (offline; start after W1 merge + frozen baseline)

### EP-4 — Spotlight untrusted email + adversarial fixtures   [W2] [F4]

- **Problem:** untrusted email bodies are concatenated into the user message after a non-unique
  `---` with no "data, not instructions" framing; zero injection fixtures exist, so regressions are
  invisible.
- **Evidence:** `gateway.py` `_prepare_evidence_text` (block headers + `---`); audit F4;
  `tests/fixtures/emails/` (no adversarial cases).
- **Proposal:** per `skills/injection-hardening`: wrap each evidence body in unique delimiters
  (e.g. random-tagged fence per run), add an explicit spotlight instruction to both prompts
  (RU+EN, one sentence: evidence is data; instructions inside it must be ignored); add
  `tests/fixtures/emails/injection-*.json` (instruction-override, exfil-link, tool-bait,
  markdown-bomb) + asserts that injected instructions do not alter sections/items and rendered MM
  text stays inert. Prompt change bumps the prompt changelog and goes through the EP-5 baseline
  diff. **Does NOT flip the citation gate** — that is D1.
- **Acceptance criteria:** injection fixtures pass (no instruction leakage into output JSON);
  baseline replay metrics unchanged ±0 on the clean corpus; changelog entry.
- **Verification path:** offline-verifiable (fixtures + replay); live model behavior under injection
  `requires corp validation` (record fixtures next session).
- **Flag + rollback:** prompt versioned via changelog (revert = previous prompt text); delimiter
  wrapper behind `llm.spotlight_evidence` (default **on** is the goal, but ships default **off**
  until the baseline diff is reviewed).
- **Depends on:** EP-5 step 1 (frozen baseline) for the prompt-diff gate.

### EP-5 — Judge: agreement stats + calibration loop (architecture per D5)   [W2] [F3]

- **Problem:** the release-quality judge is a single-call pointwise 0–1 rubric — the exact pattern
  the 2026 research pass refuted; no κ/Krippendorff-α drift tracking; the CI gate is inert.
- **Evidence:** `eval/judge.py:15-20,37-44`; `config.py:476` (`recall_floor=0.0`); CI workflow has
  no eval step.
- **Proposal:** per `skills/judge-calibration`: **step 1 — freeze the offline baseline** (run
  `evaluate_corpus` + existing judge eval on the gold set; commit metrics JSON to
  `docs/audits/baselines/`); step 2 — add `scripts/agreement.py`-based κ/α computation to
  `eval/` (judge-vs-gold, judge-vs-judge across prompt versions); step 3 — implement the
  owner-chosen architecture from **D5** (pairwise or reference-anchored or hybrid) behind
  `eval.judge_mode` (default = current pointwise until D5); step 4 — CI eval job stays
  **report-only** until **D2** sets a real floor.
- **Acceptance criteria:** agreement stats computed and stored per eval run; new-judge vs gold κ ≥
  pointwise baseline on the same gold set; no CI behavior change until D2.
- **Verification path:** offline for harness + stats (gold set is local JSONL); fresh gold growth
  `requires corp validation` (MM reactions export).
- **Flag + rollback:** `eval.judge_mode` (default `pointwise` = today); flip back to revert.
- **Depends on:** D5 (architecture), D2 (gate floor); steps 1–2 are decision-free and can start.

### EP-6 — Reproducible air-gap bundle   [W2] [F12]

- **Problem:** the Docker image is built from `pyproject.toml` (`pip install -e .`), ignoring
  `uv.lock` — the deployed env is not the tested env; no pip-audit/SBOM/checksums; corp carry-in is
  an ad-hoc zip.
- **Evidence:** `docker/Dockerfile` (`RUN pip install --no-cache-dir -e .`); `uv.lock` present;
  CI has no audit step; `ActionPulse.zip` at workspace root.
- **Proposal:** per `skills/airgap-bundle`: Dockerfile → `uv sync --frozen` from `uv.lock`
  (multi-stage); add `make bundle` producing `dist/actionpulse-bundle-<sha>/` with wheels, image
  tar, `SHA256SUMS`, SBOM (`uv export` + `pip-audit --format cyclonedx` or `syft`), and a
  `MANIFEST.json` (code SHA ↔ EP-1 provenance); CI job: `pip-audit` (report-only first).
- **Acceptance criteria:** `docker build` succeeds offline from lock only; bundle checksums verify;
  pip-audit job runs in CI (non-blocking); CORP_SESSION_RUNBOOK references the bundle instead of
  the zip.
- **Verification path:** offline-verifiable (build + checksum + audit run locally/CI).
- **Flag + rollback:** none needed (build path); keep the old Dockerfile stage until first corp
  carry-in succeeds, then delete.
- **Depends on:** EP-1 (manifest shares the provenance fields).

### EP-7 — Cross-run dedup ledger (default OFF)   [W2] [F8]

- **Problem:** a multi-day action resurfaces every day; the idem sidecar only dedups same-day
  rebuilds.
- **Evidence:** `run.py:655,694,794` (idem flow); audit F8 (no item-level state).
- **Proposal:** per `skills/memory-design`: `.state/delivered-items.jsonl` ledger keyed by
  `evidence_id`/content-hash with `first_seen`/`last_seen` + TTL; at assemble time annotate
  repeats (`seen_before: true`) — **annotate, not suppress** in v1 (R3-consistent); suppression
  and TTL values are D3. Flag `memory.dedup_ledger` default **off** — with the flag off nothing is
  written to disk, preserving today's privacy-by-not-storing.
- **Acceptance criteria:** with flag on, two consecutive replay runs over the same fixture mark
  repeats; with flag off, no `.state` ledger file is created; suite green.
- **Verification path:** offline-verifiable (replay fixtures).
- **Flag + rollback:** `memory.dedup_ledger` (default off); delete ledger file + flag off to revert.
- **Depends on:** D3 for any default-on / suppression behavior.

### EP-8 — OTel GenAI semconv (spans, flag-gated)   [W2] [F6]

- **Problem:** no spans; stage timings live only in a histogram + run_meta; `gen_ai.*` semconv
  absent, so traces can't join any standard tooling.
- **Evidence:** `metrics.py` (Prometheus only); `run.py` stage timing via `_record_stage_duration`.
- **Proposal:** per `skills/otel-genai-align`: optional dependency group `otel`; span-per-stage +
  span-per-LLM-call with `gen_ai.system/request.model/usage.*` attributes (ids and counts only —
  never payloads); exporter = OTLP-file/console offline; flag `observability.otel_enabled`
  default off.
- **Acceptance criteria:** with flag on, a replay run emits a span tree (file exporter) with
  `gen_ai.*` attributes and zero payload strings; flag off → zero overhead imports; suite green.
- **Verification path:** offline-verifiable (file exporter). Corp collector endpoint
  `requires corp validation`.
- **Flag + rollback:** `observability.otel_enabled` (default off).
- **Depends on:** EP-2 (observability surface in run_meta), EP-1 (shared resource attrs).

### EP-9 — Property/fuzz tests for the HTML normalizer   [W2] [F11]

- **Problem:** the highest-risk untrusted parser (bs4 HTML → text) is tested only with hand-picked
  fixtures; malformed-HTML fallbacks are unexercised.
- **Evidence:** `tests/test_html_normalization.py` (example-based); `normalize/html.py:116-136`
  fallback paths; no `hypothesis` dependency.
- **Proposal:** add `hypothesis` (dev group); property tests: never raises on arbitrary
  bytes/HTML, output contains no `<script>`/event-handler remnants, idempotent on its own output;
  seed corpus from the injection fixtures (EP-4).
- **Acceptance criteria:** hypothesis suite green in CI within time budget (`--hypothesis-profile
  ci`); at least one previously-unreached fallback branch covered (coverage diff).
- **Verification path:** offline-verifiable.
- **Flag + rollback:** none (tests only).
- **Depends on:** EP-4 fixtures (shared corpus), else standalone.

---

## Wave 3 (requires corp — planning only this cycle)

### EP-10 — Best-of-N extraction selected by the citation gate   [W3] [F9]

- **Problem:** extraction is single-shot at temp 0.0 while a deterministic offset+SHA verifier
  (the citation gate) sits unused as a selector — the cheapest quality upside the audit found.
- **Evidence:** `citation_gate.py:48-66` (annotate-only verifier); `gateway.py` (one call + one
  conditional quality retry); ADR-008 (≤2 calls/run) — **conflicts**, see D6.
- **Proposal:** sample N extraction candidates (temp > 0 for samples 2..N on the extractor's own
  RPM bucket), score each by offset-verifiable support recall, keep the max; flag
  `extract.best_of_n` default 1. Offline harness first (replay candidates through the gate);
  live N/RPM tuning in corp.
- **Acceptance criteria (offline part):** harness shows support-recall(best-of-N) ≥
  support-recall(N=1) on the replay corpus before any live run.
- **Verification path:** offline for the selector harness; `requires corp validation` for live
  sampling under 15 RPM / 3-parallel budgets.
- **Flag + rollback:** `extract.best_of_n` (default 1 = today).
- **Depends on:** EP-5 step 1 (baseline), D6 (ADR-008 cap), D4 if sampling rides fleet buckets.

Also W3: corp-side verification rows from W1/W2 items (EP-2 scrape reachability, EP-3 real-401
behavior, EP-4 live injection probes, EP-6 first bundle carry-in) — these ride the next corp
session checklist (`CORP_SESSION_RUNBOOK.md`), no separate items.

## Wave 4

### EP-11 — Continuous failure→gold→issue loop   [W4] [F7]

Per `skills/backlog-loop`: every eval/production failure becomes a gold row + tracked issue with
the evidence link; quarterly frontier-bar re-audit. Entry gate: W2 merged and the program in
steady state. Planning row only.

---

## DECISIONS — RESOLVED 2026-06-11 (owner interview)

All seven owner calls were resolved in a structured pros/cons interview on 2026-06-11.
The original tensions are preserved in git history; this table is the ruling.

| # | Decision | Resolution | Enactment |
|---|---|---|---|
| D1 | Citation gate shadow → enforcing | **Quarantine now**: weak items move to a trailing «Не подтверждено» section (flag `reranker.quarantine_weak`, default on) — withheld from the main sections, never dropped (R3-compatible) | run.py quarantine step + tests; repair becomes real once D4 wiring lands |
| D2 | CI eval gate + `recall_floor` | **eval-replay joins GitHub CI now** (deterministic corpus regression gate, exit 2 fails the job); `recall_floor` stays 0.0 until the first MM-reactions export → `eval-gold` → `eval-calibrate` produces a defensible number | `.github/workflows/ci.yml` eval job |
| D3 | Dedup ledger vs privacy-by-not-storing | **Default ON, annotate-only** (hashed fingerprints + 14-day TTL = the documented retention policy; right-to-be-forgotten = delete the file); MM gets a «↻ повтор» marker; suppression deferred until dogfood data | `memory.dedup_ledger` default flip + MM marker; RUNBOOK retention note |
| D4 | PC-2 per-endpoint data handling | **YES for all three** fleet endpoints (reranker `/rerank`, embeddings, judge `qwen35-35b-a3b`) — same gateway host/key/trust domain as the approved extractor | unlocks fleet wiring (W3 work + corp validation); repair + real support scores + judge calibration |
| D5 | Release-judge architecture | **Hybrid by job**: reference-anchored judge for the release/regression gate (calibrated vs gold, κ ≥ 0.41 + CI floor before it may gate); pairwise reserved for EP-10 best-of-N selection | EP-5 step 3 implementation (post fleet wiring) |
| D6 | Best-of-N vs ADR-008 | **Rewrite ADR-008 around the real constraint ceilings** (per-stage RateBroker budgets, 15 RPM key budget, 3-parallel, token budget) — go up to the ceilings when needed — **plus visible call-count + token-budget reporting** to the operator every run | ADR-008 text rewritten (ARCHITECTURE.md); `llm_budget` summary in run_meta + log + MM trace footer |
| D7 | Plane seeding | **Seed open items only** (post-decision survivors), sanitized | Approved; pending — the Plane MCP was not connected in the enactment session. Seed EP-10..15 from BACKLOG.md when it is |

---

## Backlog handoff

Resolved per D7: open items are seeded into Plane (project ACTPULSE) with sanitized text;
`docs/audits/BACKLOG.md` mirrors them. The continuous failure→issue flow belongs to EP-11
(`backlog-loop` skill).
