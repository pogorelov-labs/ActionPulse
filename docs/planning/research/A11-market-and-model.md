# A11 — Market & Model: deep-research brief

> **Deliverable for** [`../RESEARCH_SPEC.md`](../RESEARCH_SPEC.md) item **A11** (business / deployment model) ·
> deep-research backing for the M1→M2→M3 path · 2026-06-20 · external evidence.
> **Companion decision memo:** [`../A11-business-model.md`](../A11-business-model.md) · **Positioning:** [`../A8-positioning.md`](../A8-positioning.md).
> Method: multi-source web research, 2025–2026 sources, key claims cross-checked ≥2 ways and flagged where they aren't.
> Confidence tags inline: **[H]** high / **[M]** medium / **[L]** low.

---

## 1. TL;DR

- **The on-prem/sovereign-AI tailwind is real and rising in exactly ActionPulse's niche** (regulated, air-gapped). Deloitte's *State of AI in the Enterprise* (3,235 leaders, 24 countries, Aug–Sep 2025) found **~83% call sovereign AI important to strategy** and **77% now factor vendor country-of-origin into selection**; Enterprise Strategy Group found **78% would prefer to run AI on-premises**. **[H]** This validates the *wedge*, not a *market ActionPulse can sell into* — these are buyers of platforms/infra, not of a single-maintainer digest.
- **Demand ≠ addressable revenue for a one-person tool.** The figures describe enterprise procurement of on-prem AI *platforms* (and the vendors filling that gap are funded companies: IBM, Tabnine, MosaicML/Databricks, TrueFoundry, Cohere). A solo digest competes on the *individual-knowledge-worker* layer those platforms don't serve — a much thinner, harder-to-monetize slice. **[M]**
- **OSS→commercial is a well-trodden but treacherous path.** Open-core *can* reach IPO scale (**GitLab**: ~$11B IPO, 90% revenue from subscriptions, ~0.05% free→paid conversion). But the recent dominant story is **license-change backlash → community forks** (HashiCorp→**OpenTofu**, Redis→**Valkey**, Elastic SSPL→partial reversal to AGPL), and "no evidence the license changes improved revenue." **[H]** The lesson for A11: monetizing *after* an OSS community exists is high-risk and reputation-costly.
- **Single-maintainer reality is sobering.** ~60% of OSS maintainers are unpaid, ~61% of unpaid ones work alone, ~60% have quit or considered quitting (44% citing burnout); support can eat ~80% of project time. Marquee 2024–25 failures: **xz-utils** backdoor (solo-maintainer social-engineering), **Kubernetes retiring Ingress-NGINX** (Nov 2025) because volunteers couldn't sustain it. **[H]**
- **Net for ActionPulse:** the evidence **supports M1 now → M2 (OSS) deliberately → M3 deferred**, with two refinements: (i) M2 should be **source-available / inspectable** more than "build a community" — the value is *validation + the no-egress wedge made true*, not headcount; (ii) **never assume M2→M3 is a clean upgrade** — relicensing an OSS project to monetize is the single most reputation-damaging move in the dataset. If M3 is ever real, it should be a *separate commercial edition from day one*, not a rug-pull. **[M]**

---

## 2. Market & demand

**The structural driver is regulation, not preference.** For financial services, non-public financial information generally cannot transit external APIs under GLBA / the FTC Safeguards Rule; healthcare adds HIPAA; defense/government adds ITAR, CUI/UCNI handling, FedRAMP-High, PCI-DSS 4.0. For the most sensitive workloads, **air-gapped on-prem is the only compliant path**, not an optimization. **[H]** This is precisely ActionPulse's founding context (on-prem EWS + self-hosted Mattermost + gateway LLM, no cloud egress).

**Survey signal (cross-checked):**

| Figure | Source | Confidence / caveat |
|---|---|---|
| **~83%** view sovereign AI as important to strategy; **77%** factor vendor country-of-origin; ~3-in-5 build stacks primarily with local vendors | Deloitte, *State of AI in the Enterprise* (2026 report; 3,235 leaders / 24 countries; fielded Aug–Sep 2025) | **[H]** — survey scope confirmed across multiple Deloitte regional pages |
| **78%** of orgs would *prefer* to run AI applications **on-premises** | Enterprise Strategy Group (ESG, now part of Omdia), AI-infrastructure research | **[M]** — it's a stated *preference*, not measured deployment; ESG/TechTarget primary |
| **86%** expect AI-infra budgets to **>3×** within ~3 yrs | Deloitte AI-Infrastructure survey (Dec 2025) | **[M]** — secondary-sourced; directionally consistent |
| On-prem held **~57%** of AI infra *spending* in 2025 (data-residency / HIPAA cited) | Mordor Intelligence (enterprise/AI-infra market report) | **[L]** — market-research-firm estimate; methodologies vary widely |
| Edge-AI market ~**$25B (2025) → ~$118–165B (2033–35)**, CAGR ~20–22% | Precedence Research / Grand View | **[L]** — "edge AI" ≠ on-prem enterprise AI; adjacent proxy only |

