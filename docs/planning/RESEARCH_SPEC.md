# ActionPulse Enhancement Research Specification

> **🔬 Deep research landed 2026-06-20** → see [`research/SYNTHESIS.md`](research/SYNTHESIS.md)
> (cross-cutting findings + the full correction list) and the 8 per-item briefs in
> [`research/`](research/). Items below are annotated `🔬` where research changed them; the
> thesis held, with C3 (transport) and C4 (the flywheel's recall claim) materially corrected,
> and two new items added (see §5a). The A8/A11 *memos* are updated to research-verified v2.

> **✅ Build decisions (2026-06-20)** → [`PROPOSAL.md`](PROPOSAL.md). Tier 0 (build now): **A**
> facade parity (C1) + **B** injection defense (C11). Tier 1: **D** Mattermost bot (B1) —
> *architecture decided: connect-out, per-user, no server*; **C** action loop (A1/C2) — **parked**.
> Tier 2: **E** EWS Calendar (A9, read-side) + **F** scheduled queries (A6). Tier 3: **G** C4 (corp).

> **Status:** draft v2 for review · **Date:** 2026-06-20 · **Scope:** the three product
> surfaces — `InboxAPI`, the `actionpulse-mcp` MCP server, and the terminal (CLI + menu).
>
> A **research agenda**, not a build plan. Each item carries a question, a hypothesis,
> a protocol, **inputs**, a **quantitative success metric**, a **decision gate**, a
> **priority**, a **size**, and where it must run. §1 is a one-page executive summary;
> §3–§5 expand every item. Sibling docs: [`STATUS.md`](STATUS.md),
> [`ROADMAP.md`](ROADMAP.md),
> [`ARCHITECTURE.md`](../../digest-core/docs/ARCHITECTURE.md),
> [`PC2_DATA_HANDLING.md`](../../digest-core/docs/PC2_DATA_HANDLING.md).

## 0. Conventions

- **Priority:** `P0` highest leverage / unblocks others · `P1` high · `P2` opportunistic.
- **Size (research effort, not build):** `S` ≤1 wk · `M` 2–3 wks · `L` 4–6 wks.
- **Where it runs:** `🟢` offline (laptop + synthetic/replay data) · `🧪` dogfood (the
  maintainer's own inbox) · `🏢` corp (needs the corp network / live data — batch into a
  [corp session](../../digest-core/docs/CORP_SESSION_RUNBOOK.md)).
- `↔` = shared decision gate with another item. Nothing here is greenlit to build;
  outputs revise [`ROADMAP.md`](ROADMAP.md).

---

## 1. Executive summary (read this first)

**Starting point.** ActionPulse is a **read-only, pull-based, single-user,
evidence-traced** digest. `InboxAPI` (~24 verbs) is the facade; the MCP server
re-exposes 24 of them; the terminal offers ~29 commands + a 12-row menu. Delivery is
one-way to Mattermost; the powerful intelligence (reranker, judge, fused relevance,
best-of-N, embedding thread-merge, the `recall_floor` gate) is **built but dark**,
gated on PC-2 + calibration.

**The thesis — three structural gaps** (revised v2; the embedding/off-corp gap is
retired — see Non-goals):

1. **No action loop.** Every verb is read/retrieve/maintain. You can *see* "Ivan is
   waiting on you" but cannot snooze / mark-done / draft-reply / create-task from any
   surface. The product ends where triage begins.
2. **Interaction is terminal-bound & pull-only.** The digest *arrives* in Mattermost
   (any device) but `ask`/`search`/`history` work only at the terminal that ran it.
   No proactive/push intelligence beyond the scheduled drop.
3. **Intelligence is heuristic + un-personalized + dark.** Ranking is static config
   weights; the per-user calibration flywheel is built but never spun; the fleet is
   PC-2-gated. Output quality (esp. EN-by-default extraction) is unvalidated on real mail.

**The leverage map.** First wave (P0): **A1** the action loop (sets the ceiling on
everything), **B1** the Mattermost bot (reaches the user where the digest already
lives), **C1** the facade-parity contract (stops surface drift). These reinforce each
other — B1 stresses C1; A1 caps the value of both.

**All items at a glance:**

| ID | Item | Pri | Size | Run |
|----|------|-----|------|-----|
| A1 | The action loop (snooze/done/task/reply) | P0 | M | 🧪 |
| A2 | Per-user ranking from reactions | P1 | M | 🟢🏢 |
| A3 | Proactive "what changed" signals | P1 | S–M | 🧪 |
| A4 | Cross-source entity & topic resolution | P2 | M | 🟢🏢 |
| A5 | Retrospective / analytics (weekly, trends) | P2 | S | 🧪 |
| A6 | Conversational / saved / scheduled `ask` | P1 | M | 🧪🏢 |
| A7 | Evidence-tracing → trust → adoption | P1 | M | 🏢 |
| A8 | Competitive positioning / the wedge | P1 | S | 🟢 |
| A9 | Source breadth (calendar/Jira/Slack/docs) | P1 | M | 🧪🟢 |
| A10 | EN-default extraction quality vs RU | P1 | M | 🏢 |
| A11 | Business / deployment model | P1 | S | 🟢 |
| B1 | Mattermost bot / 2nd interaction surface | P0 | M | 🟢🏢 |
| B2 | Lowest-friction in-digest feedback gesture | P1 | S–M | 🏢 |
| B3 | Digest scanability & the section taxonomy | P1 | M | 🧪 |
| B4 | First-run success & wizard friction | P1 | S | 🧪🏢 |
| B5 | Power-feature discoverability | P2 | S | 🧪 |
| B6 | Reader & explain surface utility | P2 | S | 🧪 |
| B7 | Cadence & the empty-digest experience | P2 | S | 🧪 |
| B8 | Trust UI (citations/badges/redaction) | P1 | M | 🏢 |
| C1 | Facade parity + cross-surface contract test | P0 | S–M | 🟢 |
| C2 | The write/action architecture | P1 | M | 🟢 |
| C3 | MCP HTTP/SSE transport + auth model | P1 | M | 🟢🏢 |
| C4 | Safe fleet-activation state machine | P1 | L | 🟢🏢 |
| C6 | Multi-user / deployment topology | P2 | S | 🟢 |
| C7 | Cross-source store schema | P2 | M | 🟢 |
| C8 | Observability / SLOs in practice | P2 | S–M | 🏢 |
| C9 | Cost / latency envelope at volume | P2 | S | 🟢 |
| C10 | DPIA / threat model depth | P1 | L | 🟢🏢 |

*(C5 "local embeddings" was dropped in v2: gateway-only embeddings are accepted —
semantic retrieval is corp-bound by design. See §8.)*

---

## 2. Research method under corp constraints

"Code outside, run inside, debug outside" (ADR-012). EWS + the LLM gateway are
corp-only; Mattermost is reachable everywhere. That shapes each instrument:

- **🟢 Offline-first.** Prototype against synthetic corpora and `--replay-ingest` /
  `--replay-llm` snapshots; all architecture spikes and most UX prototypes live here.
- **🧪 Dogfood diary.** The maintainer's daily run is the primary qualitative
  instrument — a structured log of *what I did after reading the digest* (A1/A3),
  *what I looked for and couldn't find* (A5/A9/B5), *where I got stuck* (B4).
- **🏢 Corp session = the live lab.** Quantitative quality, recall, latency, and the
  reactions flywheel can only run inside; batch them (runbook §10). The flywheel
  (deliver→react→calibrate) is the **measurement substrate** for A2/A7/A10.
- **No-access studies:** competitive teardown (A8), comprehension (B3), business model
  (A11), most architecture spikes.
- **Privacy by construction:** any study touching real content obeys the golden rules
  (no payloads/secrets logged; DM redaction at rest; PC-2 before any new egress).

---

## 3. Track A — Product

### A1 · The action loop  `[P0 · M · 🧪]`  ↔C2
- **⏸ Parked (2026-06-20, [PROPOSAL](PROPOSAL.md)).** Deferred. When revived: build the
  local-only subset first (`Done`≈dedup-suppression, `Snooze`≈carryover); `Send-to-task` is
  outbound → a separate on-prem-only posture decision. C is the **prerequisite for the outbound
  calendar features** (cancel/reschedule/propose/forward in E).
- **Q.** Should the digest let you *act* on items (snooze, mark-done, create-task,
  draft-reply, delegate) instead of only reading them?
- **Why.** Triage is the missing half of the loop; a read-only digest caps daily value
  at "awareness." This is the single highest-leverage product question — it sets the
  ceiling for B1/B2/C2.
- **Protocol.** (1) Two-week dogfood diary: after each digest, log every action taken
  and on which item; (2) rank actions by frequency × friction-removed; (3) build one
  *throwaway* offline prototype of the top action (e.g. snooze→carryover suppression,
  done→dedup-ledger suppression).
- **Inputs.** Dogfood diary; carryover/pending + dedup-ledger seams.
- **Metric.** The top 3 actions cover ≥70% of logged post-read activity; the prototype
  removes ≥1 manual step per use. If <2 actions/day are logged, the loop is low-value → skip.
- **Gate.** Define the **action model** (triage tool vs. read digest) → scopes C2.

### A2 · Per-user ranking from reactions  `[P1 · M · 🟢→🏢]`  ↔C4
- **Q.** Can reaction signal train a *per-user relevance* model, not just the citation gate?
- **Why.** The flywheel already harvests ack/nack per evidence id; the same signal could
  re-weight `selection_weights` (today static).
- **Protocol.** Re-use the harvested gold set; offline experiment comparing learned
  weights vs. static weights on held-out reactions; ablate signal volume needed.
- **Inputs.** The reactions gold set; `eval-*` harness; `selection_weights`.
- **Metric.** Learned weights beat static by ≥0.05 NDCG@k (or ≥0.05 F1) on held-out
  reactions, with ≤N=50 reactions needed to see the lift.
- **Gate.** Build/skip a per-user ranker → feeds C4's calibration store.

### A3 · Proactive "what changed" signals  `[P1 · S–M · 🧪]`
- **Q.** Which *delta* signals beyond the daily snapshot are valued — "changed since
  yesterday", "overdue N days", "thread went quiet", "unusual volume from X"?
- **Why.** Carryover/pending are state, not change; users likely want deltas.
- **Protocol.** Tag dogfood-diary items as state vs. delta; build a synthetic
  "delta digest" over consecutive replayed days and self-rate usefulness.
- **Inputs.** Consecutive replay snapshots; carryover/pending.
- **Metric.** ≥1 delta signal rated "would change my morning" on ≥60% of days.
- **Gate.** Pick the delta signals worth a section/notification.

### A4 · Cross-source entity & topic resolution  `[P2 · M · 🟢→🏢]`  ↔C7
- **Q.** Is unifying identity (one "Ivan" across EWS+MM) and topic (one thread across
  sources) worth the modeling cost?
- **Why.** Fragmented identity/topics dilute the digest and double-count.
- **Protocol.** Measure duplication/fragmentation on a real replayed corpus; prototype
  an offline identity-merge (email↔MM handle) and a cross-source topic cluster.
- **Inputs.** Replay corpus; the store; existing thread/embedding-merge code.
- **Metric.** ≥15% of items are cross-source duplicates/fragments today (justifies
  build); merge precision ≥0.9 on a hand-labeled sample.
- **Gate.** Build/skip the unified identity+topic model → drives C7 schema.

### A5 · Retrospective / analytics  `[P2 · S · 🧪]`
- **Q.** Is there demand for weekly rollups, trends, or "digest of digests"? `history`
  exists but isn't a product surface.
- **Why.** Most value is "today"; verify retrospective demand before building.
- **Protocol.** Count retrospective questions in the dogfood diary; instrument `history`
  invocation frequency.
- **Inputs.** Dogfood diary; `history` usage logs.
- **Metric.** ≥2 retrospective queries/week sustained → promote; else leave as a CLI utility.
- **Gate.** Promote `history` to a product surface, or keep it a utility.

### A6 · Conversational / saved / scheduled `ask`  `[P1 · M · 🧪→🏢]`
- **Q.** Is "ask your inbox" a keystone feature, and should it be multi-turn / saved /
  scheduled ("every Monday, list my open commitments")?
- **Why.** One-shot `ask` may undersell it; the real shape may be a standing query.
- **Protocol.** Instrument `ask` frequency + repeat-query patterns; prototype saved +
  scheduled questions offline (cron → `ask` → MM delivery).
- **Inputs.** `ask` usage; the scheduler/systemd path; `InboxAPI.ask`.
- **Metric.** ≥30% of `ask` queries are repeats/variants of a prior one (→ saved queries
  warranted); a scheduled-`ask` prototype produces a useful weekly answer.
- **Gate.** Invest in conversational/scheduled `ask`, or keep one-shot.

### A7 · Evidence-tracing → trust → adoption  `[P1 · M · 🏢]`  ↔B8
- **Q.** Does evidence-tracing / weak-evidence signaling *measurably* increase trust and
  engagement?
- **Why.** P2 traceability is our wedge — prove it changes behavior, don't assume.
- **Protocol.** A/B the citation sub-line and the `⚠ weak basis` badge in delivered
  digests; correlate with reaction ack-rate and open-through.
- **Inputs.** api-mode delivery; the reactions ledger; A/B variants.
- **Metric.** Cited/badged digests get ≥10% higher ack-rate (or lower nack-rate) than the
  stripped variant, over ≥2 weeks.
- **Gate.** Keep/expand or simplify the trust UI → ties B8.

### A8 · Competitive positioning / the wedge  `[P1 · S · 🟢]`
- **Q.** What is the defensible wedge vs. Superhuman/Cora/Motion/native MS-Google digests?
- **Why.** Privacy-first + evidence-traced + corp-air-gapped is the likely moat; confirm
  it's differentiated, not just different.
- **Protocol.** Feature/trust/deployment teardown of 4–5 comparables → a one-page
  positioning statement (who it's for, the wedge, what we deliberately don't do).
- **Inputs.** Public product docs; this spec.
- **Metric.** A positioning one-pager with ≥3 concrete differentiators that rivals can't
  easily copy (air-gap, evidence-trace, no-egress default).
- **Gate.** Sharpen positioning → re-weights which A-items matter; feeds A11.
- **Drafted v1.** → [`A8-positioning.md`](A8-positioning.md) (wedge identified; verify competitor specifics before external use).

### A9 · Source breadth — the next ingestion source  `[P1 · M · 🧪🟢]`
- **✅ Decided: EWS Calendar next (2026-06-20, [PROPOSAL](PROPOSAL.md)).** Build the **read-side
  now** (new meetings · in-body questions/actions · agenda summaries · collision detection ·
  rank-by-attendee-seniority); the **write-side** (cancel/reschedule/propose-times/forward) rides
  the parked action loop (C). Slack/Teams stay excluded (cloud-only → break no-egress).
- **Q.** After EWS + Mattermost, which source adds the most digest value — calendar /
  meetings, Jira / tickets, Slack / Teams, or shared docs?
- **Why.** Many high-value actions originate outside mail+chat (a decision owed in a
  meeting; a ticket assigned to you). The `build_adapter` seam already generalizes ingestion.
- **Protocol.** Dogfood diary tags each digest-worthy action by its *true* origin source;
  competitive scan of rivals' source coverage; a thin read-only adapter spike for the top
  candidate behind `SourceAdapter`.
- **Inputs.** Dogfood diary; `ingest/source_adapter.py`; the snapshot/replay harness.
- **Metric.** ≥20% of high-value actions originate outside EWS+MM (justifies a 3rd source);
  the spike ingests the top source into a digest end-to-end on replay.
- **Gate.** Pick the next source, or confirm EWS+MM is sufficient.

### A10 · EN-default extraction quality vs. the RU baseline  `[P1 · M · 🏢]`
- **Q.** Is English-by-default extraction at parity with the RU baseline (the contract
  before the 2026-06-12 switch)? (Corp item C1.)
- **Why.** The default flipped to EN but EN quality is unvalidated on real corp mail —
  a silent regression would erode the core product.
- **Protocol.** Run EN and RU prompts over the same replayed corp corpus; score against
  the gold set (P/R/F1, citation validity); blind side-by-side human review of N digests.
- **Inputs.** Corp replay snapshot; the gold set; `eval-judge-run` / `eval-replay`.
- **Metric.** EN Macro-F1 ≥ RU baseline (within noise); ≥95% citation validity holds in EN;
  no section systematically worse.
- **Gate.** Confirm EN default, fix the EN prompt, or revert specific sections to RU.

### A11 · Business / deployment model  `[P1 · S · 🟢]`  ↔A8 ↔C6
- **Q.** Is ActionPulse a personal/internal tool, an open-source project, or a product —
  and for whom?
- **Why.** It's currently personal/internal; the answer gates multi-user (C6), positioning
  (A8), and how much onboarding/UX investment is justified.
- **Protocol.** A strategy memo weighing the three models against the privacy/air-gap
  wedge and maintenance cost; capture the maintainer's intent explicitly.
- **Inputs.** A8 positioning; C6 deployment analysis.
- **Metric.** A chosen model with explicit rationale (a decision, not a number).
- **Gate.** The model decision → re-scopes C6, A8, and the UX investment level.
- **Drafted v1.** → [`A11-business-model.md`](A11-business-model.md) (recommends M1 now → M2/OSS next → M3 deferred).

---

## 4. Track B — UX

### B1 · Mattermost bot / 2nd interaction surface  `[P0 · M · 🟢→🏢]`  ↔C1 ↔C3
- **✅ Architecture decided (2026-06-20, [PROPOSAL](PROPOSAL.md)).** Build via **connect-out**:
  a per-user local `actionpulse bot` daemon opens an **outbound** WebSocket to MM + replies via
  REST — **no inbound server, no slash-command registration**. Identity = the user's PAT (posts as
  self) or an optional dedicated bot account. Scales as **N independent per-user instances**
  (never a shared bot). Runs corp-side (semantic `ask` is gateway-bound); needs **B/C11** first.
