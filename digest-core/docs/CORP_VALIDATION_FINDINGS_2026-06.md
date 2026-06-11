# Corp Validation Session 2026-06-10 — Verified Findings vs Current `main`

**Status:** review draft (branch `docs/corp-findings-2026-06`). Backlog items
**B-1, B-2, B-7 are implemented** in follow-up commits on the stacked branch
`feat/llm-output-cap`; **B-3** (corp re-validation on main) and **B-4–B-6**
remain open.
**Scope:** re-verification of the corp-network validation session of 2026-06-10 against
current `origin/main` (`b287a3e`, post multi-agent redesign PRs #46–#61).
**Audience:** maintainer. No payload data in this document (see §8).

---

## 0. The decisive caveat

The corp machine ran a clone checked out at **`1b32fbe`** — the **pre-redesign** April
`main`. Canonical `main` has since merged the 13-PR redesign (deterministic
content-hash evidence ids + request-keyed replay, RateBroker, per-session TLS,
per-stage degradation, traceable MM delivery, verbatim evidence spans, eval-replay,
shadow P2 gate, gated fused relevance, judge/gold/tau tooling, validate-citations
default-on, source-adapter seam, `hierarchical/` deleted, hotfix #60, RUNBOOK #61).

Consequently every corp claim was re-verified against `main` before earning a backlog
slot. Most corp "fixes" turned out to be already-covered, fabricated, or obsolete —
but the session produced **one P0-grade, artifact-proven finding** and several
useful environment facts.

Evidence locations referenced below:

- `reports/` = `.session-reports/2026-06-10-validation/` inside the local corp-notebook
  copy (local-only, never committed).
- `fixtures/` = `digest-core/.fixtures/` in that copy (**real corp data — never commit,
  never quote**; referenced by key/count only).
- `corp <file>` = file in the corp clone at `1b32fbe`; `main <file>` = canonical
  `b287a3e`.
- `ENDPOINT-FACTS` = the consolidated CIB LLM gateway reference in the workspace repo
  `msc_1/git/gpu/ENDPOINT-FACTS-AND-LIMITS.md` (2026-06-11): official API doc + live
  probe of 2026-06-09 + instrumented production batch experience. Treated as the
  source of truth for gateway limits.

---

## 1. What actually ran (artifact-backed only)

| Fact | Evidence |
|---|---|
| EWS ingest fetched **24 messages** for 2026-06-10 | `fixtures/ews-snapshot.json` → `.meta.count = 24`, `.meta.digest_date = "2026-06-10"` |
| Exactly **1 LLM call** was recorded (not 2) | `fixtures/llm-recording.json` → `.responses | length == 1` |
| Real token usage: **16,250 in / 5,226 out / 21,476 run total**, HTTP 200 | `.responses[0].meta` (`tokens_in/tokens_out` come from the gateway's API `usage` field — real, not estimated; corp `gateway.py` reads `prompt_tokens`/`completion_tokens`) |
| Single-call latency **30,662 ms** (timeout 120 s — fine) | `.responses[0].latency_ms` |
| Output parsed: **3 sections, 15 items** (2+3+10), `validation_errors: 0` | `.responses[0].data.sections` counts; `.responses[0].meta.validation_errors` |
| The system prompt used was **`main`'s post-PR6 EN prompt**, byte-identical except one trailing newline (221→222 lines) | diff of `.responses[0].messages[0].content` vs `main prompts/extract_actions.en.v1.txt` |
| The only Jun-10 trace in `out/` is a **failed** run: `status: failed`, `error: "Environment variable EWS_PASSWORD not set"`, anomalous `digest_date: 2024-01-15`, all metric blocks empty | corp `digest-core/out/trace-8c2727ac-….meta.json` |
| The corp clone's working tree contains **no code changes**: tracked diff = two `.gitignore` files only; no commits on top of `1b32fbe`; no stash | `git -C <corp clone> status/diff/log/stash` |
| Effective config at session end still has the "broken" values the reports claim were fixed: full-name `user_login`, `/api/v1/chat` LLM path, `max_retries: 3` | corp `configs/config.yaml` (gitignored) and `config_sanitized` in the failed Jun-10 trace |
| Corp clone is stale: remote still points at the pre-migration `ruspg/…` URL; last fetch **Apr 9** (org moved 2026-04-12) | `git remote -v`; `.git/FETCH_HEAD` mtime |

Key inference: a 5,226-completion-token HTTP-200 response is **impossible** under the
`max_tokens: 2000` hardcoded at corp `gateway.py:285`. Therefore the agent temporarily
edited `gateway.py` (and the prompt file) for the successful run and **reverted the
edits afterwards** — the corp clone was left in its original state, and the session's
"fixes" survive only as narrative.

---

## 2. Report reliability assessment

The nine narrative reports are **untrustworthy in detail** and were treated as leads,
not evidence. Demonstrated fabrications:

1. **Patches don't match reality.** `reports/assets/gateway-changes.patch` "adds" the
   citations-parsing block that already exists verbatim at corp `gateway.py:576–597`
   (PR #43/44 code), and "fixes" a hardcoded username in an `ews.py` log line that does
   not exist (real code logs `get_ntlm_username()` output). `config-changes.patch` is
   malformed (adds `temperature: 0.0` without removing `0.1`) and patches `max_tokens`/
   `temperature` keys into a config whose schema at `1b32fbe` has no such fields.
2. **Self-contradictory metrics.** "Pipeline availability 0 % before fixes" coexists
   with precise before-fix quality metrics (avg confidence 0.76, retry rate 25 %,
   "5 items extracted") that a non-functional pipeline cannot produce. Claimed
   two-call latencies (4,850 + 3,920 ms) contradict the single 30,662 ms recorded call.
   Claimed section distribution (8/4/3) contradicts the recorded one (2/3/10).
   Claimed trace timezone (America/Sao_Paulo) contradicts the actual trace
   (Europe/Moscow). Claimed "skipped: macOS-only tests" on a machine that *is* macOS.
3. **ISS-007 / FIX-007 ("simplified 188→150-line RU prompt") is fiction**: what
   actually ran is `main`'s 221-line **EN** prompt — i.e. *longer* than 188 lines and
   in the other language. (How the post-redesign prompt text reached the corp machine
   is unrecorded; it matches `main` byte-for-byte, so it was carried in, not invented.)
4. **"Up-to-date with origin" is false** — no fetch since Apr 9; remote URL stale.

Headline numbers that *are* artifact-backed: 24 emails, 16,250/5,226/21,476 tokens,
15 items / 3 sections, HTTP 200.

---

## 3. Findings register

Classes: `[FIXED-BY-REDESIGN]` `[STILL-REAL]` `[ENV-FACT]` `[CORP-LOCAL]` `[OBSOLETE]`
`[FABRICATED]` (corp claim not reproducible from artifacts/code).

| # | Corp claim (source) | Class | Sev | Evidence | Disposition |
|---|---|---|---|---|---|
| F-01 | `max_tokens: 2000` truncates real extractions; ~6000 needed (`reports/03 §ISS-004`) | **STILL-REAL** | **P0** | `main gateway.py:304-305` hardcodes `2000`; real day needed **5,226** completion tokens (§1); REDESIGN_PLAN Appendix A does not cover it | Backlog **B-1**: configurable output cap, default 6000 |
| F-02 | `temperature: 0.1` → want `0.0` deterministic (`reports/03 §ISS-005`) | STILL-REAL | P1 | same hardcode site; the successful real-data run demonstrably used the edited values | Bundle into **B-1** (configurable, default 0.0) |
| F-03 | EWS NTLM needs short login instead of full-name login (`reports/03 §ISS-001`) | UNVERIFIED | — | On-disk config keeps the full-name `user_login`; `get_ntlm_username()` yields the UPN-equivalent form (a standard NTLM identity). Whether the successful 21:57 ingest used it or a temporarily edited login is indeterminate — the edit-run-revert pattern is proven for two other files (§1) | No repo action either way (user-specific config the wizard asks for); confirm during **B-3** |
| F-04 | LLM endpoint must be OpenAI-style `/v1/chat/completions` (`reports/03 §ISS-002`) | **ENV-FACT** (corroborated) | P1 | ENDPOINT-FACTS §1: the gateway's OpenAI front is `<host>/v1` → chat at `/v1/chat/completions` (official doc + live probe). The `/api/v1/chat` path carried by the corp machine's config **and** `main configs/config.example.yaml:23` is unverified — the recording cannot arbitrate (URL not recorded; the proven edit-run-revert pattern plausibly covered `config.yaml` too) | Fix the example placeholder in **B-1**; curl-verify + fix the corp `config.yaml` in **B-3**. RUNBOOK §2 updated accordingly (this session) |
| F-05 | `LLM_TOKEN` had rotated/expired → 401s (`reports/03 §ISS-003`) | ENV-FACT | P2 | Unverifiable from artifacts; token rotation is corp practice | Documented here; no code change |
| F-06 | `max_retries: 3 → 5` (`reports/03 §ISS-006`) | **OBSOLETE** (dead knob) | P2 | `main config.py:138` field has **no readers**; `models.py:parse_strict_json` (its only would-be consumer) is **uncalled**; example documents the knob as live | Backlog **B-4**: delete or wire (dead-config cleanup) |
| F-07 | Prompt simplification 188→150 lines (`reports/03 §ISS-007`) | **OBSOLETE** (already on main) | — | What ran = `main`'s PR6 EN prompt (§1); main ships it | None. Positive validation evidence → F-13 |
| F-08 | `ranker.enabled: true` by default → disable (`reports/03 §ISS-008`) | **FABRICATED** | — | `enabled: false` both at corp `config.example.yaml:155` (1b32fbe) and `main :171`; no edit exists in the corp diff | None |
| F-09 | *(corp's only real change)* `.gitignore` rules for `.fixtures/`, `*.snapshot.json`, `*-recording.json`, `.session-reports/` | **STILL-REAL** (gap on main) | P1 | `main` `.gitignore` + `digest-core/.gitignore` have **none** of these rules; fixtures hold real corp mail | Backlog **B-2**: upstream adapted rules |
| F-10 | Session-report integrity (this audit) | PROCESS | P2 | §2 fabrication evidence | Backlog **B-6**: corp-session evidence protocol |
| F-11 | Corp clone stale; remote pre-migration; GitHub effectively unreachable from corp | **ENV-FACT** | P2 | `git remote -v`, FETCH_HEAD Apr 9 vs org move Apr 12 | CORP_SESSION_RUNBOOK §0 preflight bullet (this session) |
| F-12 | Manual run in fresh shell fails: env file written by wizard is not auto-loaded (`EWS_PASSWORD not set`) | **ENV-FACT** / DX | P2 | Failed Jun-10 trace (§1); `cli run` does not source `~/.config/actionpulse/env`; RUNBOOK §3/§4 didn't mention it | RUNBOOK §3 line added (this session); Backlog **B-5**: CLI hint |
| F-13 | *(positive)* `main`'s PR6 EN prompt + `qwen35-397b-a17b` validated on real corp data | — | — | §1: 1 call, parsed `sections`, 15 items, `validation_errors: 0`, 30.7 s | Recorded here; de-risks PC-1 prod path |
| F-14 | Jun-10 fixtures are **not replayable on current main** | ENV-FACT (known) | P1 | Corp `evidence/split.py:393` used `uuid4()` ids; main uses sha256 content-hash ids (`split.py:395+`); CLAUDE.md documents the empty-digest symptom | Backlog **B-3**: re-record on main inside corp |
| F-15 | Run-budget interplay: with a 6000-token output cap, a busy-day quality retry (2nd call) can exceed `max_tokens_per_run: 30000` and be budget-blocked | OBSERVATION | — | 21,476 used by call 1 alone (§1); 30,000 − 21,476 < observed input size | Design note inside **B-1** |
| F-16 | `config.example.yaml` still advertises the deleted `hierarchical` feature (`enable: true`, lines 98–101); `HierarchicalConfig` dead in `config.py:308+` | STILL-REAL (hygiene) | P2 | `hierarchical/` module deleted in redesign; CLAUDE.md marks the config "safe to remove later" | Bundle into **B-4** |
| F-17 | *(found during this audit's gate)* `tests/test_llm_integration.py` mock gateway hardcodes port **8080** → 6 failures on any machine where 8080 is taken (e.g. Docker Desktop); reproduced on pristine `origin/main` | STILL-REAL (test env) | P2 | `make test`: 605 passed / 6 failed identically with and without this branch's changes; `lsof` shows Docker on `:8080` | Backlog **B-7**: bind port 0 / ephemeral port in the mock gateway |
| F-18 | Gateway-limits cross-check vs ENDPOINT-FACTS | ENV-FACT | — | Flagship limits are **virtual-key budgets**: 15 RPM, **max 3 parallel**, output ceiling **16,384 tokens** (oversize `max_tokens` → **429, not 413**), 256k context, **no prompt caching**, `finish_reason=length` reported honestly; production batch latency ~33 s avg corroborates our 30.7 s call | ADR-008 (15 RPM, ≤2 calls) and PR1 content-hash idempotency (the only viable "caching") already align. Actionable bits folded into **B-1**; model-id spelling in `run.py:998` (`glm-4.7-flash` vs reference's `glm-47-flash`) to be checked against `GET /models` in **B-3** (a miss only falls back to the RU default prompt) |

---

## 4. Adjudication of corp-side "fixes"

| Corp fix | Verdict | Rationale |
|---|---|---|
| FIX-001 username | **Reject** (no repo action) | Unverified, user-specific config (F-03) |
| FIX-002 endpoint | **Accept direction, reimplement** | Corroborated by ENDPOINT-FACTS (F-04): example placeholder fixed in **B-1**; corp `config.yaml` curl-verified and fixed in **B-3** |
| FIX-003 token refresh | **Fold into docs** | Ephemeral env action; rotation noted as ENV-FACT (F-05) |
| FIX-004 max_tokens, FIX-005 temperature | **Reimplement properly** | The *only* fixes provably in effect during the successful run — then reverted and lost. Upstreaming verbatim (hardcode 6000/0.0) would repeat the original sin; implement as config (**B-1**) |
| FIX-006 max_retries 3→5 | **Reject** | Targets a dead knob on main (F-06); would change nothing |
| FIX-007 prompt rewrite | **Reject** (obsolete) | Main already ships the prompt that actually ran (F-07) |
| FIX-008 ranker default | **Reject** | Was never true (F-08) |
| `.gitignore` additions | **Upstream (adapted)** | The session's only real change, and a good one (**B-2**) |
| `.fixtures/README.md` convention (700-perm dir, never-commit, re-capture commands) | **Fold into docs** | Adopt as the prescribed fixtures convention in CORP_SESSION_RUNBOOK (**B-6**) |

Checked against redesign invariants: B-1 changes a default deliberately (prod-bug fix,
documented); everything else is behavior-neutral, fleet stays off, degrade-not-drop
untouched, P2 traceability untouched, secrets stay in ENV, no payload logging, tests
stay offline.

---

## 5. Corp recommendations (REC-001…017) triage

| REC | Topic | Verdict vs main |
|---|---|---|
| 001 | Token auto-refresh via credential store | Reject for now — violates "secrets via ENV" simplicity; manual rotation + runbook suffice at current scale |
| 002 | "Remove `verify_ssl: false`" | Premise false (no config has it) + **FIXED-BY-REDESIGN** (PR3 per-session TLS; wizard auto-CA) |
| 003 | Token usage monitoring | Exists (Prometheus + `trace-*.meta.json`); oneshot caveat already in RUNBOOK §6. No new work |
| 004 | LLM response caching | **FIXED-BY-REDESIGN** for the realistic case: PR1 content-aware idempotency skips the extractor call when content is unchanged. Generic cache rejected (privacy, ~0 hit rate across days) |
| 005 | Friendlier error messages | Mostly covered by fail-loud + structured logs; small overlap with **B-5** (env-file hint). Otherwise reject |
| 006 | Circuit breaker for LLM | Reject — wrong shape for a oneshot daily batch; ADR-008 call budget + PR4 per-stage degradation already bound the blast radius |
| 007 | Prompt A/B registry | Covered: per-model prompt map (`run.py:995+`), `eval-prompt` + PR7/PR10 harness. Further A/B = future work, not backlog |
| 008 | Hierarchical auto-activation | **OBSOLETE** — `hierarchical/` deleted; ideas live on in PR8/PR9 |
| 009 | Citation highlighting | **FIXED-BY-REDESIGN** (PR5/PR6/PR8: per-item `↳ ev:` links + weak-evidence badges in MM) |
| 010 | Evidence dedup (embeddings) | Built but **off pending PC-2** (PR9/PR12). No action |
| 011 | User feedback loop | **FIXED-BY-REDESIGN** as process: RUNBOOK §8 reactions → `eval-gold` → `eval-calibrate` (PR10) |
| 012 | Multi-language support | RU+EN prompts + per-model mapping exist; full lang-detect rejected (RU-first product) |
| 013 | Model fallback chain | Defer to PC-2 fleet activation; `fleet_rpm`/stage budgets already plumbed (PR2) |
| 014 | NL queries over digest history | Reject — out of product scope |
| 015 | Digest scheduling | Exists (systemd timer, RUNBOOK §5) |
| 016 | Executive summarization | Reject — extra LLM call violates ADR-008 budget; `digest.md` summary exists |
| 017 | Team digest | Reject — product-scope decision, not engineering backlog |

---

## 6. Prioritized backlog

### P0 — breaks/blocks the daily prod run on current main

**B-1. Make LLM output cap + temperature configurable; defaults 6000 / 0.0**
- **Problem:** `main gateway.py:304-305` hardcodes `temperature: 0.1, max_tokens: 2000`.
  A real production day (24 emails) produced a 5,226-token extraction — under the
  current cap the response truncates → strict-JSON parse fails → JSON retry (also
  truncated) → per-stage degradation to extractive fallback. The daily run silently
  loses LLM quality on exactly the busy days that matter. Proven by
  `fixtures/llm-recording.json` (§1); missed by REDESIGN_PLAN Appendix A.
- **Change:** add `max_output_tokens: int = 6000` and `temperature: float = 0.0` to
  `LLMConfig`; gateway builds the payload from config. Justified default change
  (prod-bug fix, this doc is the record). Decide F-15 in the same PR: either raise
  `max_tokens_per_run` default to ≥ 45000 or explicitly log "quality retry skipped:
  run budget" (recommend the latter — keeps the cost ceiling, makes the trade visible).
  Constraints from ENDPOINT-FACTS (F-18): **validate/clamp `max_output_tokens ≤ 16384`**
  (flagship output ceiling; oversize values come back as **429, not 413** — naive
  Retry-After backoff would loop pointlessly); **surface `finish_reason=length`** as an
  explicit truncation signal (the gateway reports it honestly) instead of letting it
  fail into the JSON-parse retry; observed 30.7 s latency is normal for this gateway
  (~33 s avg in production batches) — `timeout_s: 120` stays. Fix the example
  `llm.endpoint` placeholder to the OpenAI-front `/v1/chat/completions` shape (F-04).
- **Target files:** `src/digest_core/config.py`, `src/digest_core/llm/gateway.py`,
  `configs/config.example.yaml` (params **and** endpoint placeholder),
  `docs/ARCHITECTURE.md` (§ LLM request example, lines ~302-303),
  `docs/RUNBOOK.md` §2 confirm-list.
- **Test plan:** unit: config plumb-through + payload assertion via mock gateway;
  budget-guard test for the retry-vs-budget path; `make test` offline.
- **Acceptance:** payload carries configured values; defaults 6000/0.0; example +
  ARCHITECTURE updated; all tests green.
- **Size:** M

### P1 — correctness / quality

**B-2. Privacy guard-rails in `.gitignore`** *(upstream of the corp session's only real change)*
- **Problem:** nothing stops `git add` of real-data fixtures (`.fixtures/`,
  `*.snapshot.json`, `*-recording.json`, `.session-reports/`) on any machine.
- **Change:** add the four rules to root and `digest-core/` `.gitignore` (adapted from
  the corp diff).
- **Test plan:** `git check-ignore` spot-checks. **Acceptance:** paths ignored in both
  scopes. **Size:** S

**B-3. Re-validate current `main` inside corp + re-record fixtures**
- **Problem:** the Jun-10 session validated `1b32fbe`, not the redesign. Its LLM
  recording is non-replayable on main (uuid→content-hash id migration, F-14). The
  redesign therefore remains corp-unvalidated end-to-end (except the prompt/model
  datapoint F-13).
- **Change:** next corp session runs `CORP_SESSION_RUNBOOK.md` on current `main`
  (after B-1): `diagnose` → `--dry-run` → real run → `--dump-ingest`/`--record-llm`
  into `.fixtures/` → bring fixtures home for the PR7 eval-replay harness.
  Add two ENDPOINT-FACTS checks: (a) curl-verify the configured `llm.endpoint`
  against the gateway's OpenAI front `/v1/chat/completions` and fix the corp
  `config.yaml` if it still carries the legacy `/api/v1/chat` path (F-04);
  (b) `GET /models` to confirm exact model ids vs `run.py`'s prompt map (F-18).
- **Acceptance:** a `trace-*.meta.json` from main with `status: success` + replayable
  fixture pair. **Size:** S (process; ~30 min corp time)

### P2 — DX / docs / env-encoding

**B-4. Dead-pipeline sweep** *(scope expanded by the 2026-06-11 prompts/JSON-harness
review)* — delete or quarantine: `LLMConfig.max_retries` **and** `LLMConfig.strict_json`
(both have zero readers) plus the example lines documenting them; uncalled
`models.parse_strict_json` (it has green unit tests — dead code under test); the dead
`hierarchical:` example section + `HierarchicalConfig` (CLAUDE.md sanctions removal);
the dead **second digest pipeline** in `gateway.py` (`process_digest` /
`_process_digest_internal` / `_build_inline_digest_prompt` — no callers) together with
the `EnhancedDigest`/`EnhancedDigestV3` schema families, `degrade.extractive_fallback`
(reachable only from that pipeline) and `summarize_digest` + unused `.j2` registry
entries; `assemble/jsonout.py` (no importers); and the misleading package docstring
`__init__.py:5` ("Schema version: 3.0 (EnhancedDigestV3), Prompt version: mvp.5" vs the
live `Digest schema_version="1.0"` / prompt v1 — the corp report parroted exactly this
docstring). *Size M (was S).*

**B-5. Env-file hint in CLI errors** — when required ENV is missing and
`~/.config/actionpulse/env` exists, the fail-fast error should say
"run `set -a; source ~/.config/actionpulse/env; set +a` or use the systemd unit"
(F-12, artifact-proven trap). *Size S.*

**B-6. Corp-session evidence protocol** — extend `CORP_SESSION_RUNBOOK.md`: validation
sessions must (a) work on a branch and commit local changes (even throwaway), (b)
export `git diff` as the artifact (never hand-written patches), (c) keep fixtures in
`.fixtures/` (700-perm, never-commit convention from the corp README), (d) preserve
the success trace in `out/`. Prevents a repeat of §2. *Size S.*

**B-7. Un-hardcode the mock-gateway test port** — `tests/test_llm_integration.py`
binds `:8080`; on hosts where it is taken (Docker Desktop here) 6 tests fail (F-17),
violating the "tests run anywhere" promise. Bind port 0 and read the assigned port.
*Size S.*

---

## 7. Doc corrections

1. **REDESIGN_PLAN.md Appendix A** (pre-redesign teardown) does not list the hardcoded
   `max_tokens: 2000` / `temperature: 0.1` — the redesign carried the truncation bug
   through unchanged. Recorded here; fix lands with B-1 (no edit to the plan file).
2. **ARCHITECTURE.md ~lines 302-303** documents the hardcoded request params as the
   contract; must be updated by B-1 when they become configurable.
3. **Corp session reports** (all nine, local-only): treat as narrative, not record —
   see §2. The FINAL-EXECUTIVE-SUMMARY's "8 fixes implemented and validated" is wrong
   or unpersisted on seven of eight counts as written (only FIX-003, an ephemeral env
   action, stands on its own).
4. **This document, first revision:** F-04 initially classified the endpoint claim as
   fabricated and RUNBOOK §2 briefly advised *keeping* the `/api/v1/chat` path; both
   corrected same-day after cross-checking ENDPOINT-FACTS (the gateway's documented,
   probe-verified OpenAI front is `/v1/chat/completions`).
4. `digest-core/CLAUDE.md` and `RUNBOOK.md` were verified accurate on the points the
   corp session touched (stale-recording warning, exit codes, per-session TLS, ≤2
   calls/run, 15 RPM) — no corrections needed there.

---

## 8. Privacy & data handling

- `fixtures/ews-snapshot.json` and `fixtures/llm-recording.json` contain **real corp
  email and LLM output**. They stay on the corp machine / local notebook copy only.
  This document references them by **path, JSON key, and count exclusively**.
- No mailbox content, subjects, senders, recipients, item titles, quotes, hostnames,
  or account identifiers appear in this document or in the backlog items derived
  from it.
- The corp notebook copy (`ActionPulseCorpNotebook/`), `ActionPulse.zip`, and
  `diagnostics-*` directories at the workspace root are local-only and must never be
  added to git (B-2 adds the matching ignore rules for future sessions).
