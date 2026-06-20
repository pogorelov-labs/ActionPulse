# ActionPulse Enhancement Proposal — post-research decisions

> **What this is:** the concrete enhancement decisions coming out of the deep research
> ([`RESEARCH_SPEC.md`](RESEARCH_SPEC.md) + [`research/SYNTHESIS.md`](research/SYNTHESIS.md)).
> Status per item, condensed pros/cons, the decided **Mattermost-bot architecture**, the
> **EWS-calendar feature breakdown**, and the agreed build sequence. **Decisions captured
> 2026-06-20** (maintainer review of the proposal). Companions:
> [`RESEARCH_SPEC.md`](RESEARCH_SPEC.md) (what to research), [`ROADMAP.md`](ROADMAP.md) (the
> broader plan).

## Status at a glance

| # | Addition | Decision | Tier |
|---|----------|----------|------|
| **A** | Facade parity + contract test (C1) | ✅ **build** | 0 |
| **B** | Prompt-injection defense (C11) | ✅ **build** | 0 |
| **C** | Action loop — Done/Snooze/Send-to-task (A1 → C2) | ⏸ **parked** | 1 |
| **D** | Mattermost bot / 2nd surface (B1) | ✅ **build** — connect-out, per-user (architecture decided ↓) | 1 |
| **E** | EWS Calendar source (A9) | ✅ **build** (read-side now; write-side rides C) | 2 |
| **F** | Standing / scheduled queries (A6) | ✅ **build** | 2 |
| **G** | Audit-sample + fleet-activation machine (C4) | 🏢 corp-phase (later) | 3 |

## Guardrails (bind every item)

- **No-egress wedge** — nothing routes corp content to the cloud by default. Constrains the
  roadmap (excludes cloud task targets, Slack/Teams sources; the bot runs corp-side).
- **EU-AI-Act red lines** (C10) — never infer employee emotion/sentiment (*prohibited*); never
  feed outputs into performance evaluation (*high-risk*).

---

## Tier 0 — foundational (offline, build now)

### A · Facade parity + contract test (C1) — ✅ build
**What:** move `history` into `InboxAPI`; CI test asserting every retrieval verb is reachable
consistently across **API ↔ MCP ↔ CLI** (documented exceptions allowed).
**Pros:** cheap, offline, durable CI guardrail; makes all surfaces consistent; **unblocks D**.
**Cons:** low immediate user-visible value (hygiene); small refactor + test churn; "parity" must
encode intent, not mechanical equality.

### B · Prompt-injection / tool-output-as-data defense (C11) — ✅ build
**What:** treat ingested email/chat **and** tool output as *data, not instructions* everywhere it
reaches an LLM (extractor, judge, `ask`, MCP, the future bot): delimiting, instruction-stripping,
"returns are data" framing, pinned MCP tool defs, a red-team test set in CI.
**Pros:** closes a real open hole (the corpus is attacker-influenceable); cheap vs. impact; a
**precondition** for C (a write triggered by injection is far worse) and D (a new input surface);
reinforces the evidence/trust wedge.
**Cons:** mitigation not elimination (risk of false confidence); red-team set needs upkeep;
over-aggressive stripping could nick extraction quality (guard with an eval).

---

## Tier 1 — the product unlock

### C · Action loop (A1 → C2) — ⏸ PARKED (2026-06-20)
**Decision:** deferred for now. Not dropped — **re-open cheaply** when wanted.
**When un-parked, build the local-only subset first:** `Done` (acknowledge → suppress
re-extraction; ≈ the dedup ledger) and `Snooze → next digest` (≈ carryover) — both **local
state only**, keyed by the stable content-hash `evidence_id`, no posture change. `Send-to-task` /
quick-reply is **outbound** and crosses the read-only posture → separate, consent-gated,
**on-prem-only** decision (a cloud task target would break the wedge).
**Note:** C is the **prerequisite for the *outbound* half of the calendar wishlist** (E) —
cancel/reschedule/propose-times/forward are the action loop applied to calendar. So parking C
also defers those.

### D · Mattermost bot / 2nd interaction surface (B1) — ✅ build · **architecture decided**

**Goal:** bring query (`ask`/`search`/`history`) — and later C's actions — to where the digest
already lands (Mattermost), on any device, with no new client.

**Decided model: connect-out, per-user. No inbound server. No slash-command registration.**

Two integration styles exist, with opposite needs:

| Style | How | Needs |
|---|---|---|
| **Push** (slash commands, outgoing webhooks) | MM POSTs to *your* URL on `/digest …` | a **reachable inbound endpoint** + request-URL registration — ❌ a laptop behind NAT isn't that |
| **Connect-out** (bot/PAT + WebSocket + REST) | *your* process opens an **outbound** WebSocket to MM (`/api/v4/websocket`), listens, replies via REST | just an outbound connection + a token — ✅ no inbound server; same shape as how `actionpulse` already connects out to EWS/gateway/MM-webhook |

→ **Use connect-out.** The bot is a **long-running local process** (`actionpulse bot`) on the
user's machine that connects *out* to MM, watches for the user's messages, and answers via the API.

**Identity (registration) — two options, both need only the PAT we already collect:**
- **Connect as yourself (your PAT)** — simplest, no admin. The answer posts *as you* in your own
  DM: literally "you answering yourself." Matches the use case directly.