- **Q.** What's the right interactive surface beyond the terminal — an MM bot /
  slash-commands, a TUI, a local web UI?
- **Why.** The digest *arrives* in MM (any device) but every follow-up verb is at the
  terminal. An MM bot reaches the user where the digest already lives, needs no new
  client, and runs corp-side where the gateway is reachable.
- **Protocol.** Prototype `/digest ask|search|history` (and, if A1 lands, `snooze|done`)
  over `InboxAPI`; compare task-completion time vs. the terminal for the same tasks.
- **Inputs.** `InboxAPI`; the MM PAT path; `deliver/mattermost.py`.
- **Metric.** Bot task-completion time ≤ terminal for `ask`/`search`; ≥80% of dogfood
  follow-up queries are satisfiable from the bot.
- **Gate.** Pick the second surface → drives C3 transport/auth.

### B2 · Lowest-friction in-digest feedback gesture  `[P1 · S–M · 🏢]`  ↔A1 ↔A7
- **Q.** What's the cheapest in-message gesture that yields signal — an emoji reaction,
  a tap-to-expand-evidence, an inline action?
- **Why.** A reaction is already the flywheel's input; the lowest-friction gesture wins.
- **Protocol.** Prototype reaction-to-train + "tap to expand evidence" in the delivered
  MM message; measure gesture rate.
