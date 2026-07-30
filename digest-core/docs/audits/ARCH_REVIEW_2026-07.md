# ActionPulse — Architecture Review (honest result)

**Date:** 2026-07-01 · **Reviewer:** Claude (Opus 4.8) + 4 sub-agent passes, in one session
**Scope:** `digest-core/` (the product). Point-in-time snapshot of a moving codebase.
**Base observed:** working checkout on `fix/config-generic-env-override` (= `origin/main` + 1 commit) and worktrees off `origin/main` `d72fb3e`.

> **Read this as a snapshot, not scripture.** `file:line` anchors were true at review
> time and drift; some subsystem characterizations came from sub-agents and were
> spot-checked, not each independently re-verified. Where I state confidence, I mean it.
> Corp-network components (real EWS/LLM/gateway behaviour, PC-2) cannot be verified from
> outside the bank and were reasoned about from the endpoint reference doc, not observed.

---

## 1. The one-paragraph truth

The architecture is **sound in shape, over-populated in mass, and its own source-of-truth
docs are wrong in specific, verifiable ways.** The pipeline design (ingest → normalize →
threads → evidence → LLM → assemble → deliver, with an evidence/citation spine and
push-to-Mattermost delivery) is the *right* design for an auditable corporate digest — 2025
long-context-vs-RAG research independently vindicates the retrieve-to-~7K + provenance
approach it already uses. But of ~25k lines across 73 modules, roughly **two-thirds is dead
code, "built-but-dark" machinery switched off, or secondary surface** that each grew larger
than the core it attaches to. The essential product is ~7–9k lines. This is not "rewrite it."
It is "the docs have stopped telling the truth, and the implementation carries the weight of a
platform to run a one-call extractor."

---

## 2. What is genuinely good (stated plainly, not as a courtesy)

- **Clean stage contracts and dependency direction.** Ingest/normalize/threads/evidence/
  select/llm are leaves; only `run.py` imports across all of them. No import cycles found.
- **Real test discipline.** 101 test modules, ~26k test LOC (~1:1 with source) — *verified*.
  The suite is the checklist and it is green (1430 passed / 98 skipped on `origin/main`).
- **Traceability is a real principle, not a slogan.** The live `Item` carries verbatim
  `evidence_spans` + validated `citations` + a P2 gate. This is exactly what current
  grounding/attribution best practice prescribes, and it aligns with the audit-trail argument
  for keeping retrieval over context-dumping.
- **Correct failure postures where they exist.** Degrade-not-drop, best-effort delivery,
  privacy-by-construction (hash-only dedup ledger; DM bodies redacted at rest).
- **Offline-dev affordances** (`--dump/replay-ingest`, `--record/replay-llm`, diagnostic
  bundles) are the right answer to the "corp network only" constraint.
- **The dark machinery is honestly gated,** behind explicit flags and a calibration gate
  (PC-2), not hidden or reckless.

None of the criticism below erases this. It is a capable, well-tested system.

---

## 3. What is actually wrong (ranked; evidence + confidence)

### 3.1 The source-of-truth docs misrepresent the running system — **most damaging** · HIGH confidence
This is the finding that matters most, because it silently misleads whoever operates the tool.
- **"Max 2 LLM calls per run"** (`ARCHITECTURE.md` §4.1 diagram, `:286`) vs the config's own
  default `stage_call_budgets` ceiling of ~70 (extractor 2 + reranker 10 + embeddings 30 +
  judge 8 + tokenize 20). Reality: 2 by default *because four stages are flag-gated off*, not
  by design-limit. **Honest correction:** the root `CLAUDE.md` on `origin/main` has *already*
  been fixed to the accurate per-stage framing; only the `ARCHITECTURE.md` diagram still lags.
  So this is milder than a first read suggests — but the SoT diagram is still wrong.