- **Dedicated bot account `@actionpulse`** — cleaner UX (answers come from the bot), but a
  sysadmin must create it. Optional polish. *(Corp-admin permission to confirm at build time.)*

**Scalability — trivial, *because* it's decentralized.** Each user runs their **own** instance,
connects as **themselves**, answers **only their own** queries in their own DM. N users = N
independent processes — **zero shared state, zero central bottleneck, zero shared secret.** Same
per-user model as the digest.
- 🚫 **Anti-pattern (do not):** a *single shared bot account* connected from many machines — the
  only version that doesn't scale (every instance receives every event → duplicate handling; one
  bot token handed to everyone → secret + privacy bleed, the bot sees everyone's DMs).

**Pros:** unlocks the retrieval pillar on any device (the biggest UX gap); reuses `InboxAPI`
directly (no new transport/auth complexity per the [C3 research](research/C3-mcp-transport-auth.md);
data stays in-perimeter); natural home for F (scheduled queries) + reaction-feedback (the flywheel
input); decentralized → scales for free.
**Cons:** a **persistent daemon** (new vs. today's run-on-demand/cron) → a long-running process +
WebSocket reconnect/backoff, and it only answers while the machine + process are up (fine for a
personal assistant); semantic `ask`/`search` stay **gateway-bound** so the bot runs corp-side / on
VPN (keyword search + `history` work without it); a wider injection surface → **needs B (C11)
first**; interactive use can raise LLM **cost** beyond the 2-call/run digest (needs governance, C9).
**Synergy:** the **self-DM we just excluded from ingestion** is the natural private place to talk
to the bot — your Q&A with it won't pollute the digest.

---

## Tier 2 — depth + reach

### E · EWS Calendar source (A9) — ✅ build (read-side now)
**What:** a read-only EWS Calendar adapter (rides the existing EWS auth/throttle, true on-prem,
near-zero integration cost) via the `build_adapter` seam.

The maintainer's calendar vision splits cleanly — **read/extract ships now; write/outbound rides
the parked action loop (C):**

| Half | Features | When |
|---|---|---|
| **Read / extract** (no posture change) | new meetings; **questions & actions inside meeting bodies**; agenda summarization; **meeting-collision detection**; **ranking meetings by attendee seniority/title** (a calendar-specific relevance signal) | **E now** |
| **Write / outbound** (= the action loop on calendar) | cancel; reschedule; **propose times**; forward | **with C (parked)** |

**Pros:** cheap (reuses EWS plumbing); on-prem (preserves the wedge); genuinely new signal
(meeting commitments email+chat lack); the seniority-ranking idea is a strong relevance lever.
**Cons:** calendar items are a different shape (events, not messages) → extraction/normalization +
a digest section; the write-side is gated on C; adds volume → cost. The seniority-ranking needs an
org-directory/title source (an open input question).

### F · Standing / scheduled queries (A6) — ✅ build
**What:** saved + scheduled `ask` ("every Monday: what am I waiting on / overdue / promised") =
cron + existing delivery + cited retrieval. Pairs with the bot (D) as its home.
**Pros:** nearly free architecturally; leans on the wedge (cited, in-perimeter) where the generic
digest is being commoditized (Copilot/Gemini); commitment-tracking is the high-value pattern.
**Cons:** semantic queries are gateway-bound (corp); marginal without the bot as a home; each
scheduled query = LLM cost.

---

## Tier 3 — corp-gated trust activation (later)

### G · Audit-sample + fleet-activation state machine (C4) — 🏢 corp-phase
**What:** a periodic **human-labeled random sample** to measure *true* recall (reactions are
recall-blind), + the six-state machine `DARK→SHADOW→CALIBRATE→ARMED→CANARY→LIVE` with a Wilson/CP
recall-floor gate and a kill-switch — the safe path to flip the dark fleet (reranker/judge/relevance).
**Pros:** makes activating the biggest built-but-dark value *defensible*; safe, reversible; the
audit sample doubles as drift monitoring. **Cons:** real labeling cost (maintainer time at solo
scale); largely corp-gated (only the scaffolding is offline-buildable); largest item; PC-2-gated.

---

## Build sequence

```
Tier 0 (now, offline)   A  facade parity ─┐
                        B  injection def  ─┴─► de-risk + unblock everything above
Tier 1 (the unlock)     C  action loop ……… ⏸ PARKED (local-only subset + calendar-write prerequisite)
                        D  Mattermost bot  → InboxAPI direct, connect-out, per-user daemon
Tier 2 (depth/reach)    E  EWS Calendar (read-side) · F  standing/scheduled queries
Tier 3 (corp-gated)     G  audit-sample + activation machine → flip the dark fleet (PC-2)
```

**Now:** build **A (C1)** + **B (C11)** — both offline, ship-as-code, and B is a precondition for D.

## Deferred / explicitly not doing

- **C action loop** — parked (above); local-only subset cheap to revive; outbound part is a
  posture decision.
- **C6 multi-tenant / server mode** — out (M1→M2 model decision); the bot's per-user model needs
  no server anyway.
- **Local embeddings** — out (standing decision; gateway-only accepted).
- **Cloud sources (Slack/Teams) & cloud task targets** — out (break the no-egress wedge).
- **Sentiment inference / performance-eval use** — out (EU-AI-Act red lines).
