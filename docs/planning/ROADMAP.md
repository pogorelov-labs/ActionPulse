# ActionPulse — Product Roadmap

> **What this is:** the *forward* plan — per-stream plans, integrated into a phased roadmap.
> Updated **2026-07-30** (was 2026-06-19).
>
> **Read §0 first.** The 2026-06-19 roadmap below was built on a diagnosis that turned out to be
> wrong, and it predates four merged PRs, a nine-night CI outage, and a locked owner decision that
> reverses one of its constraints. §0 states what changed; §§1–7 are kept because the per-stream
> detail is still good, but where §0 and a later section disagree, **§0 wins**.
>
> **Where we are now** (stream-by-stream % built vs live, the dark inventory) → [`STATUS.md`](./STATUS.md).
>
> **What to research before building next** (product/UX/architecture agenda) → [`RESEARCH_SPEC.md`](./RESEARCH_SPEC.md).
>
> **What we decided to build** (post-research, tiered, with the Mattermost-bot architecture) → [`PROPOSAL.md`](./PROPOSAL.md).
>
> **Source-of-truth map** (don't trust prose over code):
> - Contracts / ADRs / pipeline → `digest-core/docs/ARCHITECTURE.md` (verify §13/§14 against code — they lag).
> - Requirements / principles / unified data schema v3.0 → `docs/planning/BUSINESS_REQUIREMENTS.md`.
> - Mattermost (the live SoT) → `digest-core/docs/research/MATTERMOST_INTEGRATION_DESIGN.md`.
> - Terminal/UX program → `docs/development/TERMINAL_DESIGN_ROADMAP.md`.
> - Quality program (EP-1…EP-15) → `digest-core/docs/audits/`.
> - Corp bring-back lists → `digest-core/docs/{CORP_SESSION_RUNBOOK,VISIT_CHECKLIST_EP14,STORE_VALIDATION_CHECKLIST}.md`.

## 0. Amendment — 2026-07-30

### 0.1 The diagnosis that was wrong

Everything below is organized around "one corp session" as the pivot. Asked directly, the owner
confirmed: **corp sessions have been run several times.** What never happened is the **round
trip** — no session wrote its results back into the repo, so `STATUS.md` reported "never validated
live" for six weeks while live runs were happening.

So the gate was never *access*. It was that a corp session had **no defined output artifact**. The
replacement gate is offline-buildable and now exists:
[`digest-core/docs/CORP_AGENT_BRIEF.md`](../../digest-core/docs/CORP_AGENT_BRIEF.md) — a
self-contained brief a corp-side agent executes end-to-end (pull repo → `make test` → validation
pack → **PR or issue comment**), with reports landing in `digest-core/docs/corp-runs/`.

**Highest-leverage item on this roadmap: get one corp run to produce one report file.** Everything
in Phase C is downstream of that, and it is a *protocol* problem, not a scheduling one.

### 0.2 The constraint that was reversed

The evidence-token budget and the per-run LLM-call count are **policy, not hard limits** (owner,
2026-07-04 as ACTPULSE-77; re-confirmed 2026-07-30). This reverses `REDESIGN_PLAN.md` §0.3.1 #4
("hold ~7K"), which was written the same week. The **retrieval spine stays** — evidence spans,
citations, the P2 gate. What is lifted is *how much* we retrieve and *how many* calls we spend.

Guard rail: **ACTPULSE-86 (calibrate before raising defaults) is not optional.** "Cost is no
object" removes the cost argument for restraint, not the correctness one. Full amendment:
`REDESIGN_PLAN.md` §0.3.7.

### 0.3 The four waves, sequenced

All four were selected as in-scope (owner, 2026-07-30). They are **ordered by what each unblocks**,
not by appeal:

| # | Wave | Why here | Key items |
|---|------|----------|-----------|
| **W1** | **Stabilize + tell the truth** | Everything else is measured against a baseline. Right now the baseline is *assumed* — the suite went red for nine nights unnoticed, and the forward plan lived only in an uncommitted file. Cheapest wave; unblocks trust in every later measurement. | Glob flake ([#212](https://github.com/pogorelov-labs/ActionPulse/pull/212) ✓) · nightly-failure notification (**ACTPULSE-88**) · commit the review + v0.3 (✓) · doc-truth fixes (**ACTPULSE-78**) · **the corp brief** (§0.1) |
| **W2** | **Shed legacy + refactor** | Do this *before* W3, not after. W3 rewires the extraction path; doing that on top of three schema generations and a 2,474-line `run.py` multiplies the blast radius. `#208` already proved the pattern. | REDESIGN_PLAN **B1b** (delete `process_digest` + its whole connected component) · **B2** schema collapse · **B3** one scoring pass / one budget gate · **B4** stage-protocol `run.py` · **B5** project structure |
| **W3** | **Extraction modernization** | The capability wave, and the one the budget lift is *for*. Ordered inside itself: **A1 constrained decoding first** — map-reduce merges many partial extractions, which is only safe when each is schema-conformant by construction. | **A1** (half-built on `feat/constrained-v3-extraction`, unmerged) · **A3**/ACTPULSE-80 tokenizer · ACTPULSE-79 un-cap · ACTPULSE-81 single-large-context · ACTPULSE-82 map-reduce · ACTPULSE-83 adaptive router · ACTPULSE-84 visibility · ACTPULSE-85 RateBroker-for-large-N · **ACTPULSE-86 calibration gate** |
| **W4** | **Deepen the daemon / product surface** | ADR-016 (#209) quietly changed the product from a batch job into a background service with a continuously-fresh store — and **no planning doc had caught up**. Last because it compounds best on a modernized, measured pipeline. | Schedule the **digest** (the daemon only schedules *ingestion*) · Linux/systemd parity · daemon soak (**T6** in the brief) · lean into always-fresh-store + MCP |

**Two reversals inside W3, inherited from the budget lift.** REDESIGN_PLAN **A4** said "right-size
the `RateBroker` down"; with map-reduce and a lifted call count, real concurrency arrives and the
broker becomes load-bearing instead (ACTPULSE-85). And **A1 is a prerequisite, not a peer** — see
the W3 row.

**W1 and the corp brief run in parallel with everything.** The corp round trip is not a phase; it
is a standing protocol, and the brief is offline work.

### 0.4 What landed since this roadmap was written

#208 (B1a dead-code shed, −2,596 lines) · #209 (**background ingestion daemon**, ADR-016) · #210
(date-rot fixture fix) · #211 (nightly CI) · #212 (glob flake + corp-bulk gitignore). `main` was
otherwise **dormant for 25 days**. Detail and the CI-outage post-mortem: [`STATUS.md`](./STATUS.md) §7a.

---

## 1. The thesis

ActionPulse is **~87% built, ~58% live** (per-stream in [`STATUS.md`](./STATUS.md)). The forward
gap is **not features — it is activation, proof, and a closed feedback loop.** A large fraction of
the most powerful capability is shipped but switched **off**, gated on two things that have not
happened: **corp validation** (the PC-2 data-handling ADR) and **calibration** (one corp run).

So this roadmap is organized around a single pivot — **the corp activation cycle** — with offline
work staged before it (to finish + prepare) and after it (to deepen). Phase tags below:
**✓** done · **A** offline, now · **B** the corp cycle · **C** calibrate & flip · **D** depth.

## 2. The product, coherently — three pillars on a privacy spine

- **① Capture** — EWS email + Mattermost (mentions / allowlisted channels / consent-gated DMs).
- **② Extract & Trust** — verbatim evidence, citation gate, reranker, judge, best-of-N, calibration.
- **③ Remember & Retrieve** — the encrypted store → hybrid search → `ask`/RAG → InboxAPI + MCP.
- **Spine** — privacy / consent / retention · delivery + the reactions flywheel · observability / eval · terminal UX.

## 3. Per-stream plans

Each stream: its objective, then ordered milestones with phase tags.

### Stream 1 · Capture (ingest) — *two proven, low-friction sources*
- **✓** EWS (NTLM/TLS/retry/timeout/folders/watermark) + Mattermost (channels, consent-gated DMs, AIMD) + multi-source seam + dump/replay.
- **B** Prove EWS **and** MM ingest live on the real stack (MM ingest has never run in production).
- **D** MM chat-tuned extraction prompt (`extract_actions.chat.*`) + corp A/B.
- **D** Cadence / real-time intraday "urgent nudge" (Track B REST poll, MM_DESIGN §5).
- **D** Cross-source thread-merge surfacing (`duplicate_sources`); ADR-004 SyncFolderItems; TF-IDF topic clustering.

### Stream 2 · Extract & Trust — *quality that is measured, not asserted* ← biggest live-gap
- **✓** Evidence spans, citation gate (shadow→quarantine→repair), reranker tier, judge, best-of-N, gold/τ harness — all built, all dark.
- **B** Run the **EP-14 validation pack** (checklist ready): items/section, `support_recall`, weak/quarantined counts, the verbatim-quote invariant; EN-vs-RU comparison.
- **B** Measure **EN-extraction quality** (C1/L2) — the default output path is currently unmeasured.
- **C** **EP-15**: set `recall_floor > 0`; flip `reranker.enabled` / `enable_relevance` / `judge.enabled` (PC-2-gated); tune `best_of_n`.
- **D** Mention / "My Actions" personalization (alias dict + RU declensions + dedicated section).

### Stream 3 · Remember & Retrieve (store · API · MCP) — *a live, queryable memory*
- **✓** Store (SQLCipher + FTS5 + cosine + RRF), InboxAPI, MCP server + installer, `ask`/RAG, carryover/pending (#141–#162).
- **A** Cross-digest **history browser** ("search across 30+ digests") — the biggest *new* offline UX surface.
- **B** Store **live validation** (`STORE_VALIDATION_CHECKLIST`): `reembed` against the real gateway; semantic / `ask` / carryover on real mail.
- **D** Embedding thread-merge live (C6 cosine-threshold calibration); deeper retrieval ranking.

### Stream 4 · Deliver + reactions flywheel — *a closed, self-improving trust loop*
- **✓** Webhook + api-mode delivery; **delivered-ledger wired** into `run.py` (now carries `trace_id`); **`reactions harvest --gold-out`** bridges harvest → the `eval-gold` JSONL (one shared emoji vocab, no silent drop); `eval-gold` / `eval-calibrate` — the engine is built **and connected**.
- **✓** Synthetic end-to-end verification of `ledger → harvest → bridge → eval-gold → eval-calibrate` (`test_flywheel_harvest_to_calibrate_end_to_end`) — the engine is proven before fueling.
- **B** Deliver in **api-mode for ~1–2 weeks** → reactions accumulate via the ledger.
- **C** Harvest → calibrate → `recall_floor > 0` + judge flip — **the loop closes** (feeds Stream 2 / C).
- **D** Least-privilege **bot** delivery identity; slash commands; per-section threading; overflow "and N more" cap.

### Stream 5 · Terminal UX / setup / onboarding — *frictionless first-run + daily use*
- **✓** Wizard (+ store step), launcher menu (+ search/ask rows), reader, design system + conformance CI, global command.
- **A** Wizard prompts for `MM_PAT` / api-mode delivery (the last coverage gap).
- **B** Corp UX checks **C2–C5** (256-color, light bg, `NO_COLOR`, `--progress` modes).
- **D** History-browser UI (with Stream 3); slash-command UX.

### Stream 6 · Privacy / Consent / Retention — *a defensible, documented posture*
- **✓** Fail-closed DM-at-rest redaction (structural), consent gate + UX, retention knobs, secrets-ENV-only, log + dump redaction.
- **✓/B** PC-2 per-endpoint data-handling ADR — the master gate. **Drafted offline** (`digest-core/docs/PC2_DATA_HANDLING.md`: per-endpoint data-flow table + controls + default-deny framework); **fill the `<TBD>` corp-policy statements at the session**, then flip Status → ACCEPTED.
- **A** Rotate the exposed MM PAT; add `pat` / `bearer` to log redaction; correct ARCHITECTURE §16 fictional-masking claim.
- **B** PC-1 service-account model-access decision.
- **D** Optional local-masking fallback.

### Stream 7 · Observability / Eval / QA — *trustworthy measurement; green = correct*
- **✓** structlog / Prometheus / healthz / OTel; `eval-replay` gate; **coverage gate (86%, CI-enforced)**.
- **A** `install.sh` shellcheck lane; a real subprocess/stdio MCP e2e; enforce **TD-006** (`cost_limit_per_run`).
- **C** Real corp **P/R/F1** from the calibration run (the first true quality numbers).
- **D** EP-11 continuous failure→gold→issue loop; OTel collector endpoint decision.

### Stream 8 · Docs / Architecture / Contribution — *docs that stay true to code*
- **✓** Truth-pass, ADR-014/015, CONTRIBUTING (preflight/extras/lanes), CHANGELOG, STATUS.md (this session).
- **A** Relocate the dead `HIERARCHICAL_ORCHESTRATION` doc to `legacy/`; consolidate the two `docs/` trees; fix ARCHITECTURE §13/§16 stale tables.
- **D** L3 RU→EN translation backlog; multi-user / productization + Docker-Compose deploy docs.

## 4. Integrated phased roadmap

The streams are **not parallel tracks** — they're gated by one shared dependency (PC-2 + the corp
run). Read top-to-bottom; **Phase B is the pivot.**

### Phase A — Finish & prepare offline *(now; no corp network; ~days)*
Everything offline-buildable, front-loaded so the corp session is pure execution.

> **✓ Shipped (#175–#179):** cross-digest history browser · cost-cap enforcement (TD-006) ·
> bearer/PAT log redaction · wizard MM-creds collection · shellcheck CI lane.
> **Remaining in Phase A:** the **PC-2 ADR** is drafted (`digest-core/docs/PC2_DATA_HANDLING.md`) —
> only the corp `<TBD>` answers remain (Phase B); corp-activation runbook (S8); rotate the
> exposed PAT + §16 masking correction (S6); verify the flywheel on synthetic data (S4); the
> subprocess/stdio MCP e2e (S7); docs relocation/consolidation (S8).

### Phase B — The corp activation cycle *(the pivot; 1 supervised session + ~2 weeks passive)*
The single sequence that converts the dark inventory to live.
1. Finalize **PC-2** with corp facts (S6).
2. Prove **ingest live** — EWS + MM (S1); prove the **store live** — reembed, semantic, `ask`, carryover on real mail (S3).
3. Run the digest live: **EP-14 validation pack**; measure **EN-extraction** quality (S2, S7); corp UX checks C2–C5 (S5).
4. **Deliver in api-mode for ~1–2 weeks** → reactions accumulate (S4).

### Phase C — Calibrate & flip *(post-cycle; offline analysis + flag flips)*
The moment dark → live for the differentiators.
- **Close the flywheel:** harvest → `eval-gold` → `eval-calibrate` → set `recall_floor > 0`; flip reranker / relevance / judge; tune best-of-N (S4 → S2).
- Publish the first real **P/R/F1** (S7).

### Phase D — Reach & depth *(post-activation; mostly offline)*
- MM chat-tuned prompt + A/B (S1/S2/S4); cadence/real-time (S1).
- Bot identity, slash commands, per-section threading (S4).
- Embedding thread-merge live (S3); personalization (S2).
- L3 translation, multi-user/productization, Docker-Compose (S5/S8).

## 5. The flywheel — the strategic core (engine built, needs fuel)

```
deliver (api mode, capture post_ids) → users react ✓/✗ → reactions harvest
  → eval-gold → eval-calibrate → recall_floor > 0 + judge gate
  → extraction goes from "annotate-only" to MEASURED & gated → better digests ↺
```

Unlike earlier roadmaps, the **ledger + harvest + calibrate are already built and wired** (`run.py`
records delivered posts; `actionpulse reactions harvest`; `eval-gold`/`eval-calibrate`). The loop's
only missing link is **fuel**: one corp api-delivery window (Phase B) + the calibration analysis
(Phase C). This is the highest *strategic*-value work — and it needs **zero new engine code**.

## 6. Gates & dependencies

- **PC-2 ADR is the master gate** — blocks every fleet live-flag, the EP-14 probes, EP-15
  calibration, and store `reembed` against the real gateway. Draft it in Phase A so Phase B isn't
  blocked on writing.
- **Calibration chain:** api-delivery-live → ledger → harvest → `eval-gold` → `eval-calibrate` →
  `recall_floor > 0` → judge flip (Phase B → C). EP-14⑦ seeds the κ floor EP-15 needs.
  **C4-research caveat:** reactions calibrate *precision/the judge* but are **recall-blind**
  (survivorship — only delivered items get thumbs); a defensible recall floor needs a separate
  **human-audited random sample** + a Wilson/Clopper–Pearson lower bound, and a prevalence-aware
  judge κ. See [`RESEARCH_SPEC.md`](RESEARCH_SPEC.md) §C4 + [`research/SYNTHESIS.md`](research/SYNTHESIS.md).
- **Corp-network-only** (cannot be done offline): live ingest proof, EN "production-grade", store
  live-validation, the api-delivery window. Everything else in Phases A/C/D is offline.
- **Independent of the gate:** the history browser, all of Stream 7/8 Phase-A items, and the
  Stream-5 wizard gap — buildable now, in parallel with drafting PC-2.

## 7. Sequencing — top bets (value ÷ effort)

1. **Draft PC-2 + polish the corp runbook (A)** — unblocks the entire pivot; offline; the single
   highest-leverage move.
2. **The corp activation cycle (B)** — one session lights up ~10 dark features; the only path to
   *measured* trust.
3. **Close the flywheel (C)** — turns quality from asserted to self-improving.
4. **Cross-digest history browser (A)** — the biggest new offline UX, leverages the shipped store.
5. **MM chat prompt + A/B (D)** — converts the chat-ingest investment into delivered value.

**Bottom line:** the offline build is essentially done. The remaining progress is **one corp day**
(Phase B) plus a short calibration pass (Phase C) — preceded by a few days of offline prep (Phase
A). Everything in this roadmap after that is depth, not unlock.

> **Superseded 2026-07-30 — see §0.** Two claims here are now known false. "The offline build is
> essentially done": ACTPULSE-77's nine children (W3) and REDESIGN_PLAN Phase B (W2) are a
> substantial *offline* programme opened after this was written. "One corp day": corp days have
> already happened — the missing piece is a **report artifact**, not a session (§0.1).
> The accurate bottom line: **W1 → W2 → W3 → W4, with the corp round trip running alongside.**