- **Inputs.** api-mode delivery; the reactions harvest path.
- **Metric.** ≥1 feedback gesture on ≥50% of delivered digests over 2 weeks.
- **Gate.** Define the in-message interaction model.

### B3 · Digest scanability & the section taxonomy  `[P1 · M · 🧪]`
- **Q.** How do users actually scan a digest, and is the `my_actions/urgent/fyi/status`
  taxonomy + ordering right?
- **Why.** Section identity drives comprehension; some sections may be noise.
- **Protocol.** Think-aloud scan study on real digests; test alternate orderings and a
  reduced taxonomy.
- **Inputs.** Real digests; the canonical section keys (`assemble/labels.py`).
- **Metric.** ≥90% recall of "my actions today" within 30s of scanning; an ordering that
  beats the current one on that recall.
- **Gate.** Keep/revise the section model & ordering.

### B4 · First-run success & wizard friction  `[P1 · S · 🧪🏢]`
- **Q.** What's the first-run success rate, and where do operators get stuck?
- **Why.** The wizard audit found real gaps (timezone/auth_mode not prompted, alias
  overwrite) that likely hurt first-run.
- **Protocol.** Instrument time-to-first-digest; usability-test the wizard including the
  documented gaps; the three deferred follow-ups are candidate fixes.
