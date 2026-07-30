# ActionPulse → Multi-Agent Redesign: Implementation Plan

> **Status:** LARGELY SHIPPED — see the reconciliation table below. First committed to the
> repo 2026-06-12 (the plan previously lived only as an untracked local file while
> `ARCHITECTURE.md` and `TERMINAL_DESIGN.md` referenced it).
> **Version:** 0.3.1 — the **v0.3 Next-Wave roadmap** (2026-07-01) sits just below this status block,
> before Part 0; the v0.2 body is the shipped record. (0.1 created 2026-06-09 as a proposal; the body
> below is preserved as the dated design record — per-PR specs read as "what was planned", the table
> as "what is true").
> **0.3.1 (2026-07-30)** — first commit of the v0.3 section, which had lived only as an uncommitted
> local edit for four weeks. Adds §0.3.7 (the budget/call-count lift, which reverses §0.3.1 #4 and a
> §0.3.5 risk row) and re-verifies the plan's code claims against `origin/main` — several were stale
> branch artifacts. Companion review: [`audits/ARCH_REVIEW_2026-07.md`](audits/ARCH_REVIEW_2026-07.md) §8.
> **Scope:** Refactor the single-call, rule-based daily digest CLI into a multi-agent,
> traceability-enforced pipeline using the corporate LLM gateway **fleet**.

## Status reconciliation (verified against code, 2026-06-12)

| Plan item | Status | Where it lives now |
|---|---|---|
| PR1 determinism (content-hash ids, idem sidecar, request-keyed replay) | ☑ | `evidence/split.py`, `digest-*.idem.json`, `llm/gateway.py` |
| PR2 fleet client + RateBroker | ☑ | `llm/fleet.py` (Embeddings/Reranker/Tokenizer + record/replay sidecars), `llm/rate_broker.py` (buckets, burst, penalize, stage budgets) |
| PR3 per-session EWS TLS | ☑ | `ingest/ews.py` (`BaseProtocol.SSL_CONTEXT`; CLAUDE.md gotcha) |
| PR4 per-stage degradation | ☑ | `run.py` `degradation_policy`/`_degrade_stage` |
| PR5 traceable MM delivery | ☑ | `deliver/mattermost.py` (`↳ ev:` sub-lines) |
| PR6 verbatim spans (R2/R3) | ☑ | `llm/schemas.py` `EvidenceSpan`, span-validating gateway |
| PR7 replay + fidelity harness | ☑ | `eval/replay_harness.py` + `eval-replay` (CI gate) |
| PR8 P2 gate (shadow) | ☑ | `evidence/citation_gate.py` (default-on, shadow) |
| PR9 fused relevance score | ☑ | `select/context.py` (cosine+rerank behind `enable_relevance`, off) |
| PR10 judge + gold-set + τ | ☑ | `eval/judge.py`, `eval/gold_set.py`, `eval/calibrate.py` + CLIs |
| PR11 gate default-on + repair | ☑ | `--validate-citations` default ON; `evidence/repair.py`; recall-floor exit 2; `RunDigestResult` |
| Cleanup: `hierarchical/` | ☑ | deleted (CLAUDE.md gotcha). Deviation: `assemble/jsonout.py` was NOT deleted — it stayed wired for tests and now carries the U4 reader fields |
| PR12b source adapters | ☑ | `ingest/envelope.py` + `ingest/source_adapter.py`; `EWSConfig.folders` honored (default `["Inbox"]` = identical behavior; well-known names → account attrs, named folders under the inbox parent, unresolved folders skip with a warning — degrade-not-drop at the folder boundary). Multi-folder live behavior = corp validation (C4 note) |
| PR12a embedding threading | ◐ | **Cosine tier shipped** (`threads/embedding_merge.py` behind `threading.embedding_merge`, default off): one batched embed over the heuristic-weak `subj_`/`single_` groups, deterministic union–find (smallest id wins), merge-only/degrade-not-drop, fleet-sidecar replay, lanes via the embeddings stage. **Remaining — deliberately gated on C6 corp calibration:** the reranker-pairwise band and LLM adjudication residual would stack two more escalation tiers on a cosine threshold (0.85) that has never been measured against real mail; calibrate the cosine tier first (runbook §9.4 C6), then design the band with its own budget (the P2 gate owns the ≤10/run reranker budget). Last-50 thread-cap removal also pending (it logs today, not silent) |
| §4.3 fleet lane display ("PR2 gateway hooks" in the terminal roadmap) | ☑ | `on_lane_update` emitted by `LLMGateway`/`FleetClient` around real network calls (never replay); broker `usage_snapshot()` (trailing-60s RPM + 429 cool-down); RichLiveSink lanes (cap 4 + aggregate, cleared on stage end). Multi-lane shows live once fleet flags flip (PC-2) |
| PC-1 (service-account model) / PC-2 (per-endpoint data-handling ADR) | ☐ | **corp-only**; all fleet live-flags stay off until then (`reranker.enabled`, `enable_relevance`, `judge.enabled`) |

Beyond the plan, the same period also shipped: EP-10 best-of-N with gate-as-selector,
EP-12 judge-wired repair, the quality program (EP-5 step 3), the terminal design system
(L1+T1–T7, ADR-013) and the U-track UX follow-ups (U1–U7). ADR-008 was rewritten per R4
(per-bucket budgets). "Chat with your inbox" (U8) waits on this plan's retrieval pieces
going live (PC-2 → `enable_relevance`).
>
> **Provenance & trust:** This plan is derived from a multi-agent, code-grounded audit
> (2026-06) plus the corp gateway docs. `file:line` anchors were observed at audit time and
> **must be re-verified against the current code** before editing (per `CLAUDE.md`: trust code
> first; this doc and `ARCHITECTURE.md` can drift). Where this plan and `ARCHITECTURE.md`
> disagree about *current behavior*, this plan's Appendix A is the verified statement.
>
> This is a **code-level dev document → English** (product docs are RU per repo convention).

---

## v0.3 — Next Wave (2026-H2): modernize the interface, enable the fleet, add semantic retrieval

> **Status:** PLANNED (forward roadmap). Source: code-grounded architecture review **2026-07-01**
> (3-agent audit + corp-gateway facts `gpu/ENDPOINT-FACTS-AND-LIMITS.md` + external research).
> v0.2 above is the **shipped record**; this section is the **forward plan** and supersedes v0.2's
> "remaining/gated" rows where they conflict. `file:line` anchors are 2026-07-01 observations —
> re-verify before editing (trust code first).
>
> **What this wave optimizes for:** reliability + capability, *not* minimalism. The owner has set
> **cost as a non-constraint**, so v0.2's posture "keep the fleet dark to save calls" is retired.
> **Quality calibration still gates live use** (reason: correctness, not cost) — flip a fleet flag
> *after* it beats its baseline on the existing gold-set/τ harness, never before.