- **The generic `DIGEST_<PREFIX>_<FIELD>` env-override system was entirely dead until commit
  `d66bf2f`** (the tip of the current branch). `_merge_model` read the env var only as a
  signal to *skip* YAML and never applied it. Yet this was marked **resolved** (TD-003, "Снято
  в коде") in the architecture doc and advertised in `CLAUDE.md` for months. *Verified via
  `git show d66bf2f`.* A feature documented as done, tested as done, listed as retired debt —
  and dead.
- **Evidence budget "≤3000 tokens"** (digest-core `CLAUDE.md`) vs code default **7000**
  (`split.py`). *Verified.*

### 3.2 Dead code still in the tree — ~2.7k+ lines · HIGH confidence (grep-verified zero production callers)
| Path | LOC | Note |
|---|---:|---|
| `evidence/actions.py` + `evidence/lemmatizer.py` | 1,295 | Abandoned rule-based extraction stack (hand-rolled lemmatizer). **Removed in [PR #208](https://github.com/pogorelov-labs/ActionPulse/pull/208).** |
| `llm/models.py` | 160 | Parallel JSON validator the gateway reimplemented. **Removed in #208.** |
| `llm/gateway.py` v2/v3 methods (`process_digest`/`summarize_digest`/…) | ~550 | Never called by `run.py` — but **test-covered** (see 3.6), so not free to delete. |
| `assemble/jsonout.py` | 252 | Production-unused (reader uses `json.load`), but the owner **deliberately kept it once** — not removed without intent. |
| `degrade.build_digest_with_fallback` | ~58 | No caller. |
| `hierarchical/` | 0 src | Orphan dir; `HierarchicalConfig` (a 40+-field dead class) still in `config.py`. |

### 3.3 A single-step extractor wearing a multi-agent exoskeleton — MEDIUM/HIGH confidence
ADR-002 says single-step; the code grew `best_of_n`, a reranker fleet, an embeddings client,
a cross-model repair judge, a citation gate, and a 217-line `RateBroker` — **all gated OFF by
default.** You carry the complexity cost of the platform on every read and get its value on
zero production runs. The `RateBroker`'s RPM enforcement, specifically, solves a problem the
1–2-call default load does not have (the corp gateway's real bottleneck is per-call latency,
not RPM — per the endpoint reference).

### 3.4 God objects and accretion — HIGH confidence (measured)
- `run.py`: **2,107 lines, 64 module-level functions**, a mutable `RunContext` + an untyped
  `run_meta` dict written by ~15 functions. ADR-005 ("no pipeline abstraction — one source")
  expired (there are two sources now) but the linear orchestrator just grew.
- `config.py`: **1,393 lines, 26 sub-config classes.**
- `gateway.py`: **1,400 lines**, ~39% of it the dead v2/v3 stack.
- The "single-step extraction" is wrapped in a **7-pass post-LLM mutation chain** that
  alternates immutable `model_copy` and in-place mutation, with **none of the passes guarded**
  — a bug in enrichment discards the already-paid LLM call (assemble runs *after*).

### 3.5 Redundant layers — MEDIUM confidence (agent-characterized, partially verified)
Three independent scoring passes (`split.py` priority → `select/context.py` 11-term →
`select/ranker.py` 10-term), a two-stage token budget, and `signals.py` scanning 100+ verb
lists per chunk only to feed a fallback retry hint.

### 3.6 The "old digest surface" is not dead — it is test-covered · HIGH confidence
`schemas.py` carries **three generations** (v1 live; v2 + v3 + thread-summary dead-in-prod).
But v2/v3 are exercised by `test_enhanced_digest` / `test_enhanced_markdown` /
`test_end2end_no_pii`. **Critically: v3 is the *older*, less-capable schema** — it predates the
evidence-span/citation/P2-gate machinery and has only a bare `quote: str`. The live v1 `Item`
is the evolved, traceable one. (This directly shaped a decision — see §5.)

### 3.7 Server-shaped infra on a batch job — MEDIUM confidence
`metrics.py` (747 LOC) binds a Prometheus port for the 2–5 minutes the process lives, then
vanishes — a pull-scraper can't reliably catch a 2:30am run. It also still instruments a
**deleted** subsystem (`hierarchical_runs_total`). And `ingest/mattermost.py` (1,431 LOC) is
2.15× the EWS ingest for what began as "mentions only."

---

## 4. Corrections I owe the record (being honest about my own review)

- **I initially measured `store/` as empty (0 LOC) and called store/inbox/MCP "half-wired."**
  That was the `fix/config-generic-env-override` checkout. `origin/main` **does** contain a
  full encrypted-store + InboxAPI + MCP subsystem (confirmed from the `origin/main`
  `CLAUDE.md`). So: the secondary-surface mass was *under*-counted at review time, and those
  subsystems are real and merged, not vaporware. My earlier "empty/half-wired" read was a
  branch-state artifact.
- **The "max 2 calls" doc drift is partly already fixed** on `origin/main` (§3.1). I first
  presented it as wholly open; it is not.
- **Some findings are sub-agent characterizations** (the scoring-pass redundancy, the metrics
  weight) that I spot-checked but did not exhaustively re-verify line-by-line.

---

## 5. Decisions taken this session, and their honest risk

The owner made two calls that override the review's default recommendations. Both are
legitimate owner prerogatives; recording the risk is the honest part.

- **Enable the full fleet (reranker/judge/embeddings), cost no object.** Reverses the
  "keep-it-dark-to-save-calls" posture. *Sound, provided* calibration still gates *live* use —
  the reason is now correctness, not cost. The PC-2 corp data-handling ADR remains a real,
  non-code gate.
- **"Literal v3" as the constrained-decoding target.** I flagged **twice** that v3 predates
  and lacks the P2 traceability machinery, so a literal revival would regress P2 — the #1
  golden rule. The owner chose it both times, informed. I am building it **P2-preserving**:
  the traceability backbone is grafted onto the v3 item types (shipped in slice A1.1), so P2
  survives. **Residual risk:** the full v3 migration (slice A1.4) is a large, live-path-flipping
  rewire across ~15 files (run.py, the post-LLM chain, citation gate, assemble, reader, labels,
  and the 3 enhanced tests). It is the single riskiest change on the roadmap and should land
  incrementally behind a flag with the suite green throughout.

---

## 6. What this review produced (honest accounting)

- **Shipped:** [PR #208](https://github.com/pogorelov-labs/ActionPulse/pull/208) — removed
  2,596 lines of grep-verified dead code (rule-based stack + dead validator), suite green.
- **Foundation, local (branch `feat/constrained-v3-extraction`, not pushed):** A1.1 (P2
  backbone on v3 types) + A1.3 (JSON-schema constrained-decoding mechanism in the gateway).
  Both non-breaking, suite green (1438).
- **Roadmap:** `REDESIGN_PLAN.md` §v0.3 — the forward plan (modernize LLM interface, shed
  legacy, enable+calibrate the fleet, add semantic retrieval). *Currently uncommitted in the
  working checkout.*

---

## 7. Bottom line

Ship the honesty fixes first: **correct the three drifted doc claims** (they are the actively
harmful part) and **land the dead-code deletions** (#208 + the gated remainder). Then modernize
the **LLM I/O contract** (constrained decoding — highest ROI, already begun). The retrieval +
citation spine is correct; do not rip it out — make its *implementation* honest and lean, and
turn the dark machinery on *after* it beats its baseline, not before. The system doesn't need a
new architecture. It needs its existing one to stop lying about itself and shed the two-thirds
that isn't earning its place.

---

## 8. Re-verification against `origin/main` (2026-07-30)

This review was written from a checkout **71 commits behind `origin/main`** and then sat
uncommitted for four weeks. Everything below was re-checked against `origin/main` `bcaa28c`
before the review was committed. Three claims changed.

### 8.1 Claims that were wrong, and are now corrected

- **§3.1 — the env-override finding is obsolete.** The review said the generic
  `DIGEST_<PREFIX>_<FIELD>` system "was entirely dead until commit `d66bf2f` (the tip of the
  current branch)". `d66bf2f` was never merged and never needed to be: the **same fix shipped
  on `main` as [#140](https://github.com/pogorelov-labs/ActionPulse/pull/140) (`f00dfc4`)**
  and was refined in [#170](https://github.com/pogorelov-labs/ActionPulse/pull/170)
  (`78c4aaf`). *Verified live on `origin/main`:* `DIGEST_LLM_TIMEOUT_S=999` →
  `config.llm.timeout_s == 999`. So `ARCHITECTURE.md` §13.1's "TD-003 снято" is **accurate**,
  and the branch `fix/config-generic-env-override` is a superseded duplicate, safe to delete.

- **§3.2 — `degrade.build_digest_with_fallback` no longer exists.** There is no top-level
  `degrade.py`. The live module is `llm/degrade.py::extractive_fallback` (140 LOC), and it
  *is* called — from `gateway.py:1047`.

- **The dead set is a connected component, not a list of isolated symbols.** A naive
  "count src references" pass makes `EnhancedDigest` look alive (12 refs outside `schemas.py`).
  It is not: `run.py` calls `gateway.extract_actions`, never `process_digest`. The whole
  cluster hangs off that one dead entry point —
  `gateway.process_digest` → `llm/degrade.extractive_fallback` → `EnhancedDigest` →
  `markdown.write_enhanced_digest` / `_generate_enhanced_markdown` (no src callers). Deleting
  `process_digest` frees all of it at once; deleting any single member first will look unsafe.

### 8.2 A trap for whoever executes B2 (schema collapse)

**`ThreadSummary` is two different classes with the same name.**
`llm/schemas.py::ThreadSummary` is dead-in-prod; `store/retrieve.py::ThreadSummary` is **live**
and backs `InboxAPI.list_threads` → the MCP `list_threads` tool. A grep-driven deletion will
conflate them. Re-verify per-symbol, per-module — the "13 of 19 classes dead" figure in
`REDESIGN_PLAN.md` §0.3.2 is a *starting hypothesis*, not a verified count.

### 8.3 Claims that held up

Confirmed on `origin/main`: `jsonout.py` (252 LOC) still has **zero** src callers; the
`hierarchical/` directory is gone but `HierarchicalConfig` survives in `config.py:797` and is
still wired at `config.py:1364`; the gateway still ships `response_format: {"type":
"json_object"}` (`gateway.py:525`) with **no** `json_schema`/`guided_json` path; `split.py`
still estimates tokens as `words * 1.3` (`:262`, `:285`) while `fleet.py:283` already wraps
`/v1/tokenize`. The accretion critique also held: `run.py` grew **2,107 → 2,474** lines and
`config.py` **1,393 → 1,657** since the review was written.