- **Inputs.** The wizard; the corp runbook §0.3/§1; the audit findings.
- **Metric.** Time-to-first-digest < 15 min; first-run success ≥90% without doc lookups.
- **Gate.** Prioritize the deferred wizard fixes.

### B5 · Power-feature discoverability  `[P2 · S · 🧪]`
- **Q.** Do users discover `search`/`ask`/`history`? (Search/ask appear only when the
  store is on.)
- **Why.** The headline gap was that the retrieval pillar was invisible to menu users.
- **Protocol.** Discoverability test of the 12-row menu; track feature reach over the first week.
- **Inputs.** The menu; usage logs.
- **Metric.** ≥70% of users invoke `search` or `ask` within their first week (store on).
- **Gate.** Re-design menu/onboarding surfacing.

### B6 · Reader & explain surface utility  `[P2 · S · 🧪]`
- **Q.** Are the digest reader (drill-down) and `explain` (post-crash) used and useful?
- **Why.** Low-traffic surfaces may be over-built or under-discovered.
- **Protocol.** Instrument usage; a quick usability pass on each.
- **Inputs.** Reader/explain usage logs.
- **Metric.** Each surface used ≥1×/week, or a clear "not needed" verdict.
- **Gate.** Keep / cut / merge these surfaces.

### B7 · Cadence & the empty-digest experience  `[P2 · S · 🧪]`
- **Q.** Is daily cadence right, and what should an empty/near-empty day feel like?
- **Why.** Cadence should be per-user; the "no actions" day needs a deliberate UX.
- **Protocol.** Dogfood cadence variations (daily / twice-daily / on-demand); design the
  quiet-day message.