### 0.3.1 Owner decisions (locked 2026-07-01)

| # | Decision | Effect on plan |
|---|----------|----------------|
| 1 | **Modernize the LLM interface** | Phase A — constrained decoding + result cache + real tokenizer. |
| 2 | **Shed legacy impl; refactor structure & old digest surface** | Phase B — delete dead set, collapse schema generations, stage-protocol `run.py`, fix project structure + drifted docs. |
| 3 | **Adaptive best-of-N** | Phase C (C3) — sample only when the deterministic candidate is weak. |
| 4 | ~~**Keep retrieval (no long-context dump)** — keep retrieve-to-~7K~~ **SUPERSEDED 2026-07-04, re-confirmed 2026-07-30** | The *retrieval spine* stays (evidence + citations + P2). The **~7K ceiling does not**: the owner lifted both the evidence-token budget and the per-run call count (ACTPULSE-77). See §0.3.7. |
| 5 | **Enable the full fleet stack** (cost no object) | Phase C — reverses v0.2 "stay dark". reranker + judge + embeddings go live **after calibration + PC-2**. |
| 6 | **Add a vector DB / embeddings retrieval** | Phase D — persistent embedding index over the encrypted store; hybrid keyword+vector; unlocks U8 "chat with your inbox" + InboxAPI/MCP. |

### 0.3.2 Findings this wave acts on (2026-07-01 review, code-cited)

- **Output contract is JSON-*mode*, not schema-constrained** — `llm/gateway.py:482` sends
  `response_format:{type:"json_object"}` (valid JSON, *not* schema-conformant), so a malformed/empty
  result triggers the "quality retry" — **that retry is the scarce 2nd call**. The gateway supports
  `json_schema`/`guided_json` (LiteLLM ≥1.72 passthrough) **and** tool-calling (verified live). → A1
- **217-line RPM machinery for a 1–2 call load** — `llm/rate_broker.py`; production batch on this
  gateway hit **1.6 of 15 RPM**, latency-bound (~33 s/call), not rate-bound. RPM enforcement only
  matters once the fleet is live. → A4
- **~2.7k dead LOC + an old digest surface** — gateway v2/v3 methods, `evidence/actions.py` +
  `lemmatizer.py` (abandoned rule-based extractor), `llm/models.py`, `assemble/jsonout.py`,
  `degrade.build_digest_with_fallback`, `hierarchical/` ghost; `llm/schemas.py` carries **13 of 19
  classes dead** (v2 `EnhancedDigest`, v3 `*V3`, `ThreadSummary`). → B1/B2
- **`words*1.3` token estimate** (`evidence/split.py:262`) despite `/v1/tokenize` on the gateway. → A3
- **Three scoring passes + two-stage token budget** (`split.py` priority → `select/context.py`
  11-term → `select/ranker.py` 10-term). → B3
- **No prompt caching on the gateway** → only viable cache = **client-side content-hash of results**;
  current idempotency is all-or-nothing on the whole run. → A2
- **Docs drift**: "max 2 calls" (vs budget ceiling ~70); TD-003 env-overrides marked *resolved*
  (dead until `d66bf2f`); evidence "≤3000" (code 7000). → B6
- **External grounding:** retrieval sweet-spot 4K–8K beats context-dumping (arXiv:2501.01880) — the
  7K budget is *correct*; adaptive sampling cuts ~70% samples at equal accuracy (arXiv:2502.18581).

### 0.3.3 Workstreams

**Phase A — Modernize the LLM interface** *(highest ROI · low risk · dev-side · unblocked now)*
- **A1 Constrained decoding.** Replace `json_object` + post-hoc validate + quality-retry with
  server-side schema constraint (`response_format:{type:"json_schema"}`; fallback tool-calling).
  Schema-conformant by construction → delete the quality-retry call and the JSON-repair path.
  **OPEN D-A1:** target schema = evolve live v1 `Digest`, or revive the richer dead v3
  `EnhancedDigestV3` (typed `ActionItem`/`DeadlineMeeting`/`RiskBlocker`/`FYIItem`)? Recommend
  evolve-v1-now, mine-v3-for-fields. One corp probe to confirm passthrough.
- **A2 Client-side result cache.** Key = `md5(model + prompt_sha + per-evidence content-hash)`,
  jsonl beside artifacts; re-extract only changed evidence on prompt iteration (no prompt caching
  upstream). Finer-grained than the run-level idem sidecar.
- **A3 Real tokenizer.** `/v1/tokenize` (or local qwen tokenizer) for budget fill; retire `words*1.3`.
- **A4 Right-size RateBroker.** Reframe "RPM enforcer" → "parallelism(≤3)+latency guard". Keep it
  (Phase C needs concurrency control), shed the dead-load assumptions.

