# ActionPulse — Project Status (stream snapshot)

> **As of:** 2026-06-19 · **Companion to:** [`ROADMAP.md`](./ROADMAP.md) (forward plan),
> [`../../digest-core/docs/ARCHITECTURE.md`](../../digest-core/docs/ARCHITECTURE.md) (SoT).
> This is a point-in-time *snapshot* — progress %s are honest engineering estimates, not
> instrumented metrics. When a number and the code disagree, trust the code.

## 1. Executive snapshot

ActionPulse is **~87% built but ~58% live**. The codebase is feature-complete and well-tested
across eight streams; the gap between *built* and *live* is the whole story, and it concentrates
in the three differentiating streams (Extract & Trust, Remember & Retrieve, Deliver). They share
a **single unlock**: one supervised corp-network session (write PC-2, run live, deliver in
api-mode, harvest reactions, calibrate, flip the flags). That sequence converts most of the dark
inventory to live **with zero new features** (§5).

- **Built** = code shipped + tested.
- **Live (realized)** = actually working for a user today — not behind a default-off flag, and not
  requiring the corp network it has never run on.

## 2. Streams

| # | Stream | Built | Live | One-line state |
|---|--------|:----:|:----:|----------------|
| 1 | Capture — EWS + Mattermost ingest | 90% | 60% | Both sources built; only the EWS basic path is proven live |
| 2 | Extract & Trust — the quality loop | 85% | 20% | All trust tiers built but **dark**; `recall_floor=0.0` (inert) |
| 3 | Remember & Retrieve — store · API · MCP | 95% | 40% | Whole surface shipped; store off-by-default, semantic needs corp |
| 4 | Deliver + reactions flywheel | 80% | 35% | Webhook live; the flywheel is a finished engine, **never spun** |
| 5 | Terminal UX · setup · onboarding | 92% | 88% | Most-realized stream; works offline today |
| 6 | Privacy · consent · retention | 80% | 75% | Guardrails strong; the **PC-2 ADR is unwritten** |
| 7 | Observability · eval · QA | 82% | 60% | Harness + coverage gate built; real corp P/R/F1 un-measured |
| 8 | Docs · architecture · contribution | 88% | 88% | SoT + ADRs + CONTRIBUTING current (truth-pass done) |

**Overall: ~87% built / ~58% live.**

## 3. Per-stream: done / missing

### 1 · Capture — 90 / 60
- **Done:** EWS (NTLM, per-session TLS, retry + HTTP timeout, multi-folder, incremental watermark);
  Mattermost (allowlisted channels, consent-gated DMs, AIMD adaptive concurrency, api-mode);
  the multi-source seam; `--dump-ingest` / `--replay-ingest`.
- **Missing:** chat-tuned extraction prompt; cadence / real-time "urgent nudge"; **MM ingest never
  validated on the real stack** (EWS basic fetch is the only live-exercised path).

### 2 · Extract & Trust — 85 / 20  ← *largest gap*
- **Done:** verbatim evidence spans; citation gate (shadow → quarantine → repair); reranker tier;
  reference judge; best-of-N; content-hash evidence IDs; gold / τ-calibration harness.
- **Missing (all dark):** `reranker.enabled` / `judge.enabled` / fused relevance all `False`;
  `best_of_n=1`; `recall_floor=0.0` (the trust gate is **inert**); EN-extraction quality unmeasured;
  calibration never run. This is the product's headline promise and the least realized.

### 3 · Remember & Retrieve — 95 / 40
- **Done:** SQLCipher 30-day store (FTS5 + brute-force cosine + RRF hybrid); `InboxAPI` facade
  (retrieve / search / `ask` / summarize / compare / related / open-loops / pending / source verbs);
  `actionpulse-mcp` MCP server + macOS AI-CLI installer; `ask`/RAG; carryover + pending sections.
- **Missing:** `store.enabled=False` by default; semantic / `ask` / carryover need store-on + the
  corp gateway; cross-digest **history browser** unbuilt. Offline keyword search is what's live today.

### 4 · Deliver + reactions flywheel — 80 / 35
- **Done:** webhook delivery (live); api-mode + owner-only channel + post-id capture;
  `delivered_ledger` + `reactions` harvest + `eval-gold` / `eval-calibrate` — *a finished engine*.
- **Missing:** the flywheel has **never been spun** (no corp deliver → react → calibrate cycle);
  least-privilege **bot** identity; per-section threading; slash commands.

### 5 · Terminal UX / setup — 92 / 88  ← *most realized*
- **Done:** setup wizard (+ encrypted-store step); launcher menu (+ search / ask rows, store-gated);
  digest reader; the full terminal design system + conformance CI; global `actionpulse` command.
- **Missing:** wizard prompts for `MM_PAT` / api-mode delivery (minor); corp visual checks C2–C5.

### 6 · Privacy / Consent / Retention — 80 / 75
- **Done:** fail-closed DM-at-rest redaction (structural — DMs get no chunk rows); consent gate
  (Pydantic validator + wizard/menu UX); retention knobs (7 d plaintext / 7 d ledger / 30 d store);
  secrets-via-ENV-only; log redaction; fail-closed `--dump-ingest` redaction.
