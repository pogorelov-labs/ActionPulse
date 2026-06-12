# ActionPulse → Multi-Agent Redesign: Implementation Plan

> **Status:** LARGELY SHIPPED — see the reconciliation table below. First committed to the
> repo 2026-06-12 (the plan previously lived only as an untracked local file while
> `ARCHITECTURE.md` and `TERMINAL_DESIGN.md` referenced it).
> **Version:** 0.2 (0.1 created 2026-06-09 as a proposal; the body below is preserved as the
> dated design record — per-PR specs read as "what was planned", the table as "what is true").
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
| PR12b source adapters | ◐ | `ingest/envelope.py` + `ingest/source_adapter.py` shipped; **open:** `EWSIngest` still fetches hardcoded `account.inbox` (`EWSConfig.folders` not honored) |
| PR12a embedding threading | ☐ | `threads/build.py` still subj-hash + text-similarity clustering; build behind a flag on `EmbeddingsClient` (replay-sidecar testable offline) |
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
