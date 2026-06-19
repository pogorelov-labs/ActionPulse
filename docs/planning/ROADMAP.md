# ActionPulse — Product Roadmap

> **What this is:** the *forward* product plan — current state, the coherent product
> shape, and what we build next. Updated **2026-06-19**.
>
> **Source-of-truth map** (don't trust prose over code):
> - Contracts / ADRs / pipeline → `digest-core/docs/ARCHITECTURE.md` (verify §13/§14 against code — they lag).
> - Requirements / principles / unified data schema v3.0 → `docs/planning/BUSINESS_REQUIREMENTS.md`.
> - Mattermost (the live SoT) → `digest-core/docs/research/MATTERMOST_INTEGRATION_DESIGN.md`.
> - Terminal/UX program → `docs/development/TERMINAL_DESIGN_ROADMAP.md`.
> - Quality program (EP-1…EP-15) → `digest-core/docs/audits/`.
> - Corp bring-back lists → `digest-core/docs/VISIT_CHECKLIST_EP14.md`, `digest-core/docs/STORE_VALIDATION_CHECKLIST.md`.
>
> The original LVL1–LVL5 vision (this file's prior content) is **largely delivered through LVL4**;
> its requirements live in `BUSINESS_REQUIREMENTS.md` and git history.

## 1. Where we are (honestly)

ActionPulse is no longer an email-digest MVP. It is a fairly complete **privacy-first
personal communications-intelligence tool**: an 8-stage pipeline (ingest → normalize →
threads → evidence → select → LLM extract → assemble → deliver) over **two sources**
(Exchange/EWS email + Mattermost mentions / allowlisted channels / consent-gated DMs),
evidence-traced extraction, and — new — an **encrypted searchable corpus**.

The real gap forward is **not features**. It is **activation, proof, and a closed feedback
loop**. A large fraction of the most powerful capability is **built and shipped but switched
OFF**, gated behind two things that haven't happened yet: **corp validation** (the PC-2
data-handling ADR) and **calibration**.

**Built-but-dark (default off, pending corp validation / calibration):** reranker support
tier, fused relevance scoring, LLM reference-anchored judge, best-of-N extraction,
embedding thread-merge, the `recall_floor` citation gate (still 0.0 → annotate-only),
consent-gated DM ingest, and the entire message store. **Unproven live:** the store
(merged #141/#143) has never run against the real EWS/MM/gateway/Linux stack.

## 2. The product, coherently — three pillars on a privacy spine

- **① Capture** — sources. EWS email + Mattermost (mentions / allowlisted channels /
  consent-gated DMs), incremental per-source watermark, multi-source seam. *Built; gaps:
  chat-tuned extraction prompt, cadence/real-time.*
- **② Extract & Trust** — the quality program. Verbatim evidence spans, citation gate
  (shadow→quarantine→repair), best-of-N, reranker tier, reference judge, gold/τ
  calibration harness. *Built but DARK — corp + calibration gated.*
- **③ Remember & Retrieve** — the new store. SQLCipher-encrypted 30-day corpus → FTS5 +
  brute-force-cosine + RRF hybrid search → (next) `ask`/RAG → cross-digest history.
  *Newest; live-unproven, ask-layer unbuilt.*
- **Spine (cross-cutting, strong):** privacy / consent / retention; delivery (webhook +
  owner-only api mode); observability / eval / calibration; terminal UX / setup.

## 3. The flywheel — the strategic core

The whole product is one virtuous loop that is **~80% built and not yet closed**:

```
deliver digest (api mode, capture post_ids) → users react ✓/✗
  → harvest reactions → gold set → calibrate recall_floor + judge gate
  → extraction goes from "annotate-only" to MEASURED & gated
  → better digests → more reactions  ↺
```

Closing it turns *"we assert the digest is trustworthy"* into *"trust is measured and
self-improving."* The only missing links are a **`delivered-posts.jsonl` ledger + reaction
harvest** (small offline build) and **one corp calibration run**. This is the highest
*strategic*-value work on the board.

## 4. Phased roadmap

**Phase 0 — Truth & tidy** *(offline, now).* Replace this doc's stale prose with the plan
above; correct ARCHITECTURE §16 fictional masking claim + banner §13/§14; banner the
superseded `MATTERMOST_INTEGRATION.md`; sweep the drifted Plane ACTPULSE board; rotate the
exposed MM PAT + add `pat`/`bearer` to log redaction. *Cheap; makes everything after it
honest and plannable.*

**Phase 1 — Activate the dark capabilities** *(corp, 1–2 supervised sessions).* Run the
**EP-14 validation pack** + the **store validation checklist**; write the **PC-2
data-handling ADR**; with evidence, flip on reranker / fused relevance / judge / best-of-N,
validate EN extraction, prove the store live. *One or two sessions convert ~10 built-but-dark
features into real value — highest ROI.*

**Phase 2 — Close the flywheel** *(corp + small build).* Build the delivered-posts ledger +
reaction harvest; take api-delivery live; run **EP-15** calibration → set `recall_floor > 0`
and flip the judge gate. *Trust becomes measured and self-improving.*

**Phase 3 — The memory pillar** *(mostly offline, high UX payoff).* Ship `actionpulse ask
"<question>"` as RAG over the store's hybrid search (the store **removed the old blocker** —
no longer needs fleet retrieval/PC-2); add a store-backed "search across 30+ digests"
history browser. *The surface the store was built for.*

**Phase 4 — Reach & depth.** MM chat-tuned extraction prompt + corp A/B (convert the chat
investment into delivered value); cadence / real-time "urgent nudge" (Track B REST poll);
least-privilege **bot** delivery identity (vs personal PAT); slash commands; multi-user /
productization; Docker-Compose deploy.

## 5. Open backlog by stream (condensed; IDs where they exist)

**① Capture / Ingest** — MM chat-extraction prompt (`extract_actions.chat.*`); PR12a
reranker-pairwise band + LLM-adjudication thread-merge tiers (gated on C6 cosine-threshold
calibration); cross-source thread-merge surfacing (`duplicate_sources`); ADR-004 EWS
SyncFolderItems; TF-IDF topic clustering; real-time intraday path (MM_DESIGN §5).

**② Extract & Trust** — **EP-14** corp validation pack (HIGH, checklist ready); **EP-15**
recall_floor calibration + judge gate-flip (needs reactions + EP-14⑦); fleet live-flag flips
(`reranker.enabled` / `enable_relevance` / `judge.enabled`, all PC-2-gated); mention/"My
Actions" personalization (alias dict + RU declensions + dedicated section); EN-extraction
quality unmeasured (C1/L2).

**③ Remember & Retrieve** — **store corp validation** (checklist); **`ask`/RAG over store**
(Phase 3 — newly unblocked); store-backed cross-digest history browser.

**Delivery** — per-section threading + **`delivered-posts.jsonl` ledger** (EP-15 prereq);
reaction harvest; least-privilege bot identity (decision); slash commands; overflow "and N
more" cap; Docker-Compose; MM file-upload for `export-diagnostics --send-mm`.

**Privacy / Consent / Retention** — correct ARCHITECTURE §16 fictional masking; **PC-2**
per-endpoint data-handling ADR (master gate); **PC-1** service-account model access;
`--dump-ingest` retention hole (dev-only + DM exclusion); rotate exposed PAT + log-redaction;
optional local masking fallback.

**Terminal / UX / Setup** — **U8 "ask your inbox"** (now via the store); L2 corp EN
validation; L3 docs translation; corp UX checks C2–C5; slash-command UX.

**Observability / Eval** — EP-11 continuous failure→gold→issue loop; OTel collector endpoint
decision; TD-006 enforce `llm.cost_limit_per_run` (USD cap unenforced; token budget *is*
enforced).

## 6. Cross-stream dependencies (the gates)

- **PC-2 ADR is the master gate** — blocks every fleet live-flag, EP-14 probes ②④⑤, EP-15
  calibration, and the store's `reembed` against the real gateway. Nothing in the
  fleet-quality cluster goes live without it.
- **Calibration chain:** api-delivery-live → `delivered-posts.jsonl` → reaction harvest →
  `eval-gold` → `eval-calibrate` → `recall_floor > 0` → judge gate-flip. EP-14⑦ seeds the κ
  floor EP-15 needs.
- **U8 `ask`/RAG was blocked on fleet retrieval (PC-2); the merged hybrid store unblocks it**
  — a re-sequencing win (Phase 3 can run before Phase 1).
- **MM chat-prompt A/B, EN "production-grade", store live-validation** all need a corp day.

## 7. Top bets (value ÷ effort)

1. **`ask`/RAG over the store** — now offline-buildable, biggest *new* UX surface, leverages
   what we just built.
2. **Phase 0 truth/tidy** — near-free; removes actively-misleading docs.
3. **EP-14 corp activation** — one session lights up ~10 dark features.
4. **Close the flywheel** (delivered-posts ledger + harvest → EP-15) — makes quality
   measurable/self-improving.
5. **MM chat prompt** — turns the chat-ingest investment into delivered value.