- **Inputs.** The scheduler; the empty-digest path.
- **Metric.** A default cadence chosen with a satisfaction rationale; an empty-day message
  that reads as reassurance, not failure.
- **Gate.** Set default cadence + per-user override UX.

### B8 · Trust UI  `[P1 · M · 🏢]`  ↔A7
- **Q.** How should citations, evidence spans, weak/repeat badges, and redaction be
  presented — legible without clutter?
- **Why.** Trust signals must be readable at a glance; ties directly to adoption (A7).
- **Protocol.** Co-design variants; A/B with A7; measure whether a user can correctly
  trace one item to its evidence.
- **Inputs.** The delivered message format; A7's A/B harness.
- **Metric.** ≥90% of users correctly identify an item's source/evidence; no drop in scan speed.
- **Gate.** Finalize the trust-UI spec.

---

## 5. Track C — Architecture

### C1 · Facade parity + cross-surface contract test  `[P0 · S–M · 🟢]`
- **Q.** Should *all* retrieval go through `InboxAPI`, with MCP/CLI parity enforced by a test?
- **Why.** `history` lives outside the facade; reactions/flywheel and `explain` are
  CLI-only — drift that compounds as surfaces grow (gap #4 elsewhere, but here it's debt).
- **Protocol.** Spike: move `history` into `InboxAPI`; add a parity contract test asserting
  every API verb ↔ MCP tool ↔ CLI command (allowing explicit, documented exceptions).
- **Inputs.** `api/inbox.py`, `mcp/server.py`, `cli.py`.
- **Metric.** Parity test green in CI; 0 undocumented retrieval verbs outside the facade.
- **Gate.** Adopt the single facade boundary + the test.

### C2 · The write/action architecture  `[P1 · M · 🟢]`  ↔A1
- **Q.** If A1 is greenlit, what architecture supports actions (mutation layer, action
  ledger, idempotent delivery back to MM/EWS)?
- **Why.** Everything is read-only today; actions are a new architectural axis.
- **Protocol.** Design spike driven by A1's action model — thread-safety, idempotency,
  audit trail, failure/rollback.
- **Inputs.** A1's action model; the store; the delivery path.
- **Metric.** An ADR with a worked design for the top-2 actions, incl. idempotency + audit.
- **Gate.** The action-layer ADR, or "stay read-only."

### C3 · MCP transport + auth model  `[P1 · M · 🟢🏢]`  ↔B1
- **🔬 Research correction** ([brief](research/C3-mcp-transport-auth.md)): premise mostly
  *refuted* (good). stdio+env-key is **already the spec's recommended local shape** — no change
  needed. **SSE is deprecated** — never build it; use **Streamable HTTP** only if a genuine
  *remote* MCP client is ever required (behind PC-2). For B1 (the likely 2nd surface), the
  secure pattern is a **corp-side service calling `InboxAPI` directly**, not a remote MCP. The
  real, transport-independent priority is the new **prompt-injection item (§5a)**. This item
  shrinks to "keep stdio; only add Streamable HTTP+OAuth 2.1 if remote MCP is forced."
- **Q.** Does the MCP server need non-stdio transport + auth (today: stdio + env key only)?
- **Why.** A second surface (B1) and any remote/multi-client use needs HTTP/SSE + auth;
  cloud egress (ADR-015) must be revisited at scale.
- **Protocol.** Spike FastMCP HTTP/SSE transport; threat-model the auth + egress posture
  against PC-2.
- **Inputs.** `mcp/server.py`; ADR-015; PC-2.
- **Metric.** A working HTTP/SSE prototype with an auth gate; a PC-2-consistent egress note.
- **Gate.** The transport/auth ADR; a PC-2 amendment if egress changes.

### C4 · Safe fleet-activation state machine  `[P1 · L · 🟢→🏢]`  ↔A2
- **🔬 Research result + flywheel correction** ([brief](research/C4-calibration-activation.md)):
  adopt the **six-state machine** `DARK→SHADOW→CALIBRATE→ARMED→CANARY→LIVE` + a feature-flag
  kill-switch. Certify the recall floor with a **Wilson / Clopper–Pearson lower bound over
  labeled positives** (never the Wald approx — it gives false certainty at recall≈1).
  **Decisive correction:** **reactions are recall-blind** (survivorship — they only land on
  *delivered* items), so the flywheel calibrates *precision/the judge* but **a defensible recall
  floor needs a separate human-audited random sample**. Replace any bare-κ judge gate with
  prevalence-aware per-class precision/recall (κ is deflated when positives are rare). *The
  flywheel code shipped this session is sound; the recall-floor **claim** in STATUS/ROADMAP needs
  the audit-sample caveat.*
- **Q.** What's the safe-activation architecture for the dark fleet (reranker / judge /
  relevance / best-of-N / embedding-merge)?