> ⚠️ **Recency/accuracy flag.** A secondary source attributed **"71% of AI infrastructure now operates outside public cloud"** to ESG (2025). I could **not** confirm that figure in ESG's own material — what ESG actually publishes is the **78% *preference*** number. Treat "71% deployed off-cloud" as **unverified [L]** and do not cite it externally. The honest, defensible claim is *preference is strongly on-prem; measured deployment still skews cloud for early AI.*

**Vendor moves confirm the demand is being met — by funded companies, not solos.** 2025 examples: **IBM** introduced a defense-grade model for isolated environments; **Los Alamos** self-hosted LLMs (Jan 2025) for CUI/UCNI/ITAR; **Tabnine** sells fully air-gapped code-assist ($39/user/mo) into defense/banking; **MosaicML/Databricks, TrueFoundry, Cohere** target on-prem/VPC inference. **[H]** **Implication:** the *infrastructure* layer is crowded and capitalized; the *opinionated personal-productivity* layer (a cited daily action digest over EWS+Mattermost) is **not** something these platforms ship — that's the genuine gap, but it's a feature-shaped gap, not a market a solo dev can defend with a sales motion.

---

## 3. OSS→commercial patterns (named examples)

| Project | Model used | Outcome | Lesson for A11 |
|---|---|---|---|
| **GitLab** | Open-core (Free + Premium/Ultimate; self-managed *and* SaaS) | **Success.** ~$11B IPO (Oct 2021); ~90% revenue from subscriptions; ~30M users but ~15k paying (**~0.05% conversion**) | Open-core *can* scale — but on a **brutal funnel** + a real sales org. Not a solo path. **[H]** |
| **n8n** | Open-core; Community Edition free self-host + paid Cloud/Enterprise (€20–50/mo + custom) | Healthy; "platform for technically-minded SMBs," data-sovereignty as a selling point | Closest analogue to ActionPulse's *shape* (self-host-free + paid tier). Still a funded company, not one maintainer. **[M]** |
| **Mattermost** | Open-core, self-hosted-first; 2025 launched a **free fully-featured "Entry" tier** | Sustained; leans into **sovereign / self-hosted** as the pitch | Validates the *positioning* (self-hosted = the product, not a fallback). **[H]** |
| **Sentry** | Source-available (BSL after 2019); self-host free, cloud paid | Sustained commercial; some community friction over BSL | "Source-available" is a viable middle path when the goal is inspectability, not a copyleft community. **[M]** |
| **HashiCorp (Terraform)** | **Relicensed** MPL→BSL (Aug 2023) to block competitors | **Backlash → OpenTofu fork** (Linux Foundation; Spacelift/env0/Scalr); "deepest community damage" | **The cautionary tale.** Relicensing an established OSS project to monetize triggers a fork and lasting distrust. **[H]** |
| **Redis** | **Relicensed** BSD→SSPL/RSALv2 (Mar 2024) | **Backlash → Valkey fork** (Linux Foundation; AWS/Google/Oracle); 150+ contributors in weeks; Redis later re-added AGPL (2025) | Same pattern, faster fork. "Pleasing basically no one." **[H]** |
| **Elastic** | SSPL (2021) → **reverted to AGPL (Aug 2024)** — first reversal | Distro removals, OpenSearch fork persisted | Even *reversing* the change didn't undo the fork. The damage is sticky. **[H]** |

**Synthesis [H]:** Two distinct stories. (1) *Born-commercial open-core* (GitLab, n8n, Mattermost, Sentry) — works, but needs a company. (2) *Relicense-to-monetize-later* (HashiCorp, Redis, Elastic) — consistently produces forks, distro removals, and durable distrust, with **no demonstrated revenue upside in the public record**. For a project that ships OSS *first*, the safe monetization route is a **separate paid edition / hosted offering kept commercial from the start**, never a retroactive license flip on the open code.

---

## 4. Single-maintainer trade-offs

