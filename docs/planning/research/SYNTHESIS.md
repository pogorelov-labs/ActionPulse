# Deep-Research Synthesis — ActionPulse enhancement agenda

> **What this is:** the cross-cutting synthesis of the 8 deep-research briefs in this
> directory (launched from [`../RESEARCH_SPEC.md`](../RESEARCH_SPEC.md), 2026-06-20). Each
> brief web-verified its claims against ≥2 recent sources with confidence tags. This doc
> distills the convergent themes, the **corrections to apply to the spec/memos**, and what
> is newly on the table. Read the individual briefs for evidence + sources.

## 1. Bottom line

The research **confirms ActionPulse's core thesis and sharpens it** — with one finding
that corrects an architectural assumption (MCP transport) and one that corrects the
**flywheel's own framing** (reactions can't measure recall). Nothing reverses the
direction; several things make it more precise.

- **The wedge is real and, if anything, widening** (A8): the market splits into *cloud
  personal-productivity* tools (all egress your mail to an LLM vendor) and *on-prem
  customer-support* platforms (not personal digests). The "privacy-first, evidence-traced,
  personal action-digest on on-prem Exchange + Mattermost" intersection is **unserved**.
- **But the generic "AI digest" is being commoditized** (A6): Microsoft Copilot *Scheduled
  Prompts* and Google *Gemini Daily Brief* now ship scheduled inbox digests. So the
  defensible ground is precisely **no-egress + evidence-tracing**, not "we do digests."
- **Lead with ①no-egress + ②evidence-traced** — the two wedges no incumbent can match
  without changing its business model. Demote ④privacy-by-construction (table-stakes
  *slogan*; differentiates only as *architecture*) and ⑤open/self-hostable (commodity
  alone; the edge is the *combination*).
- **The flywheel needs an audit sample** (C4): reactions only land on *delivered* items, so
  they measure precision but are **structurally blind to recall** — the very thing the
  citation gate protects. A defensible recall floor must come from a **separate human-audited
  random sample**, certified with a **Wilson/Clopper–Pearson lower bound** (never the naive
  Wald approx). This refines — does not invalidate — the flywheel shipped this session.
- **MCP needs no transport change** (C3): stdio + env-key is *already* the spec's
  recommended shape; the 2nd surface (B1) should be a corp-side service calling `InboxAPI`
  **directly**, not a remote MCP. The real hardening is **prompt-injection defense**.
- **EWS Calendar is the next source** (A9); Slack/Teams are ruled out (cloud-only → would
  break the no-egress wedge). **The wedge constrains the roadmap, not just the pitch.**
- **Two EU-AI-Act red lines** (C10): never infer employee emotion/sentiment (Art. 5
  *prohibited*) and never feed outputs into performance evaluation (Annex III *high-risk*).
  A DPIA is legally required before rollout; no-egress already satisfies GDPR Ch. V + Russia
  152-FZ.

## 2. Per-item verdicts