- **Why.** Shadow→calibrate→flip is the pattern; the per-user calibration store and an
  automated retraining loop from the flywheel are missing.
- **Protocol.** Spec the activation state machine + calibration store; dry-run the flip
  path offline against the flywheel's output; define the rollback trigger.
- **Inputs.** The flywheel; `eval-calibrate`; the fleet flags; PC-2.
- **Metric.** A documented state machine with explicit flip/rollback criteria tied to a
  measured `recall_floor`; a successful offline dry-run flip.
- **Gate.** The activation ADR; the PC-2-gated flip plan.

### C6 · Multi-user / deployment topology  `[P2 · S · 🟢]`  ↔A11
- **Q.** Is multi-user / server deployment ever a goal (vs. single-user SQLCipher +
  systemd)?
- **Why.** Probably not soon — but A8/A11/B1 may imply it; decide deliberately, not by drift.
- **Protocol.** Enumerate target topologies; cost the data-model / isolation / secrets
  changes each implies.
- **Inputs.** A11's model decision; the store; the secrets model.
- **Metric.** An explicit "single-user only" statement or a multi-user ADR with the cost owned.
- **Gate.** The deployment-topology decision.

### C7 · Cross-source store schema  `[P2 · M · 🟢]`  ↔A4
- **Q.** What schema does cross-source entity/topic resolution (A4) require?
- **Why.** Identity is per-source today; A4 needs a unified model in the store.
- **Protocol.** Schema spike + a migration plan over the existing SQLCipher store
  (additive, reversible).