**Phase B — Shed legacy + refactor structure** *(clears the path · dev-side · unblocked now)*
- **B1 Delete the dead set** (with tests). **B1a SHIPPED** in
  [#208](https://github.com/pogorelov-labs/ActionPulse/pull/208) — the rule-based extraction stack
  (`evidence/actions.py`, `evidence/lemmatizer.py`) and `llm/models.py`, 2,596 lines, suite green.
  **B1b remains:** `gateway.process_digest` **and everything hanging off it** (`llm/degrade.py`,
  the `EnhancedDigest` family, `markdown.write_enhanced_digest`) — delete as one connected
  component, not symbol-by-symbol; `jsonout.py` (252 LOC, zero src callers); `HierarchicalConfig`
  (`config.py:797`, still wired at `:1364`); dead prompt-registry entries; the `jinja2` dep.
  `degrade.build_digest_with_fallback` **no longer exists** — do not go looking for it.
  ⚠ See `audits/ARCH_REVIEW_2026-07.md` §8.2 before touching `ThreadSummary`: there are two
  distinct classes with that name and one of them is live.
- **B2 Collapse the digest surface.** One schema generation; delete v2 + v3 + `ThreadSummary`
  unless D-A1 picks v3 as the constrained-output target (then delete only the loser).
- **B3 One scoring pass, one budget gate.** Fold the three scorers into one ranker feeding a single
  ~7K budget (budget *size* is research-validated; the duplication is not).
- **B4 Stage-protocol `run.py`** (retire ADR-005): typed `PipelineState` replacing the `run_meta`
  bag; uniform `_guard` over *all* stages incl. post-LLM passes (add the missing `skip` posture so an
  enrichment bug can't discard a paid extraction); **persist raw digest before enrichment**. (See the
  2026-07-01 orchestration deep-dive.)
- **B5 Project structure.** Remove the dual `digest_core/` shim (canonical = `cd digest-core` /
  editable install); gitignore + purge worktree pollution (`out/` 110 artifacts, `ActionPulse.zip`
  45M, `ActionPulseCorpNotebook` 131M, stray `diagnostics-*` dirs); decide monorepo-vs-single-package.
- **B6 Fix the 3 drifted docs** (`ARCHITECTURE.md` max-2→budget model; TD-003 resolved→dead-till-
  `d66bf2f`; evidence 3000→7000) + the digest-core `CLAUDE.md` "≤3000"/"max 2 calls" lines.

**Phase C — Enable & calibrate the fleet** *(owner: cost no object; gated on PC-2 + calibration, not cost)*
- **C0 PC-1/PC-2 (corp).** Still required: service-account identity + per-endpoint data-handling ADR.
  This is a *privacy/governance* gate — embeddings/rerank/judge ship evidence text to extra endpoints.
  Corp-side **critical path**; start in parallel with A/B.
- **C1 Calibrate then flip.** Use existing `eval/calibrate.py` + gold-set + τ. Enable in order, each
  behind a measured win: P2 reranker gate (`reranker.enabled`) → cross-model repair judge
  (`judge.enabled`) → fused relevance (`enable_relevance`). Cheap models where apt (`bge-m3` rerank,
  `glm-47-flash` judge).
- **C3 Adaptive best-of-N.** Trigger sampling only when deterministic `support_recall < τ`; keep the
  citation-gate-as-selector (already a domain-appropriate verifier); raise `stage_call_budgets.extractor`
  alongside.

**Phase D — Semantic retrieval / vector DB** *(new strategy; needs C embeddings live + the store; gated on PC-2)*
- **D1 Embeddings retrieval.** Score evidence by `bge-m3` cosine (`EmbeddingsClient` exists in
  `llm/fleet.py`) instead of hand-rolled keyword passes; feeds the **same ~7K budget** (honors #4).
- **D2 Vector store.** Persist an embedding index in the encrypted store (SQLCipher+FTS5+cosine).
  ~~**Branch caveat:** `store/` is **empty in this checkout**~~ — **resolved 2026-07-30:** that was
  a stale-branch artifact. `store/` is real on `origin/main` (2,056 LOC; inbox program #150–#155),
  as are `api/` (531) and `mcp/` (970). Extend them, don't rebuild them.
- **D3 Hybrid retrieval.** FTS5 lexical + vector cosine + the addressing/sender buckets (keep the
  bucket strategy as the final re-rank). Default-on only behind a measured win vs the keyword baseline
  on the replay/fidelity harness.
- **D4 Unlock U8 "chat with your inbox" + InboxAPI/MCP** on the persistent index.

### 0.3.4 Sequencing & critical path

```
A (interface)   ─┐
                 ├─►  C (fleet — needs clean base + PC-2)  ─►  D (vector — needs C embeddings + store)
B (shed/refactor)┘
```
- **A and B run in parallel, now** — both dev-side, no corp dependency. Do them first; they de-risk
  everything downstream and shrink the surface C/D build on.
- **C and D are gated on PC-2 (corp)** — the real critical path, and not a coding task. Open the
  PC-1/PC-2 ADR in parallel with A/B.

### 0.3.5 New risk rows
- Constrained decoding has version-specific vLLM guided-decoding bugs → keep tool-calling as the
  fallback contract; pin/probe.
- Enabling fleet without calibration → silent quality regressions → calibrate-then-flip is mandatory
  even with free cost.
- Vector index of mail at rest → privacy surface → keep the store's DM-body redaction; embeddings live
  under the same PC-2 ADR.
- ~~Recall↑ tempting budget creep → hold ~7K (lost-in-the-middle; research-backed).~~
  **SUPERSEDED — see §0.3.7.** The lost-in-the-middle risk is *real* and does not go away; it is
  now managed by **calibration** (ACTPULSE-86) rather than by a fixed ceiling. Raising the budget
  without a measured win is the failure mode to guard against, not raising it at all.

### 0.3.6 Open decisions for the owner
- **D-A1** — constrained-output target schema: evolve v1, or revive v3 typed schema? *(rec: evolve-v1, mine-v3)*
  **RESOLVED 2026-07-01:** v3, built P2-preserving (see §5 of the review). Slices A1.1 + A1.3 are on
  `feat/constrained-v3-extraction` — **unmerged, 4 commits behind `main`.**
- **D-B5** — keep monorepo-with-one-package, or flatten to a single root package?
- **D-D** — vector backend: extend the SQLCipher store (privacy-aligned, in-tree) vs a dedicated vector DB (more capable; new dep + privacy review)?

### 0.3.7 Amendment — the budget/call-count lift (2026-07-04, re-confirmed 2026-07-30)

> This section resolves a direct contradiction. §0.3.1 #4 and the §0.3.5 risk row (both written
> 2026-07-01) said **hold ~7K**. ACTPULSE-77 — created 2026-07-04 and attributing the decision to
> the owner *on the same date, 2026-07-01* — says the budget and call-count caps are **lifted**.
> Asked directly on 2026-07-30, the owner confirmed: **the lift wins.** The rows above are struck
> through rather than deleted so the reversal stays visible.

**What changed.** The two constraints that shaped the minimalist design — the evidence-token
budget and the per-run LLM-call count — are **policy, not hard limits**, and may be raised.

**What did *not* change.** The retrieval spine (evidence spans → citations → P2 gate) stays. This
is a decision about *how much* context to retrieve, not about replacing retrieval with a
context dump. The research that motivated ~7K (retrieval sweet-spot 4K–8K; lost-in-the-middle)
is not refuted — it is **demoted from a hard rule to a calibration hypothesis**.

**Reality check against code** (ACTPULSE-77, re-verified 2026-07-30):
`context_budget.max_total_tokens = 7000` has no hard cap and is already raisable, but three
things bind before it — `llm.max_tokens_per_run = 30000`, and two silent count-caps
(`context_budget.per_thread_max = 3`, `split.max_chunks_per_message`). `max_output_tokens` is
hard-clamped to **16384**, which is a *real* gateway ceiling (429-not-413), not a policy choice —
map-reduce is the answer there, because each map call's output stays small.

**The plan this implies** (Plane children of ACTPULSE-77):

| Issue | Work |
|---|---|
| ACTPULSE-78 | Retire the hard-limit language from docs/ADRs (max-2 calls, 3000/7000) |
| ACTPULSE-79 | Un-cap the evidence path end-to-end + raise defaults |
| ACTPULSE-80 | Accurate tokenization via `/v1/tokenize` (retire `words*1.3`) — same as **A3** |
| ACTPULSE-81 | Single large-context extraction: position-aware ordering + collapse scoring passes — overlaps **B3** |
| ACTPULSE-82 | Map-reduce extraction (chunk → per-chunk constrained extract → citation-gate merge) |
| ACTPULSE-83 | Adaptive router: single-call ↔ map-reduce threshold |
| ACTPULSE-84 | Cost/latency/call visibility for unbounded budgets |
| ACTPULSE-85 | RateBroker for large N — **reverses A4**: the broker becomes load-bearing, not right-sized-down |
| ACTPULSE-86 | **Calibration gate before raising defaults** (single-large vs map-reduce vs current) |

**Two consequences for the sections above.** (1) **A4 is reversed** — the plan said "right-size
the RateBroker down"; with map-reduce and a lifted call count, real concurrency arrives and the
broker becomes load-bearing (ACTPULSE-85). (2) **A1 becomes a prerequisite, not a peer** —
map-reduce merges many partial extractions, which is only safe when each one is
schema-conformant by construction. Constrained decoding therefore lands **before** ACTPULSE-82.

**The guard rail.** ACTPULSE-86 is not optional. "Cost is no object" removes the *cost* argument
for restraint; it does not remove the *correctness* one. Raise a default only when it beats the
current baseline on the existing replay/gold harness.

---

## Part 0 — Context, decisions, and ground truth

### 0.1 What this redesign optimizes for

Digest **quality and trustworthiness** over simplicity or cost. Token cost is **not** a
constraint (the gateway meters *requests*, not tokens). The product's soul — **P2 Traceability**
(every digest item traces to offset-verifiable evidence) — is currently *modeled but not
enforced* (see Appendix A); making it a real, enforced, end-to-end guarantee is the headline goal.

### 0.2 Decisions locked with the product owner

| # | Decision | Consequence |
|---|---|---|
| D1 | **15 RPM is a real per-model hard cap** (token-bucket, burst ~3, 60s refill) on `qwen35-397b-a17b` specifically — *not liftable by asking* | Don't try to lift it; **distribute work across the fleet** on independent per-model RPM buckets |
| D2 | **Daily batch latency relaxed to minutes** | Paced multi-agent fan-out is feasible behind a rate-limit broker; keep a fast path only for any future interactive `/digest` |
| D3 | **`hierarchical/` → delete + rebuild clean** | Its good ideas (must-include chunks, merge-with-citations, skip-if-empty) are absorbed into PR8/PR9; the package + its 3 test files are removed |
| D4 | **Single-tenant + read-only retained** | Design seams for multi-user/source-neutral, but don't build them |

### 0.3 Corp gateway fleet (the decisive input)

One boundary (`llm-api.cibaa.raiffeisen.ru`), one Bearer, **non-logging** ("Мы НЕ ЛОГИРУЕМ
содержимое ваших запросов"), tokens **unmetered**. Per-model RPM buckets:

| Design role | Model (API id) | RPM (Personal / SPUZ) | Endpoint / note |
|---|---|---|---|
| Extractor (deep) | `qwen35-397b-a17b` | 15 / — | `/v1/chat/completions`; 512k in / **32k out**; parallel 3 |
| Heavy reasoner / svc-acct extractor | `qwen3-next-80b-a3b` | 45 / 45 | reasoning; likely SPUZ extractor |
| Fast worker / critic | `glm-4.7-flash` | 60–100 | reasoning, high RPM |
| Mid worker / **judge** | `qwen35-35b-a3b` | 30 / 30 | hybrid reasoning |
| **Embeddings** | `bge-m3`, `qwen3-embedding-*` | 30–100 | `/v1/embeddings` |
| **Cross-encoder reranker** | `bge-reranker-v2-m3` | 10–30 | `/v1/score`, `/rerank` (non-batchable) |
| **Tokenizer** | — | — | `/v1/tokenize` (exact token counts) |

**Architectural consequence:** under a *request-rate* cap with *free tokens*, optimize
**work-per-request** (fewer, fatter 397B calls) and offload *breadth* + *verification* to cheaper
high-RPM models on **separate buckets** — verification no longer competes with extraction. The
reranker (10–30 RPM, non-batchable) is the scarce resource and must be **budgeted (~10 calls/run)**.

### 0.4 Preconditions (human confirmations — gate *live* fleet use only)

Everything is built and validated **offline first**; these gate only the flip-to-live of new endpoints.

- **PC-1 — Service-account model access.** Confirm prod role (SPUZ `srv-*` vs Personal). Selects the
  extractor model (`qwen35-397b-a17b` @15 vs `qwen3-next-80b` @45). Config value, not a code fork.
- **PC-2 — Per-endpoint data-handling ADR.** Gateway promises *non-logging*, not *redaction*. Before
  enabling `/v1/embeddings`, `/v1/score`, `/v1/tokenize`, or the judge model **in prod**, obtain a
  written logging/retention/caching statement per endpoint and record it as an ADR. **Delete the
  fictional `x-redaction-policy: strict` claim from `ARCHITECTURE.md §16` now** (it is doc-only; the
  gateway never receives it — see Appendix A).

### 0.5 Cross-phase reconciliations (override per-phase specs where they conflict)

| # | Seam | Decision |
|---|---|---|
| **R1** | Avoid 4 ad-hoc gateway clients | **One shared `llm/fleet.py`** (embeddings/reranker/tokenizer) + judge via existing `LLMGateway` w/ model override; all share the RateBroker, the record/replay channel (namespaced per endpoint), and the one Bearer. Built in PR2; consumed by all later PRs. |
| **R2** | Span coordinate system (models miscount offsets; chunk-local vs body-global) | Extractor returns **verbatim span *text*** `{msg_id, quote}`, **not numeric offsets**. Offsets are derived server-side via `CitationBuilder.find()` into the normalized body. One coordinate system; no model char-counting. |
| **R3** | Drop-vs-degrade on weak/missing span | **Never hard-drop.** `require_evidence_spans=False` through PR11; the gate annotates `weak_evidence`. Degrade-not-drop preserves the ≥90% coverage target. |
| **R4** | ADR-008 "max 2 LLM calls" vs judge/repair | ADR-008 rewritten as **per-model-bucket budgets** (extractor ≤2; judge/reranker/embeddings on own buckets), enforced by the RateBroker. |

---

## Part 1 — Target architecture (hardened)

A small **typed stage decomposition** (the *existing* `run.py` `_stage_*` structure — **no DAG**),
every gateway call mediated by one **RateBroker**, every output graded by one **P2 Gate**, with
verification on **separate model buckets**.

```
              ┌──────────────── RateBroker (per-model buckets, burst 3, Retry-After 60s,
              │                  intra-model serial / cross-model parallel, per-stage budgets) ───────┐
 Envelope[]   │   Thread[]        Evidence[]            Items[]            Graded[]            Digest   │
┌──────────┐  ▼  ┌──────────┐   ┌──────────┐         ┌──────────┐       ┌──────────┐       ┌──────────┐│
│ SOURCE   │────▶│ THREADING│──▶│ RELEVANCE│────────▶│EXTRACTION│──────▶│ P2 GATE  │──────▶│ ASSEMBLE ││
│ adapters │     │ embed +  │   │ 1 fused  │         │ fatter,  │       │ offset+  │       │ +DELIVER ││
│ per-sess │     │ rerank + │   │ score +  │         │ verbatim │       │ rerank   │       │ traceable││
│ TLS      │     │ adjud.   │   │ tokenize │         │ spans    │       │ +repair  │       │ MM+JSON  ││
└────┬─────┘     └──────────┘   └──────────┘         └──────────┘       └────┬─────┘       └────┬─────┘│
     │ fail→partial   (per-stage try/except + degradation_policy; immutable, content-hash keyed)│      │
     └──────────────────────────────────────────────────────────────────────────────────────────┘    │
              ┌──────────────────────────────────────────────────┐  emoji feedback (external JSONL)   │
              │ LLM-JUDGE / EVAL (separate RPM bucket): P/R/F1,    │◀──────────────────────────────────┘
              │ Brier, bootstrapped gold-set, τ calibration       │   gates the default-on flip (PR11)
              └──────────────────────────────────────────────────┘
```

### 1.1 Data / trace model (P2 end-to-end)

```
EvidenceUnit {
  evidence_id        # STABLE content-hash(msg_id|conv_id|msg_idx|chunk_idx|content) — NOT uuid4
  msg_id, source_ref # source_ref carries STABLE conversation_id (content-hash, not PYTHONHASHSEED)
  body_checksum      # sha256 of the exact normalized body the offsets index
}
DigestItem {
  claim(title,RU), section, due, confidence
  evidence_id, evidence_spans:[{msg_id, quote(verbatim, source-lang)}]
  # offsets {start,end} derived server-side from quote via CitationBuilder.find()
  support_score      # reranker(span, claim) OR cosine; graded
  citation_fidelity_ok, weak_evidence, repaired   # gate annotations
}
```

The rule: **no item ships without either (a) a verbatim span whose offsets verify against the
immutable normalized body and whose reranker support ≥ τ, or (b) an explicit `weak_evidence` label**
surfaced in every delivery surface (JSON, Markdown, **Mattermost**). The hierarchical anti-pattern
(free-text aggregation that strips `source_ref`) is banned.

### 1.2 Variants

- **Conservative** (if PC-1/PC-2 restrict prod to one model, no extra endpoints): broker + per-stage
  degradation + deterministic IDs + **graded P2 gate using the extractor/substring only** + traceable
  delivery + one fused rule score. No embeddings/reranker/judge. Still a large trust upgrade.
- **Ambitious** (recommended, feasible per §0.3): full fleet — embedding/reranker relevance &
  threading, cross-model judge, bootstrapped gold-set. All behind flags that stay **off until PC-2**.

---

## Part 2 — Migration plan (PR-by-PR)

### 2.1 Sequence, dependencies, effort

```
PR1  P0 determinism ──────────────┬──────────────► (unblocks replay for ALL)
PR2  P1a fleet client + broker ──┐ │
PR3  P1b per-session TLS ─────────┘ │
PR4  P2 per-stage degradation ──────┤ (independent, early)
PR5  MM traceable delivery ─────────┤ (P5 quick-win, independent — instant trust value)
PR6  P3 extraction spans + RU/EN ──[PR2]──┐
PR7  P4 replay+fidelity harness ───[PR1]──┤ (measurement scaffold)
PR8  P5 shadow gate ───────────────[PR1,PR4,PR6,PR7]┤
PR9  P6 scoring fusion ────────────[PR2]──┤ (parallel to gate track)
PR10 P7 judge+gold+τ ──────────────[PR7,PR8]
PR11 P8 flip gate on + repair ─────[PR10 τ-floor]
PR12 P9 embedding threading+adapters [PR1,PR2]  (XL; split 12a/12b)
+    Cleanup: delete hierarchical/  (independent, anytime early)
```

- **Critical path:** PR1 → PR6 → PR8 → PR10 → PR11.
- **Effort:** P0 M · P1 M · P2 M · P3 M · P4 L · P5 L · P6 L · P7 L · P8 L · P9 XL.
- **PR1–PR5 are behavior-preserving foundation.** Every risky capability ships **off by default**
  (`reranker.enabled`, `enable_relevance`, `judge_enabled` = False; gate `shadow_mode=True` until PR11),
  so PR1–PR9 can land in prod with **zero behavior change** until flags are flipped post-PC-2.

### 2.2 PR1 — Determinism foundation *(M, first; highest ROI)*

Fixes: byte-unstable digests, **broken record/replay** (uuid4 evidence_ids → empty replayed digest),
config/content-blind idempotency, `PYTHONHASHSEED` thread IDs.

- `evidence/split.py:393` — replace `uuid.uuid4()` with
  `evidence_id = "ev_" + sha256("\x01".join([msg_id, conversation_id, str(message_index), str(chunk_index), content]))[:16]`.
- `threads/build.py:216` — replace `hash(normalized_subject)` with `"subj_" + sha256(normalized_subject)[:16]` (matches `build.py:128`).
- `run.py:709/586` — `digest-{date}.idem.json` sidecar `{config_sha256, content_sha256, pipeline_version}`;
  cheap pre-ingest `config_sha256+mtime<48h` guard + post-ingest `content_sha256` gate; `--force` bypasses.
- `llm/gateway.py:448/471` — request-keyed replay: store `request_hash=sha256(canonical(messages))[:16]`;
  `_replay_by_request` with positional fallback for legacy/quality-retry.
- **Tests:** new `test_replay_determinism.py`; extend `test_idempotency.py`, `test_llm_gateway.py`.
- **Acceptance:** identical runs → identical `evidence_id`s & digest; replay reproduces item-set; skip
  fires only on config+content+mtime match.
- **Note:** pre-PR1 recordings are stale (uuid ids) — document re-record requirement in `CLAUDE.md`.

### 2.3 PR2 — Shared fleet client + RateBroker *(M)*  [R1, R4]

- **New `llm/fleet.py`:** `EmbeddingsClient.embed(texts)`, `RerankerClient.score(query,docs)`,
  `tokenize(text)` — endpoints derived from `LLMConfig.endpoint`, one Bearer, `config.headers`;
  **record/replay-aware, namespaced per endpoint**; **no network in replay**.
- **New `llm/rate_broker.py`:** per-model token buckets (`rpm`, `burst=3`, monotonic refill);
  intra-model serialize / cross-model parallel; `penalize(model, retry_after)` honoring 60s; **hard
  per-stage call budgets** (`extractor=2`, `reranker=10`, …) → `StageCallBudgetExceeded`.
- `llm/gateway.py:54/439/87` — delete `MIN_LLM_INTERVAL_SECONDS`, `_wait_for_rate_limit`,
  `_last_call_started_at`; acquire a permit on the network path; `penalize` on 429.
- `config.py:109` — repurpose dead `rate_limit_rpm` → `fleet_rpm` map + `burst` + `stage_call_budgets`.
- **Tests:** `test_rate_broker.py` (burst/refill/penalty/budget); gateway acquires once/attempt, not in replay.

### 2.4 PR3 — Per-session TLS *(S–M)*

- `ingest/ews.py:133–327` — **delete** the process-global SSL monkeypatch of `requests.Session.request`
  + `httpx.Client.__init__`; keep the per-instance `ssl_context`; apply only via `BaseProtocol.SSL_CONTEXT`.
- **Tests:** `test_ews_tls_isolation.py` (after `EWSIngest(verify_ssl=false)`, a fresh `httpx.Client()` still verifies).
- **Verify-before-merge:** MM/healthz httpx clients previously rode the insecure global patch when EWS ran
  with `verify_ssl=false` — confirm corp MM/gateway certs are CA-trusted, or those calls newly fail.

### 2.5 PR4 — Per-stage graceful degradation *(M, no DAG)*

- `run.py:611–694` — wrap `_stage_ingest/_stage_threads/_stage_evidence/_stage_select/_stage_assemble`
  in `try/except` routed through one pure `degradation_policy(stage, exc, cfg)→{crash|partial|empty}` +
  `_degrade_stage(...)` helper; reuse `_build_partial_digest`/`_build_empty_digest` with a per-stage RU
  banner (`STAGE_BANNERS_RU`). Policy: ingest/normalize→empty; threads/evidence/select→partial;
  assemble→crash; `DegradeConfig.enable=false`→crash everywhere. The LLM (`:366-380`) and deliver
  (`:538-544`) stages already self-degrade — don't double-wrap.
- **Tests:** `test_stage_degradation.py`; assemble-failure still exits 1; replay missing-snapshot degrades.
- **Reconcile:** set `citation_validation_ok=False` only when `--validate-citations` is active (a partial
  digest is exit 0 by default — don't trip CI spuriously).

### 2.6 PR5 — Traceable Mattermost delivery *(S, independent quick-win)*

- `deliver/mattermost.py:66–87` — per-item inline sub-line `↳ ev: {evidence_id} | [json](…#{evidence_id})`
  (+ `⚠ слабое обоснование` once the gate lands); thread `json_path` into `deliver_digest`; **no
  `Источники` header** (keeps `test_e2e_pipeline.py:284` green). `getattr`-guard new fields for legacy items.
- Ships immediate trust value before the gate exists.

### 2.7 PR6 — Extraction contract: verbatim spans + RU/EN fix *(M)*  [R2, R3]

- `llm/schemas.py:27` — add `evidence_spans: List[EvidenceSpan]`, `EvidenceSpan = {msg_id, quote}`
  (**verbatim text per R2**, offsets derived later), default `[]`.
- prompts `extract_actions.v1.txt` + `.en.v1.txt` — require ≥1 verbatim **source-language** span per item
  (pointer-to-support, not the RU title); update all 3 few-shots; keep RU output mandate.
- `run.py:765` `_load_extract_prompt` — replace `'qwen' in name` with an explicit model→prompt map
  (covers next-80b/glm); comment that **output is RU in both prompts**, only instruction language differs.
- `llm/gateway.py:547` `_validate_item` — validate each span's `quote` is a verbatim non-empty substring of
  the matching chunk's body; keep surviving spans; **`require_evidence_spans=False`** (R3 — annotate, don't drop).
- **Tests:** `test_extraction_spans.py`, `test_load_extract_prompt.py`; update `mock_llm_gateway.py` to emit spans.

### 2.8 PR7 — Replay + citation-fidelity harness *(L, measure-first)*

- **New `eval/replay_harness.py` + `eval/corpus.py`:** run the real pipeline over frozen
  `--replay-ingest`+`--replay-llm` cases; assert on **metrics** (citation-fidelity via `CitationValidator`,
  support-score histogram, coverage proxy, taxonomy, item-count) vs a committed baseline — **not bytes**.
- `llm/gateway.py:471` — record LLM **input messages** alongside output (`{messages,response,meta}`, backward-compatible read).
- `eval/prompt_eval.py:531` — **delete the circular** output-derived evidence_id fallback; surface `evidence_ids_unverifiable`.
- New CLI `eval-replay` (exit 2 on regression); `make eval-replay` in `make ci`.
- **Privacy:** committed corpus fixtures must be **synthetic/redacted** only (never real corp bodies).

### 2.9 PR8 — P2 gate in SHADOW mode *(L)*  [R3, R6]

- **New `evidence/citation_gate.py`:** per item compute `offset_ok` (offset + **checksum** vs immutable
  body) and, for low-confidence items only, a budgeted `reranker(span, claim)` support score (≤10/run,
  offset-check first); annotate `citation_fidelity_ok + support_score + weak_evidence`; **never drop**.
- `run.py:456/508` — gate runs **default-on but shadow**: annotate always; `citation_validation_ok` stays
  True unless `--validate-citations` (preserves exit codes until PR11).
- `llm/schemas.py` — add the three Optional annotation fields (`exclude_none` keeps artifacts byte-compatible).
- `observability/metrics.py` — `citation_support_score_histogram`, `citation_weak_evidence_total`, `reranker_calls_total`.
- **Reconcile (R6):** normalize confidence type (float vs High/Med/Low) before threshold compare.
  **Reranker `enabled=False` default** until PC-2.

### 2.10 PR9 — One fused relevance score *(L, parallel to gate track)*

- `select/context.py:157` — replace `_calculate_enhanced_scores` with `score = w_rerank·relevance +
  w_meta·metadata`. Relevance = **batched embeddings-cosine** over all chunks + reranker on top-K only
  (≤10/run). Metadata = recency + addressed_to_me + importance + flagged + sender_rank + negative_prior
  (**not in chunk text — these stay**). Drop the textual terms from metadata + the `base_priority*0.1` fold.
- **Keep byte-for-byte:** bucket min-quotas, `per_thread_max`, budget-relax floors — `test_min_bucket_guarantee.py`
  / `test_balanced_selection.py` are the acceptance gate.
- **Delete:** `split.py:470 _calculate_priority_score`, `actions.py:586 _calculate_confidence`, the 5 dead
  `context.py:546–584` helpers. Replace the EVIDENCE-stage sort key (`split.py:176`) with a deterministic
  metadata pre-rank (regression-test which chunks survive the budget cut).
- `signals.py:266` — replace the `sender_rank=1` stub with a real signal from `RankerConfig.important_senders`
  (makes the `critical_senders` bucket live). Fallback to metadata-only when no client/query (offline/tests).
  `enable_relevance=False` default until PC-2.

### 2.11 PR10 — LLM-judge + gold-set + τ calibration *(L)*

- **New `eval/judge.py`** (judge `qwen35-35b-a3b`, own bucket): classify each item vs its cited span →
  per-RU/EN-stratum P/R/F1 + hallucination rate + Brier. **New `eval/gold_set.py`** (bootstrap from
  *externally exported* MM emoji reactions, keyed `(trace_id, item_key)`). **New `eval/calibrate.py`** →
  `calibration.json` with per-stratum **τ at recall≥0.90** + `gates_p8` boolean.
- CLI: `eval-judge`, `eval-gold ingest`, `eval-calibrate`. `judge_enabled=False` default; live run path untouched.
- **Constraint:** MM incoming-webhook is outbound-only → reactions come from an exported JSONL, not a live
  websocket; per-item gold key relies on PR1's stable ids.

### 2.12 PR11 — Flip gate default-on + non-generative repair *(L)*  [R3, R4]

- `cli.py:52/106` — `--validate-citations` **default True** (+ `--no-validate-citations`); exit 2 only when
  **measured support recall < P7 floor**, not on a single bad citation; weak items alone never trip exit 2.
- `run.py:54` — extend `RunDigestResult` with `support_recall/recall_floor/items_weak/items_repaired`
  (keep 2-arg construction); `citation_validation_ok = support_recall ≥ recall_floor`.
- **New `evidence/repair.py`** + `citations.py reselect_span` — **non-generative**: re-select a verbatim
  substring from the *same* chunk; must clear a **higher `tau_repair`**; approved by a **cross-model judge**
  (`assert judge_model != proposer_model`); else mark `weak_evidence` and **deliver with a badge**
  (degrade-not-drop). Repair is substring-only + replay-safe.
- `recall_floor` defaults conservatively (0.0) if PR10 slips. **Docs:** update CLI exit-code tables (CHANGELOG,
  ARCHITECTURE, CLAUDE.md) — downstream CI may newly see exit 2.

### 2.13 PR12 — Embedding threading + source adapters *(XL, last; split 12a/12b)*

- **12a threading** (`threads/build.py`): replace Jaccard-0.7/`subj_hash` with conversation_id →
  reply-headers → **bge-m3 cosine clustering** → reranker pairwise on the ambiguous band (≤10/run) →
  `qwen35-35b-a3b` on a capped residual (≤2/run); **deterministic content-hash thread ids** (PR1);
  **remove the silent last-50 truncation** (defer any cap to the budget stage, logged).
- **12b adapters:** **new `ingest/envelope.py`** (source-neutral `Envelope`) + **`ingest/source_adapter.py`**
  (`SourceAdapter` protocol, `run_sources` with per-source try/except so one source down ≠ crash);
  `EWSIngest` honors `EWSConfig.folders` (not hardcoded `account.inbox`, `ews.py:580`); snapshot format →
  `Envelope` with a back-compat loader.

### 2.14 Cleanup PR — delete `hierarchical/` *(independent, early)*

Remove the `hierarchical/` package + `test_hierarchical*.py` (3 files) + the dormant `HierarchicalConfig`
(or demote to no-op). Its ideas are absorbed into PR8/PR9. Also delete confirmed-dead `assemble/jsonout.py`
(after verifying no rewire). Leave `llm/degrade.py` only if a test still pins it.

---

## Part 3 — Cross-cutting concerns

### 3.1 Feature-flag / rollback strategy
Every risky capability ships **off by default** and is independently revertible (`reranker.enabled`,
`enable_relevance`, `judge_enabled`, gate `shadow_mode`). PR1–PR9 land in prod behavior-neutral; flags flip
only after PC-2.

### 3.2 Test-suite evolution
- Deterministic units stay mocked (broker, id hashing, offset math, tokenizer).
- New **behavioral layer**: replay-corpus + judge-metric assertions (PR7/PR10) replace string-exact ones.
- The **two bucket-guarantee tests are the recall regression gate** for PR9.
- Deleted-code tests (legacy `test_selector.py` helpers, hierarchical, structural-breaks) removed with their code.
- New mocks for embeddings/reranker/judge mirror `tests/mock_llm_gateway.py`.

### 3.3 Privacy
PR6/PR8/PR9/PR10/PR11 add raw-evidence egress (embeddings/reranker/judge). Their live flags stay **off until
PC-2**. Offline/shadow development proceeds freely. Conditional ~30-line local redaction (reusing `logs.py`
patterns + the offset map) is the fallback if any endpoint can't confirm non-logging, and a **hard
precondition before any DM ingest (LVL4)**.

### 3.4 Consolidated risk register

| Risk | PR | Mitigation |
|---|---|---|
| Old recordings stale (uuid ids) | 1 | Re-record inside corp; positional replay fallback |
| EWS-TLS-removal breaks MM/healthz that rode the insecure patch | 3 | Verify corp certs CA-trusted before merge |
| Model miscounts span offsets | 6 | **R2**: emit verbatim text, derive offsets server-side |
| Span enforcement craters recall | 6/8 | **R3**: `require_evidence_spans=False`, degrade-not-drop |
| Reranker bucket starvation (10–30 RPM, non-batchable) | 8/9/12 | offset/cosine first, ≤10 rerank/run, broker budget |
| Removing `_calculate_priority_score` shifts the EVIDENCE token cut | 9 | deterministic metadata pre-rank + survivor regression test |
| Tiny/biased emoji gold → permissive τ | 10 | min-N/stratum, `low_confidence` flag, `gates_p8=False` |
| Judge/repair vs ADR-008 "max 2" | 11 | **R4**: per-bucket budgets; `judge_only_on_repair`; rewrite ADR-008 |
| Default-on flip changes observed exit codes | 11 | conservative `recall_floor` default; CHANGELOG + docs |
| Privacy-surface expansion onto unverified endpoints | 6+ | PC-2 ADR; live flags off until confirmed; gate DM ingest |

---

## Appendix A — Verified current-state teardown (2026-06 code audit)

`file:line` anchors observed at audit time; re-verify before relying on them.

### A.1 Map corrections (where prior docs/assumptions were wrong/stale)

| Claim (prior) | Reality (verified) | Evidence |
|---|---|---|
| **P2 traceability enforced** | **Modeled, not enforced.** `validate_citations` defaults **False**; the normal digest ships items with an **empty `citations` list**. With the flag on, failed items are *kept*; effect is only exit-2 *after* write+deliver. | run.py:106, cli.py:52, run.py:487-502, 656-662 |
| `hierarchical/` dead, config not merged, `ThreadPoolExecutor(4)` | **Dormant.** Config merged + defaults `enable=True`/`auto_enable=True`; processor unwired (only 3 test files reach it); pool `parallel_pool=8`. | config.py:278,444,565 |
| One deterministic LLM call; "max 2" (ADR-008) | Default path can make **3 HTTP calls** (primary + quality-retry-with-retry). "Max 2" **enforced nowhere**. | gateway.py:128,245 |
| Static ~180-line **RU** prompt default | **Default prompt is EN** (`.en.v1`), selected because model name contains "qwen". Output still forced RU. | run.py:765 |
| `degrade.py` wired into `process_digest` | `process_digest` only called by dormant hierarchical → `degrade.py` **dead in prod**. Live LLM-failure path = `_build_partial_digest` "Статус" banner. | gateway.py:753, run.py:783 |
| PII masked at gateway via `x-redaction-policy: strict` | **Documentation-only ASCII art** — never sent (gateway gets only `Authorization: Bearer`). **No local masking.** Privacy = unverified external assumption. | gateway.py:290; ARCHITECTURE §16 |
| `NormalizedMessage` is a NamedTuple, body raw HTML | **Frozen dataclass**; fields `text_body`/`body_norm`; body plaintext-preferred. | ews.py:32,41,55,464 |
| 8-attempt backoff on connection | On **fetch**, not `_connect` (no retry); only catches builtin `ConnectionError`/`TimeoutError`. | ews.py:359,186,406 |
| Quote stripping recursive ≤5 levels | Live `clean_email_body` deletes from first marker to end — **no recursion/level cap**; the 5-level path is **dead legacy**. | quotes.py:697,214,523 |
| One/"second divergent" scoring path | **Three** scoring systems; SELECT's 11 weights are mostly **configurable** + fold 10% of split's score. | split.py:470, context.py:157,208, actions.py:586 |
| `--validate-citations` strict, write-then-validate | **Validate-then-write** and **`strict=False`**; outcome (bad artifacts persist, exit 2) holds, mechanism misstated. | run.py:656,662,500 |
| `test_gold_set_precision_recall` unused skeleton | **Live, passing** test (P≥0.85/R≥0.80/F1≥0.82) but scores the **rule-based** `ActionMentionExtractor` on 18 in-test strings, **not** LLM output. | test_actions.py:331-359 |
| Idempotency per `(user_id, date)` | **Date-only** `(output_dir, date)`; skip trusts `mtime<48h`, never validates content. | run.py:203,586-598 |
| "8-stage pipeline" | Only **7 `_stage_*` functions**; NORMALIZE is a sub-step of `_stage_ingest`, **skipped in replay**. | run.py:276-278,255-263 |

### A.2 P2 reality (the most important finding)
- **ID-level provenance IS enforced** in the live path (`gateway._validate_item` drops items missing
  `evidence_id`/`source_ref` or whose `evidence_id` ∉ input chunks, gateway.py:551-573).
- **Offset-level verifiability is NOT**: opt-in (default off); when on, rebuilds via `.find(chunk.content)`
  (**first-occurrence wins** — can cite the wrong span), checks only **chunk self-consistency** (never the
  LLM's quote), is **`strict=False`** and **keeps** failing items; exit-2 fires *after* write+deliver.
- **The delivered Mattermost message carries zero traceability** (mattermost.py:66-87).
- `enrich_actions_with_evidence` can mis-stamp `evidence_id` (falls back to `msg_chunks[0]`, actions.py:641).

### A.3 Dead / dormant / duplicate inventory
- **Dormant:** `hierarchical/` (config live + default-on, processor unwired; 3 overlapping enable flags;
  `min()`-threshold logic smell; stray-backslash regex bug; pool=8).
- **Dead in prod (tests only):** `degrade.py`, `assemble/jsonout.py` (drops `citations[]`), `llm/models.py`
  strict stack, `summarize_digest`, `_get_simplified_prompt`, `_detect_structural_breaks`, the whole
  `RemovedSpan` apparatus, `context.py:546-584` helpers, enhanced-markdown v2 path.
- **Duplicate/divergent:** 3 scorers; 2 quote-cleaners; 2 LLM-validation stacks; 2 markdown generators; two
  config fields both named `per_thread_max`.
- **Unenforced config:** `cost_limit_per_run` (TD-006), `rate_limit_rpm`, `track_removed_spans`, `≤7-day
  retention` (no knob exists at all).

### A.4 Quality/eval & privacy reality
- **No measurement of LLM-output quality.** Only LLM-output gate = `eval/prompt_eval.py` structural heuristic
  (reachable via `eval-prompt` CLI only, not the run path), with a **circular** evidence-id fallback (prompt_eval.py:531).
- **Privacy:** no local masking; the redaction header is never sent; retention unimplemented; emails *are*
  redacted in logs (contradicting ADR-006). The "sacred" P3 boundary rests on an **unverified gateway assumption**.
- **Record/replay is currently broken:** `evidence_id = uuid4()` per run (split.py:393) re-mints each run, so a
  recorded LLM response's ids never match → `_validate_item` drops every item → **replay yields an empty digest**.
  Replay matching is positional, not request-keyed. (PR1 fixes all of this.)

---

## Appendix B — Provenance & how to verify

- Derived from a multi-agent code audit (13 cluster verifiers + 8 adversarial refuters) and an adversarial
  red-team of the proposed design (7 load-bearing claims, all initially knocked down and the design revised
  accordingly), plus the corp gateway API docs (`CIB-GPU.pdf`) and a real rate-limit probe report.
- **Caveats:** `file:line` anchors are point-in-time; the corp gateway is reachable only inside the corp network
  (offline replay must be used for dev, per ADR-012); SPUZ-vs-Personal model access (PC-1) and per-endpoint
  data-handling (PC-2) are **not derivable from code** and must be confirmed with the LLM-platform team.
- **Re-verify** any claim here against current code before implementing (`make test` + source).