| Item | Verdict | Headline finding | Effect on spec |
|---|---|---|---|
| **A8** competitive | ✅ confirmed, sharpened | Unserved intersection; cloud rivals egress to a vendor; Copilot *Flex Routing* (2026, default-on) lets EU inferencing leave the boundary | Reframe ④⑤; lead ①②; pick Copilot as the contrast foil |
| **A11** market/model | ✅ M1→M2→M3 holds | Sovereign-AI demand is real but is *enterprise-platform* procurement; OSS *relicense-to-monetize* always forks | M2 = inspectable/low-burden + **decide monetization before publishing**; M3 strongly deferred |
| **A1** action loop | ✅ actionable answer | Minimal set = **Done · Snooze→next-digest · Send-to-task/quick-reply**; maps onto carryover + dedup + stable `evidence_id` | Operationalizes A1; the only fork is the read-only-posture decision (C2) |
| **A6** ask | ✅ + ⚠️ competitive | Invest in **saved/scheduled standing queries** (commitment-tracking); incumbents are commoditizing the generic digest | Confirms A6; reinforces the A8 wedge |
| **A9** sources | ✅ decisive | **EWS Calendar next**; Jira DC 2nd (sunsetting); **Slack/Teams excluded (cloud-only)** | Answers A9; the no-egress wedge constrains source choice |
| **C3** MCP | 🔄 corrects premise | stdio+env-key already spec-correct; **don't add SSE**; bot→`InboxAPI` direct; **prompt-injection is the real risk** | Rewrite C3; add a prompt-injection security item |
| **C4** calibration | 🔄 corrects the flywheel | **Reactions are recall-blind** → need an audit sample; Wilson/CP recall floor; κ prevalence paradox; six-state machine | Rewrite C4; add the audit-sample caveat to STATUS/ROADMAP flywheel claims |
| **C10** DPIA | ✅ + constraints | DPIA required; **EU-AI-Act red lines** (no emotion-inference, no perf-eval); legitimate-interest not consent; no-egress = compliance asset | Add red lines as design constraints on A1/A2; DPIA outline for C10 |

## 3. Cross-cutting themes

1. **The wedge is load-bearing across the whole roadmap, not just marketing.** A8 says
   no-egress is the moat; A9 shows it *excludes* Slack/Teams as sources; C10 shows it
   *satisfies* GDPR Ch. V + 152-FZ; A6 shows it's what survives the incumbents' digest
   commoditization. The same property keeps recurring as the decisive constraint — which is
   the strongest possible signal it's the real strategy.
2. **"Evidence-traced" is the second pillar and is validated by rivals' failure modes.** A8,
   A6, and C4 independently land on it: competitors ship *generative* summaries with
   hallucination disclaimers (Gemini summaries are even prompt-injectable); the dominant
   complaint is trust failure (silent omission, fabrication). ActionPulse's P1/P2
   extract-over-generate is the answer — *if* it's enforced by default (a separate shipped-vs-
   claimed question) and *if* the recall floor is honestly measured (C4).
3. **Honest measurement is harder than it looked.** C4 is the most consequential brief: the
   reactions flywheel — the strategic core — can calibrate *precision/the judge* but is
   **blind to recall**. A trustworthy "we don't silently drop your urgent item" promise needs
   a periodic human-audited random sample, a Wilson/CP lower bound, and prevalence-aware
   judge agreement (κ alone misleads when positives are rare). The flywheel code is sound;
   the *claim* attached to it must be corrected.
4. **The cheapest next features ride existing primitives.** A1's action set maps onto
   carryover (snooze), the dedup ledger (done), and the stable content-hash `evidence_id`
   (identity); A6's scheduled queries ride cron + existing delivery + cited retrieval; A9's
   next source (EWS Calendar) rides the exact EWS auth/throttle already used for mail. The
   high-value moves are small *because* the architecture already supports them.
5. **The solo-maintainer reality bounds ambition.** A11 + C10 agree: M3/commercial means
   multi-tenant + SLAs + DPIA-at-depth — a team undertaking. The defensible path is M1 now,
   M2 as *inspectable low-burden release* (validation, not revenue), M3 only on demonstrated
   pull and only as a separate edition (never a retroactive relicense).

## 4. Corrections to apply (to spec + memos)

**Material (change a conclusion):**
- **C3 — rewrite.** Drop "needs HTTP/SSE transport." Facts: stdio+env-key is the spec's
  *recommended* local shape (no change needed); SSE is *deprecated* (use Streamable HTTP if
  ever remote); the B1 surface should be a **corp-side service calling `InboxAPI` directly**,
  not a remote MCP. Fix the stale "HTTP/SSE" wording.