- **Inputs.** A4's findings; the store schema.
- **Metric.** A migration that adds identity/topic tables with a tested up/down path.
- **Gate.** The schema ADR, or skip with A4.

### C8 · Observability / SLOs in practice  `[P2 · S–M · 🏢]`
- **Q.** What should we actually monitor (cost, latency, recall, delivery SLA)? Metrics +
  OTel exist but are unused.
- **Why.** We have the plumbing (Prometheus endpoint, OTel off) but no SLOs/dashboards.
- **Protocol.** Define the SLO set; stand up a minimal dashboard; wire OTel export in one
  real corp run.
- **Inputs.** The metrics endpoint; OTel; run telemetry.
- **Metric.** A dashboard covering ≥4 SLOs (cost/run, stage latency, support-recall,
  delivery success) populated from a real run.
- **Gate.** The observability/SLO spec.

### C9 · Cost / latency envelope at volume  `[P2 · S · 🟢]`
- **Q.** Does the envelope (2 LLM calls/run, 15 RPM, the TD-006 cost cap) hold as sources
  and volume grow?
- **Why.** Adding MM channels/DMs + the fleet multiplies calls; the cost cap must scale.
- **Protocol.** Model cost/latency vs. volume; stress the rate broker offline with
  synthetic load at 2×/5×/10× today's message count.
- **Inputs.** The rate broker; the cost model; replay corpora.
- **Metric.** Projected cost/run stays under the cap at 5× volume, or a documented
  scaling plan if not.
- **Gate.** Revise the cost model / call budget.

### C10 · DPIA / threat model depth  `[P1 · L · 🟢🏢]`  ↔PC-2
- **Q.** How deep should the privacy/data-lifecycle architecture go (DPIA, STRIDE/LINDDUN
  threat model, right-to-be-forgotten, at-rest posture)?
- **Why.** PC-2 + 7-day retention + DM redaction are the floor; a deeper DPIA is owed
  before any broader rollout (the discovery finding about PDn-at-rest in `out/` is a flag).
- **Protocol.** Author the DPIA + threat model; verify the RTBF + at-rest flows end-to-end
  against the store and `out/`.
- **Inputs.** PC-2; the retention/redaction code; the discovery findings.
- **Metric.** A completed DPIA with every data flow classified; RTBF verified to remove a
  subject end-to-end.
- **Gate.** The DPIA + any architecture changes it forces.

---

## 5a. Research-added items (new, 2026-06-20)

### C11 · Prompt-injection / tool-output-as-data defense  `[P1 · M · 🟢]`  *(new — from C3 research)*
- **Q.** How does ActionPulse defend against prompt injection, given it feeds **untrusted
  inbound email/chat** to an LLM (extractor, judge, `ask`, and the MCP tool surface)?