- **Missing:** **the PC-2 per-endpoint data-handling ADR is unwritten** (referenced everywhere as the
  master gate); ARCHITECTURE §16 fictional-masking correction; rotate the exposed PAT; optional
  local-masking fallback.

### 7 · Observability / Eval / QA — 82 / 60
- **Done:** structlog JSON · Prometheus · healthz · OTel spans; `eval-replay` regression gate; the
  gold/judge/calibrate harness; **coverage gate (86% on store/api/mcp, CI-enforced)**; CI lanes
  (test · test-store · test-mcp · coverage · terminal-matrix · eval-replay).
- **Missing:** real corp P/R/F1 (calibration un-run); the corp LLM/EWS paths are faked in tests;
  `cost_limit_per_run` unenforced (TD-006); a true subprocess/stdio MCP e2e; `install.sh` shellcheck.

### 8 · Docs / Architecture / Contribution — 88 / 88
- **Done:** ARCHITECTURE SoT + ADR-014 (store) / ADR-015 (MCP); current ROADMAP; RUNBOOK;
  CONTRIBUTING (preflight + extras + lanes + dual-docs-tree); CHANGELOG caught up; memory hygiene.
- **Missing:** the PC-2 ADR (same gate as §6); relocate the dead `HIERARCHICAL_ORCHESTRATION` doc to
  `legacy/` (bannered for now); consolidate the two `docs/` trees; L3 RU→EN translation backlog;
  ARCHITECTURE §13/§16 stale tables.

## 4. The "built-but-dark" inventory

Everything below is shipped + tested but **off by default** — the inventory that one corp session
converts to live. (Defaults verified against `config.py` on 2026-06-19.)

| Capability | Default | Gate |
|------------|---------|------|
| Encrypted store | `store.enabled = False` | install extra + corp validation |
| Carryover ("Open loops") | `store.carryover = False` | store on + a few days history |
| Pending ("Awaiting your reply") | `store.pending = False` | store on + history |
| Semantic / hybrid search, `ask`/RAG | needs store + gateway | corp network |
| Reranker tier | `reranker.enabled = False` | PC-2 + calibration |
| Reference judge | `judge.enabled = False` | PC-2 + calibration |
| Best-of-N | `best_of_n = 1` | corp tuning |
| Trust gate | `recall_floor = 0.0` (inert) | reactions calibration |
| Embedding thread-merge | `threading.embedding_merge = False` | cosine calibration |
| Mattermost ingest | `mm_source.enabled = False` | PAT + corp |
| DM ingest | `mm_source.dm_scope = off` | explicit consent |
| MCP server | opt-in `[mcp]` extra | install + consent |

## 5. The critical path (one corp session)

The ~30-point built→live gap is **not spread evenly** — it concentrates in streams 2, 3, 4, which
share one unlock. In order:

1. **Write PC-2** — the per-endpoint data-handling ADR (the master gate). Needs corp-policy facts;
   the technical content is already in ADR-014/015.
2. **Run the digest live** — prove the EP-14 validation pack + the store on the real EWS/MM/gateway/
   Linux stack (closes the "never run in production" risk).
3. **Deliver in api-mode for ~1–2 weeks** — real recipients react ✓/✗ on owner-only posts.
4. **Harvest → calibrate** — `reactions harvest` → `eval-gold` → `eval-calibrate` → set
   `recall_floor > 0` and flip `reranker`/`judge`/relevance.

That sequence moves Extract-&-Trust ~20→70, Remember-&-Retrieve ~40→80, Deliver ~35→75 — i.e. it
turns the trust promise from *asserted* to *measured*, with **zero new features**. The highest-value
remaining work is a **calendar event, not a backlog**.

## 6. Recent work log (2026-06-19)

Three PR waves landed this day, all squash-merged to `main`:

- **Inbox-API + MCP program** (#149–#162) — store retrieval primitives → `InboxAPI` →
  `actionpulse-mcp` server → macOS installer → report-enrichment routing → ADR-014/015; then a
  post-ship adversarial round (redaction-leak fix, source verbs, resource-URI fix, discoverability)
  and an "artifact-complete" round (onboarding truth-fixes, doc sweep, wizard store step).
- **Multi-angle review remediation** (#163–#167) — RV1 timezone/date correctness · RV2 network
  resilience + honest degradation · RV3 doc/version truth-pass · RV4 coverage gate +
  DM-redaction-at-tool-boundary test · RV5 UX (menu search/ask, hide `eval-*`, glyphs).
- **Architecture refactor** (#168–#172) — `from_config` factories · source-adapter dispatch ·
  config env-flag dedup · `run.py` snapshot extraction · JSON-list dedup + dead-code deletion.

## 7. Deferred (consciously not done)

- **run.py idempotency / degrade extractions** — entangled with `_sanitize_config` /
  `_artifact_age_hours` / `PIPELINE_VERSION` (used elsewhere); a clean split needs shared modules
  with real circular-import risk. The god-module coupling is partly inherent.
- **`NormalizedMessage` → `ingest/models.py`** (11 importers) and the **`EnhancedDigest` /
  `process_digest`** dead subsystem (still test-coupled) — larger cleanups than warranted now.
- **Wizard `MM_PAT` / api-delivery prompts** — EWS is primary; webhook delivery works; secret
  *preservation* on re-run already landed.
- **Everything in §5** — corp-network-only; cannot be validated offline.
