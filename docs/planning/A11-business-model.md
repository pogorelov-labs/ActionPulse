# A11 — Business / deployment model

> **Deliverable for** [`RESEARCH_SPEC.md`](RESEARCH_SPEC.md) item **A11** · draft v1 ·
> 2026-06-20 · internal decision memo. **Companion:** [`A8-positioning.md`](A8-positioning.md).
> **Gates:** C6 (multi-user), A8 (positioning), and the overall UX investment level.

## The question

Is ActionPulse a **personal/internal tool**, an **open-source project**, or a
**commercial product** — and for whom? The answer is upstream of a lot: it decides
whether multi-user ([C6](RESEARCH_SPEC.md)) is even in scope, what [A8](A8-positioning.md)
can claim, how much onboarding/UX polish is justified, and what privacy/compliance rigor
is owed. Today it is de-facto **M1** (one maintainer, one corp).

## The three models

| | **M1 · Personal/internal** | **M2 · Open source** | **M3 · Commercial product** |
|---|---|---|---|
| **For** | The maintainer + maybe a few colleagues at the same corp | Privacy-conscious devs/teams at other air-gapped orgs who self-host | Regulated enterprises buying an on-prem/self-hosted product |
| **Distribution** | None | GitHub release + docs | Sales motion |
| **Pros** | Max velocity, full control, privacy trivially holds (single user), zero support burden | Distribution without a SaaS business; external validation; matches the "inspectable/self-hostable" wedge; no data-custody liability (they run it) | Revenue; the air-gap wedge is real + underserved; could fund lighting up the dark fleet |
| **Cons** | Limited impact; powerful work (flywheel, store, MCP) serves one person; no external signal | Support/docs/issue + security-disclosure burden; first-run must work for strangers | Enterprise sales; multi-tenant architecture becomes mandatory (C6); SOC2/DPIA-at-depth (C10); SLAs; a big leap |
| **Maintenance** | Low | Medium–high | High |
| **UX investment justified** | Medium (good-enough for a handful) | High — [B4](RESEARCH_SPEC.md) first-run becomes critical | High + enterprise-admin tooling |
| **Multi-user (C6)** | No | No (single-user-per-host) | Yes (mandatory) |

## Decision criteria

- **Wedge value** — the air-gap/on-prem niche is real and underserved → favors M2/M3.
- **Maintenance appetite** — a single maintainer → favors M1/M2, against M3.
- **Privacy posture** — M1 is trivially private (single user); M2 pushes custody to the
  self-hoster (clean); M3 takes on custody/compliance liability → favors M1/M2.
- **Learning per unit effort** — M2 surfaces external first-run data (feeds B4/B5) at low
  cost; M3's learning comes bundled with a sales commitment.
- **Architecture readiness** — already OSS-friendly: self-hostable, secrets-ENV-only, no
  vendor services in the path. The marginal cost of M2 is mostly *onboarding polish*, not
  re-architecture.

## Recommendation

> **Research-verified v2** (see [`research/A11-market-and-model.md`](research/A11-market-and-model.md)
> + [`research/SYNTHESIS.md`](research/SYNTHESIS.md)). The evidence **supports the path**, with
> three refinements baked in below: (a) scope **M2 as source-available / inspectable + low-burden**,
> not "build a community"; (b) **decide the monetization stance *before* publishing** — every recent
> *relicense-to-monetize* (HashiCorp→OpenTofu, Redis→Valkey, Elastic) triggered a fork and lasting
> distrust with no proven revenue upside; (c) set M2's expectation as **validation, not revenue** —
> the people *with budget* buy on-prem *platforms* (served by funded vendors), not a solo digest.

**M1 now → a deliberate, low-cost path to M2 (OSS) as the next step → M3 explicitly
deferred** (not pursued unless an external pull appears).

- **Why M1 now:** keeps velocity while the product and the dark-feature activation are
  still maturing; single-user keeps privacy and ops trivial.
- **Why M2 next (and cheap):** the architecture is already self-hostable and
  no-egress-by-default — the OSS wedge ([A8](A8-positioning.md) #5) is *already true*. The
  only real gap to an OSS release is **first-run success for a stranger** ([B4](RESEARCH_SPEC.md))
  plus a clean README/installer (largely done). M2 then yields external validation
  without a sales motion or a custody liability.
- **Why defer M3:** commercial is a *different commitment* — multi-tenant architecture
  (C6), enterprise sales, certification (C10 at depth), SLAs. Take it on only when there
  is **demonstrated external demand**, which M2 is the cheapest way to discover. **Guardrail
  (research):** if M3 ever activates, ship it as a **separate commercial edition kept
  proprietary from inception** — *never* a retroactive relicense of the OSS core. The public
  record is unanimous: HashiCorp, Redis, and Elastic all forked, and even Elastic's *reversal*
  didn't undo the fork.

## What this decision gates

- **C6 (multi-user): OUT for now.** M1 and M2 are single-user-per-install. Revisit only
  if M3 is triggered.
- **A8 (positioning):** lead with the **self-hostable + inspectable + air-gap** wedge —
  it holds for both M1 and M2, so positioning work isn't wasted.
- **UX investment level:** invest enough that a **stranger can self-host and succeed**
  (raises [B4](RESEARCH_SPEC.md) first-run, and [B5](RESEARCH_SPEC.md) discoverability, in
  priority *if/when* M2 is pursued) — but **not** enterprise-admin UX.
- **Privacy/compliance rigor:** M1/M2 need the **personal-grade** posture (already in
  place: PC-2, retention, redaction). **M3-level** DPIA/threat-model ([C10](RESEARCH_SPEC.md))
  and fleet-activation rigor ([C4](RESEARCH_SPEC.md)) stay deferred until M3.

## What would change the call

- A **colleague-team at the corp** wanting it → a small **M1.5** (a few users, one host) —
  reopens a *narrow* slice of C6 (shared-host multi-user), not full multi-tenant.
- **Another org** expressing interest → pulls toward M2/M3 and makes the C6/C10 work real.
- The **deployment model going off-corp/standalone** → this is also the only condition
  that reopens the local-embeddings non-goal (see [`RESEARCH_SPEC.md`](RESEARCH_SPEC.md) §8).

## Decision-gate status (per A11)

✅ **A model is chosen with rationale:** **M1 today, M2 (OSS) as the next deliberate
step, M3 deferred pending external demand.** This sets C6 = out-for-now, tells the UX
track to optimize for stranger-self-host (not enterprise admin), and keeps compliance at
personal-grade until an external pull appears.
