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

These are **structural** (architecture + principles + business model), not features a
competitor toggles on:

1. **Runs inside the perimeter, no egress by default.** EWS *and* the LLM gateway are
   corp-only; nothing leaves the network. A cloud SaaS structurally *cannot* match this —
   their model is to hold your data. This is the moat.
2. **Evidence-traced, extract-over-generate.** Every item cites a verbatim evidence span
   + source ref (principle P2); weak evidence is badged; a citation gate guards recall.
   Generative summarizers can't retrofit verifiable provenance — it's a design principle
   (P1/P2), not a setting.
3. **Native to the actual corp stack.** On-prem **Exchange (EWS)** + **self-hosted
   Mattermost** (channels + consent-gated DMs) — the systems cloud tools don't touch.
4. **Privacy by construction, at depth.** DM redaction *at rest*, a consent ladder,
   ≤7-day retention, secrets-ENV-only, and a per-endpoint data-handling discipline
   (PC-2). A compliance-grade posture, not a privacy-policy promise.
5. **Open, inspectable, self-hostable.** You own the code, the prompts, and the data;
   no lock-in. (Assumes the [A11](A11-business-model.md) M1→M2 path.)

## Competitive frame (category-level)

| Category | Examples | What they do well | Why they don't fit the ICP |
|---|---|---|---|
| AI email clients | Superhuman, Shortwave | Fast triage, keyboard-first, AI summaries | Cloud-hosted; your mail goes to their servers + an LLM provider; consumer/SMB |
| AI triage / digest | Cora, native Gmail Priority / Outlook digests, M365 Copilot / Workspace Gemini | Summaries, priority, suite-integrated | Cloud / tenant-cloud; tied to the suite; no on-prem EWS or Mattermost; generative, not evidence-traced |
| AI scheduling / tasks | Motion, Reclaim, Sunsama | Auto-scheduling, task/calendar focus | Different job (calendar/tasks, not an inbox digest); cloud |
| DIY scripts / rules | Outlook rules, homegrown | Free, local | No extraction quality, no eval discipline, no privacy guarantees, no traceability |

**Teardown axes that matter:** deployment (cloud vs on-prem/air-gap) · data egress
(does your mail leave?) · source coverage (on-prem EWS? Mattermost?) · trust model
(evidence-traced vs generative) · openness/ownership. ActionPulse is the only entry
that is on-prem + no-egress + evidence-traced + Mattermost-native + self-hostable.

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