- **Why.** The corpus *is* attacker-influenceable — a malicious email can carry instructions
  the model obeys (Gemini's email summaries were demonstrably injectable). This is
  transport-independent and, per the C3 brief, the **highest-leverage hardening** — and it's
  currently unaddressed in the spec.
- **Protocol.** Audit every place ingested content reaches a model; adopt "treat content +
  tool output as **data, not instructions**" (delimiting, instruction-stripping, the model is
  told returns are data); consider pinning the MCP tool-definition set (SHA-256) so a change is
  visible; human-in-the-loop for any future state-changing action (ties C2/A1).
- **Inputs.** `llm/`, `assemble/`, `mcp/server.py`, the ingest path; OWASP MCP + LLM guidance.
- **Metric.** A documented injection threat model + a red-team test set of adversarial
  emails/posts that the pipeline resists (no instruction-following, no fabricated items).
- **Gate.** The injection-defense ADR + the red-team test in CI.

### Design red lines (constraints, not a research item) — from C10
Two EU-AI-Act lines the product **must never cross**, binding on A1 (actions) and A2
(personalization): **(1)** never infer employee **emotion/sentiment** (Art. 5 *prohibited*);
**(2)** never feed outputs into **performance evaluation** (Annex III *high-risk*). Lawful basis
is **legitimate-interest-with-balancing, not employee consent**. These are gates on design
scope, recorded here so A1/A2/C4 stay inside *limited-risk*.

---

## 6. Prioritization & sequencing

**First wave (one cycle) — the P0 set, mutually reinforcing:**

- **A1** (action loop, diary) — caps the value of everything; cheap to start (a diary).
- **B1** (MM bot prototype) — reaches the user where the digest lives; runs corp-side.
- **C1** (facade parity + contract test) — offline, stops drift, makes B1 clean.

Add two cheap, high-signal companions that need no corp access: **A8** (positioning,
frames the A-track) and **A11** (business model, frames C6 and the UX investment level).

**Second wave** (after the action model + positioning are settled): A2/A6/A7 (the
flywheel-measured items, batched into a corp session with A10), B3/B4/B8, C2/C3.

**Deferred until demand or a model decision:** A4/A5, B5/B6/B7, C4 (PC-2-gated),
C6/C7/C8/C9, C10 (large, but start the DPIA early if rollout nears).

## 7. Decision gates & deliverables

Per cycle, each track produces evidence-backed go/skip calls — **a research item is
"done" when its decision gate is answered with data, not when a doc is written:**

- **Product:** the action-model proposal (A1), an instrumented dogfood report
  (A3/A5/A6/A9), the EN-quality verdict (A10), a positioning + business-model pair (A8/A11).
- **UX:** a working MM-bot prototype + task-completion comparison (B1), the scan-study
  verdict on the section model (B3), a first-run report (B4), the trust-UI spec (B8).
- **Architecture:** the parity contract test merged to CI (C1); ADR drafts for whichever
  of C2/C3/C4/C10 the product decisions activate.

Outputs revise [`ROADMAP.md`](ROADMAP.md); they do not themselves authorize building.

## 8. Non-goals (this cycle)

- **Local embeddings — standing decision: NO** (confirmed 2026-06-20). Gateway-only
  embeddings are accepted; semantic retrieval (`search` semantic/hybrid, `ask`,
  `related`, `summarize_thread`, `compare`) is **corp-bound by design — not a gap.**
  The second surface (B1) runs corp-side where the gateway is reachable, so this is not
  a blocker. This is durable, not just this-cycle; the only thing that would reopen it
  is the deployment model (A11) changing to an off-corp/standalone product.
- Not committing to multi-tenant SaaS — that is the *question* (C6/A11), not an assumption.
- Not flipping the dark fleet — PC-2 + calibration gate it regardless; C4 only *specs* the path.
- Not building any action/write feature before A1's gate.
- Not adding a new egress path before C3/PC-2 clears it.

## 9. Dependencies & ties to existing artifacts

- **PC-2** ([`PC2_DATA_HANDLING.md`](../../digest-core/docs/PC2_DATA_HANDLING.md)) gates
  the dark fleet (C4), new egress (C3), and broader rollout (C10).
- The **reactions flywheel** (code-complete, [`STATUS.md`](STATUS.md) stream 4) is the
  measurement substrate for A2/A7/A10 and the input to C4.
- The **wizard/logging audit** (deferred follow-ups: timezone/auth_mode prompts, alias
  preservation) feeds B4.
- The **`build_adapter` source seam** ([`ARCHITECTURE.md`](../../digest-core/docs/ARCHITECTURE.md))
  is the entry point for A9.
- The **corp session** ([`CORP_SESSION_RUNBOOK.md`](../../digest-core/docs/CORP_SESSION_RUNBOOK.md))
  is the only venue for 🏢 items — batch them.
