# A8 — Positioning one-pager

> **Deliverable for** [`RESEARCH_SPEC.md`](RESEARCH_SPEC.md) item **A8** · draft v1 · 2026-06-20 · internal.
> **Companion:** [`A11-business-model.md`](A11-business-model.md) (the model choice this
> positioning assumes). **Verify-before-external-use:** competitor *feature* claims below
> are pitched at the category level from general knowledge — confirm specifics (a quick
> deep-research pass) before any outward-facing use. ActionPulse's own claims are grounded
> in its architecture and are authoritative.

## One line

> **ActionPulse is the daily action digest for people whose inbox can't go to the
> cloud — evidence-traced extraction that runs entirely inside the corporate perimeter.**

## Who it's for (ICP)

A knowledge worker inside a **privacy-sensitive / regulated / air-gapped enterprise**
(bank, government, healthcare, defense) who is drowning in corporate email + chat and
**cannot use cloud AI email tools** because corp data may not leave the network. The
founding context is exactly this: on-prem Exchange (EWS) + self-hosted Mattermost + an
LLM behind a corp gateway, where every cloud incumbent is a non-starter by policy.

Secondary: any individual or small team that wants a **self-hosted, inspectable**
digest they fully own — no vendor in the data path.

## The wedge — differentiators rivals can't easily copy

> **Research-verified v2** (see [`research/A8-competitive-landscape.md`](research/A8-competitive-landscape.md)
> + [`research/SYNTHESIS.md`](research/SYNTHESIS.md)). **Lead with ① + ② together** — the
> pair no incumbent can match without changing its business model. ④ and ⑤ are *reinforcing,
> not standalone* (reframed below). The generic "AI digest" is being commoditized (Copilot
> *Scheduled Prompts*, Gemini *Daily Brief*) — so the wedge is no-egress + evidence-tracing,
> **not** "we do digests."

1. **Runs inside the perimeter, no egress by default** *(lead).* EWS *and* the LLM gateway
   are corp-only; nothing leaves the network. A cloud SaaS structurally *cannot* match this.
   Research reinforces it: even the strongest enterprise option — **M365 Copilot** — keeps
   mail in the *vendor's* cloud, can call LLMs across regions, excludes Anthropic models from
   the EU Data Boundary, and its 2026 **Flex Routing** (default-on) lets EU inferencing leave
   the boundary under load. "Stays in your network" is genuinely distinct.
2. **Evidence-traced, extract-over-generate** *(lead).* Every item cites a verbatim evidence
   span + source ref (P2); weak evidence is badged. *Every* competitor ships a **generative
   summary** with hallucination disclaimers (Gemini's are even prompt-injectable); the
   dominant complaint across tools is trust failure (silent omission, fabrication) — the exact
   ground P1/P2 owns. Frame as **per-item verbatim evidence + extract-not-generate** (Copilot
   Chat already shows grounding citations, so "citations" alone isn't unique).
3. **Native to the actual corp stack.** On-prem **Exchange (EWS)** + **self-hosted
   Mattermost** — systems cloud tools don't touch. A niche moat (of incumbent *disinterest*,
   durable while the niche stays small), not a technical impossibility.
4. **Privacy by construction — but only as *architecture*** *(reinforcing).* "We don't train
   on your data" is now table-stakes (every vendor claims it). This differentiates **only**
   when expressed as the constructive facts — no egress, DM-redaction-at-rest, evidence-only
   output — never as a policy promise. *Bonus, per [C10 research](research/C10-dpia-regulatory.md):
   the no-egress posture is also a compliance asset (satisfies GDPR Ch. V + Russia 152-FZ).*
5. **Open / self-hostable — but only in *combination*** *(reinforcing).* OSS self-host is
   matched by hobby tools (Gmail-only) and, for support, by Rasa. The edge is *self-hostable
   AND enterprise-grade AND evidence-traced AND Exchange-native* — the combination, not
   openness alone. (Assumes the [A11](A11-business-model.md) M1→M2 path.)

## Competitive frame (category-level)

| Category | Examples | What they do well | Why they don't fit the ICP |
|---|---|---|---|
| AI email clients | Superhuman, Shortwave | Fast triage, keyboard-first, AI summaries | Cloud-hosted; your mail goes to their servers + an LLM provider; consumer/SMB |
| AI triage / digest | Cora, native Gmail Priority / Outlook digests, M365 Copilot / Workspace Gemini | Summaries, priority, suite-integrated | Cloud / tenant-cloud; tied to the suite; no on-prem EWS or Mattermost; generative, not evidence-traced |
| AI scheduling / tasks | Motion, Reclaim, Sunsama | Auto-scheduling, task/calendar focus | Different job (calendar/tasks, not an inbox digest); cloud |
| DIY scripts / rules | Outlook rules, homegrown | Free, local | No extraction quality, no eval discipline, no privacy guarantees, no traceability |

**Teardown axes that matter:** deployment (cloud vs on-prem/air-gap) · data egress
(does your mail leave?) · source coverage (on-prem EWS? Mattermost?) · trust model
(evidence-traced vs generative) · openness/ownership.

**The market structure (research finding):** it bifurcates cleanly into **cloud
personal-productivity** tools (Superhuman, Shortwave, Cora, Gemini, Copilot — *all* transit
your mail to an LLM vendor) and **on-prem *customer-support* automation** platforms (Cognigy,
Kore.ai, IBM watsonx, eGain — none do a personal action-digest). The only self-hostable
*personal* tools are Gmail-only hobby projects. So ActionPulse's intersection —
**privacy-first, evidence-traced, personal action-digest on on-prem Exchange + Mattermost** —
is **unserved**. *Claim the unserved intersection*, not a bare superlative.

> **Honesty guardrails for the pitch** (from the research): do **not** claim "the only tool
> that doesn't train on your data" (false — everyone claims it) or "the only on-prem email AI"
> (false — support platforms exist). The defensible claim is the *combination no one ships*.
> The Mattermost+EWS-uniqueness claim is "no evidence found," not proven — don't over-state it.

## What we deliberately do NOT do (anti-positioning)

- **Not a full email client** — it's a digest, not a replacement for Outlook (no
  compose-first experience).
- **Not a calendar/task auto-scheduler** — that's Motion's job, not ours.
- **Not cloud / multi-tenant SaaS** — see [A11](A11-business-model.md); currently NO.
- **Not real-time** — a daily/scheduled digest, not a live inbox-zero tool.
- **Not a chatbot novelty** — `ask` is grounded and cited, never open-ended generation.

## Risks to the positioning

- **Tenant-cloud incumbents** (M365 Copilot, Workspace Gemini) can run "in your tenant"
  and may be *good enough* for orgs whose policy permits cloud-tenant AI — but they
  remain cloud, suite-locked, non-evidence-traced, and Mattermost-blind.
- **Market size.** Truly air-gapped enterprises are fewer — but high-value and
  underserved; the wedge is depth, not breadth. (Sizing is an [A11](A11-business-model.md)
  question.)
- **Self-host friction.** The differentiator (you run it) is also the cost (first-run
  must succeed) — raises the priority of [B4](RESEARCH_SPEC.md) if the OSS path is taken.

## Decision-gate status (per A8)

✅ **≥3 structural differentiators identified** that rivals can't easily copy:
no-egress deployment (1), evidence-tracing (2), and on-prem-stack nativeness (3),
with privacy-depth (4) and openness (5) reinforcing. The positioning is **defensible**.
Next: a verification pass on competitor specifics before any external use, and align the
ICP framing with the [A11](A11-business-model.md) model choice.