Evidence base: Tidelift 2024 maintainer survey (437 resp.), OpenSSF/Ecosyste.ms data, Socket/Linux Foundation reporting, arXiv mixed-methods study on maintainer vulnerability management.

- **Maintenance & support burden.** ~60% of maintainers unpaid; **~61% of unpaid maintainers work alone**; support/community can consume **~80% of project time** (≈20% left for code). **[H]** Going OSS converts *velocity* into *triage*.
- **Attrition is the base rate.** **~60% have quit or considered quitting** (44% burnout, 48% feel underappreciated). **[H]** A solo OSS project's *expected* trajectory is abandonment unless scoped to stay low-burden.
- **Security disclosure is a real, asymmetric liability.** Going OSS creates an implicit duty to receive and act on vulnerability reports. **xz-utils** (2024) showed solo maintainers are *targets* for social-engineering supply-chain attacks; post-xz, **66% of maintainers became less trusting of external PRs.** **[H]** ActionPulse handles corporate mail + chat content — a disclosure mishandled here is reputationally worse than average.
- **The "it dies when the person stops" risk is concrete.** **Kubernetes retired Ingress-NGINX (Nov 2025)** — a flagship component — purely because volunteer maintainers couldn't sustain it. **[H]**
- **Monetization at solo scale is the weakest link.** Donations rarely fund a person; the GitLab funnel (~0.05% paid) shows even great products convert thinly; paid support implies SLAs a solo can't honor. **[H]**

**Net:** personal/internal (M1) carries *none* of these costs. OSS (M2) adds support + security-disclosure duty + abandonment risk but can be **bounded** (clear "no-SLA, best-effort" stance, a security policy, narrow scope). Commercial (M3) multiplies all of them and is structurally a *team* undertaking. **[H]**

---

## 5. Implications for the M1 → M2 → M3 path

**Does the evidence support the tentative path? — Largely yes, with two refinements. [M–H]**

- **M1 (personal/internal) now — supported [H].** The single-user posture sidesteps the *entire* maintainer-burden and disclosure-liability literature. Privacy holds trivially. This is the correct default while the product and the "dark" features mature. The market data doesn't argue against M1; it argues the *wedge* is real for *later*.
- **M2 (open-source) next — supported, but reframe [M].** Three independent signals favor it: (i) the **sovereign/self-hosted positioning is exactly what's winning** (Mattermost's own pivot, Deloitte's 77–83%); (ii) the architecture is *already* no-egress/self-hostable, so the marginal cost is onboarding polish, not re-architecture (consistent with the companion memo); (iii) OSS yields external validation + real first-run data without a sales motion or data-custody liability. **Reframe:** treat M2 as **"source-available / inspectable + low-burden"** rather than "grow a contributor community." The value is the *wedge made literally true* and *signal*, while explicitly **capping** the maintainer burden the §4 evidence warns about (no-SLA stance, security policy, narrow scope, no promise of support). Pick a permissive or source-available license deliberately — and **decide the monetization stance *before* publishing**, because §3 shows reversing later is the costly move.
- **M3 (commercial) deferred — strongly supported [H].** The demand figures are real but describe **enterprise platform/infra procurement**, served by funded vendors (IBM, Tabnine, MosaicML, TrueFoundry, Cohere). A single maintainer cannot field multi-tenant architecture (C6), SOC2/DPIA-at-depth (C10), SLAs, and an enterprise sales motion. Defer until *demonstrated external pull* — which M2 is the cheapest instrument to detect. **Critical guardrail from §3:** if M3 ever activates, ship it as a **separate commercial edition kept proprietary from inception**, never a retroactive relicense of the OSS core (HashiCorp/Redis/Elastic all forked).

**One honest tension the memo should hold:** the same evidence that says "the niche is real and underserved" also says "the people *with budget* are buying platforms, not personal tools." So M2's realistic payoff is **validation + reputation + the occasional self-hosting peer**, not a user base that converts to revenue. That's fine *if* M2 is scoped as low-burden inspectable-release — and it's a trap if it's scoped as "build toward a business." The path survives; the *expectations* attached to M2 need to be modest.

---

## 6. Open questions / low-confidence