- **C4 — rewrite + correct the flywheel narrative.** Adopt the **six-state machine**
  (DARK→SHADOW→CALIBRATE→ARMED→CANARY→LIVE + flag kill-switch); recall floor via **Wilson/CP
  lower bound over labeled positives** (not Wald); **reactions are recall-blind → add the
  human-audited-sample requirement**; replace any bare-κ judge gate with prevalence-aware
  per-class precision/recall. Add the **"reactions ≠ recall" caveat to STATUS/ROADMAP** where
  the flywheel is described as yielding a `recall_floor`.
- **A8 — reframe ④⑤.** ④ privacy-by-construction differentiates *only as architecture*
  (the "we don't train on your data" claim is table-stakes); ⑤ open/self-hostable is
  commodity alone — the edge is the *combination*. Lead with ①+②. Add the honesty guardrail:
  don't claim "only tool that doesn't train" or "only on-prem email AI" — claim the
  *unserved intersection*.
- **➕ New spec item — prompt-injection / tool-output-as-data defense** (C-track security).
  ActionPulse feeds *untrusted inbound email/chat* to an LLM → classic injection vector
  (Gemini's summaries were demonstrably injectable). Treat ingested content + tool output as
  **data, not instructions**; this is transport-independent and high-leverage.
- **➕ New design constraints (from C10) on A1/A2** — never infer employee emotion/sentiment
  (EU AI Act Art. 5 *prohibited*); never feed outputs into performance evaluation (Annex III
  *high-risk*). These bound how far personalization (A2) and actions (A1) may go.

**Refinements (sharpen, don't reverse):**
- **A11** — M2 = *source-available/inspectable + low-burden* (no-SLA stance, security policy,
  narrow scope); **decide the monetization stance before publishing**; M3 only as a separate
  proprietary edition, never a retroactive relicense; set M2's expectation as *validation, not
  revenue*.
- **A1** — adopt the minimal set (Done/Snooze/Send-to-task); note that Done≈dedup-suppression
  and Snooze≈carryover already exist; the open question is whether a batch tool needs snooze
  at all vs. "leave open."
- **A6** — keep one-shot core, add light multi-turn, invest in saved/scheduled standing
  queries; prioritize commitment/obligation patterns over generic summaries.
- **A9** — record the answer: **EWS Calendar next**; Jira DC second but sunsetting; Slack/Teams
  excluded by the no-egress wedge.
- **C10** — DPIA is *legally required* (≥4 WP248 criteria) before broader rollout; lawful basis
  = legitimate-interest-with-balancing (not employee consent); no-egress already satisfies GDPR
  Ch. V + 152-FZ — **do not regress it**.

## 5. What's newly on the table

- **A prompt-injection security workstream** — not previously in the spec; arguably the
  highest-leverage hardening because ActionPulse's corpus *is* attacker-influenceable.
- **An audit-sample mechanism** — a periodic human-labeled random sample is now a *prerequisite*
  for a defensible recall floor and thus for the whole fleet-activation story (C4 ↔ the flywheel).
- **EWS Calendar as the concrete next source** — turns A9 from a question into a build candidate.
- **Standing/scheduled queries** (A6) — a near-free feature that leans on the wedge and
  side-steps the commoditized generic digest.

## 6. Confidence & honesty notes (carried from the briefs)

- **Unverifiable stats excluded:** the "71% of AI infra off public cloud" figure (A11) and a
  Slack rate-limit cutover date (A9) did **not** survive cross-checking and were dropped.
- **"No competitor does Mattermost+EWS"** (A8) is *no-evidence-found*, not proven — a niche
  vendor behind a sales wall could exist; don't over-claim externally.
- **MCP spec is churning** (C3) — a 2026 RC + a stateless-session redesign are coming; the
  transport *choice* is safe, but defer any auth *implementation* detail to build time.
- **Recall-floor certification needs enough positives** (C4) — with single-digit positives per
  cycle, *no* method certifies a tight floor; whether the flywheel+audit yields enough is an
  open risk.
- **C10 is research, not legal advice** — the DPIA/AI-Act/lawful-basis reading needs counsel
  sign-off before any rollout decision.