- **The "71% off-cloud deployed" figure is unverified [L]** — excluded from any external claim; only the 78% *preference* (ESG) and Deloitte 77–83% sovereignty numbers are safe to cite.
- **No clean sizing exists for "personal/individual on-prem AI productivity tools."** All figures found are enterprise *platform/infra* TAM; the individual-knowledge-worker-behind-an-air-gap slice is **un-sized in public data [L]**. ActionPulse's true addressable population is unknown.
- **Market-research-firm market-size numbers (Mordor, Precedence, Grand View) diverge widely** and use inconsistent "on-prem/edge/sovereign" definitions — treat any single dollar figure as **[L]**.
- **Donation/sponsorship viability for a niche corp-productivity OSS tool is untested** — the maintainer-funding literature is general; nothing specific to this category.
- **Conflict to watch:** vendor blogs (TrueFoundry, Squirro, PredictionGuard, etc.) have a *commercial interest* in overstating on-prem demand. The Deloitte/ESG primary surveys are the more neutral anchors; vendor-blog specifics are corroborating, not primary. **[M]**

---

## 7. Sources

**Market & demand (regulated / sovereign / on-prem AI):**
- Deloitte — *State of AI in the Enterprise* (2026 report; 3,235 leaders, 24 countries, fielded Aug–Sep 2025): https://www.deloitte.com/global/en/issues/generative-ai/state-of-ai-in-enterprise.html and press release https://www.deloitte.com/us/en/about/press-room/state-of-ai-report-2026.html
- Deloitte — AI-Infrastructure survey (2028 outlook, Dec 2025): https://www.deloitte.com/us/en/insights/topics/technology-management/ai-infrastructure-survey.html
- Enterprise Strategy Group (Omdia) — *Navigating the Evolving AI Infrastructure Landscape* (78% prefer on-prem): https://www.techtarget.com/esg-global/research-report/navigating-the-evolving-ai-infrastructure-landscape/
- PredictionGuard — best self-hosted models for regulated industries (GLBA/FTC Safeguards framing): https://predictionguard.com/blog/best-self-hosted-ai-models-regulated-industries
- TrueFoundry — air-gapped AI in regulated industries: https://www.truefoundry.com/blog/air-gapped-ai-deploying-enterprise-llms-in-highly-regulated-industries
- AInvest — on-prem LLM & computational sovereignty (secondary; "71%" claim, **not verified**): https://www.ainvest.com/news/rise-premises-llm-deployment-computational-sovereignty-2025-2601/
- Mordor Intelligence — enterprise/AI-infra market reports (on-prem ~57% spend): https://www.mordorintelligence.com/industry-reports/ai-infrastructure-market
- Precedence Research — Edge AI market size: https://www.precedenceresearch.com/edge-ai-market
- DreamFactory — government/defense air-gapped LLM (Los Alamos, IBM defense model): https://blog.dreamfactory.com/government-defense-air-gapped-llm-data-access

**OSS→commercial patterns:**
- GitLab business model: https://thestrategystory.com/2022/11/20/how-does-gitlab-work-make-money-business-model/ · pricing/handbook: https://handbook.gitlab.com/handbook/company/pricing/
- n8n self-host vs cloud (2025): https://www.infralovers.com/blog/2025-05-09-n8n-workflow-automation/
- Mattermost free Entry tier (sovereign self-hosted): https://mattermost.com/newsroom/press-releases/mattermost-launches-free-entry-tier/
- "The open-core business model": https://dev.to/ryandawsonuk/the-open-core-business-model-363n
- Redis SSPL change + Valkey fork: https://www.theregister.com/software/2024/03/22/redis_changes_license/ · https://www.softwareseni.com/the-redis-valkey-fork-how-enterprises-rapidly-migrated-after-the-sspl-license-change/
- License-change pattern (MongoDB→Redis 2018–2026; HashiCorp/OpenTofu; Elastic reversal): https://www.softwareseni.com/the-open-source-license-change-pattern-mongodb-to-redis-timeline-2018-to-2026-and-what-comes-next/
- Source-available legal risks (SSPL/BSL): https://www.termsfeed.com/blog/legal-risks-source-available-licenses/

**Single-maintainer trade-offs:**
- Socket — *The Unpaid Backbone of Open Source* (Tidelift 2024 + OpenSSF/Ecosyste.ms data): https://socket.dev/blog/the-unpaid-backbone-of-open-source
- byteiota — maintainer crisis (60% unpaid / 44% burnout): https://byteiota.com/open-source-maintainer-crisis-60-unpaid-burnout-hits-44/
- Linux Foundation — what maintainers need: https://www.linuxfoundation.org/blog/open-source-maintainers-what-they-need-and-how-to-support-them
- arXiv — mixed-methods study, maintainer vulnerability management: https://arxiv.org/html/2409.07669v2
- RoamingPigs — maintainer burnout / Ingress-NGINX retirement (Nov 2025): https://roamingpigs.com/field-manual/open-source-maintainer-burnout/
