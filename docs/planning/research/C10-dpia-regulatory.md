# C10 — DPIA & Threat-Model Depth (Privacy / Compliance Research)

**Spec item:** C10 (DPIA / threat model depth) · **Status:** research input, not a completed DPIA
**Date:** 2026-06-20 · **Scope:** ActionPulse — daily corporate-comms digest (email + chat → LLM behind a corp gateway → encrypted short-retention store → digest delivery), deployed inside a bank's network, no cloud egress by default.

> ⚠️ **This is research, not legal advice.** It synthesises statute, regulator guidance and technical standards (current to June 2026) to scope a DPIA and threat model. Get DPO/legal counsel sign-off before relying on any conclusion here — especially the AI-Act tier call, the crypto-shred-as-erasure question, and anything jurisdiction-specific. Jurisdiction: **EU/GDPR is used as the rigorous baseline**; Russia (152-FZ) and banking-sector notes are flagged where they diverge.

---

## 1. TL;DR

- **A DPIA is almost certainly legally required.** ActionPulse does **large-scale, systematic processing of employee communications** containing PII. Under GDPR Art. 35 + the EDPB WP248 nine-criteria test it hits at least **systematic monitoring**, **large scale**, **vulnerable data subjects (employees)**, **innovative use (LLM)**, and likely **sensitive data** — well past the "two or more criteria ⇒ do a DPIA" threshold. Do the DPIA **before** any broader rollout (Art. 35(1) requires it *prior* to processing). Skipping it is a standalone fineable breach (up to €10M / 2% turnover, Art. 83(4)).
- **Threat-model with two lenses, one diagram.** Run **STRIDE** (security) **and LINDDUN** (privacy) over the same ingest→LLM→deliver Data Flow Diagram. STRIDE catches spoofed-gateway / disclosure / DoS; LINDDUN catches the privacy harms STRIDE misses — *Linkability* (profiling a colleague across messages), *Identifiability* (re-identification from a "de-identified" digest), *Detectability* (the digest reveals someone is being watched), *Unawareness* (senders never consented), *Non-compliance* (no legal basis / retention breach).
- **AI-Act tier: most likely *limited risk* (Art. 50 transparency) + an AI-literacy duty — but one design choice flips it to *prohibited*.** Action-item extraction/summarisation is not, on its face, an Annex III "employment/worker-management" high-risk use. **However, any "emotion/sentiment/engagement" inference about employees risks the Art. 5 ban on emotion recognition in the workplace, and any performance-evaluation use pushes it into Annex III high-risk.** Keep it extraction-only.
- **Lawful basis is the soft underbelly.** Consent is generally **not** valid for employee monitoring (power imbalance). Expect to rely on **legitimate interest** (Art. 6(1)(f)) with a documented balancing test, plus **Art. 14 transparency** to senders whose messages are ingested.
- **Erasure & retention are an engineering problem, not a flag.** Implement **crypto-shredding with per-subject keys** for the right to erasure, propagate deletion to backups/logs/indexes (or put backups "beyond use"), keep the short rolling-retention window with **automated expiry**, and treat the LLM gateway as a **processor** with a deletion duty (Art. 28(3)(g)).
- **Residency reinforces the existing posture.** No-cloud-egress + in-network gateway already aligns with EU transfer rules **and** Russia's 152-FZ localisation (which since July 2025 binds **processors** too). Don't regress it.

---

## 2. DPIA — triggers, required contents, how to run one here

### 2.1 When a DPIA is legally required
GDPR **Art. 35(1)**: a DPIA is mandatory where processing "is **likely to result in a high risk** to the rights and freedoms of natural persons," and must be carried out **prior to the processing**.

**Art. 35(3) — three automatic triggers** (any one ⇒ DPIA): (a) systematic & extensive **evaluation/profiling** producing legal or similarly significant effects; (b) **large-scale** processing of **special-category** (Art. 9) or criminal data; (c) **systematic monitoring of a publicly accessible area** on a large scale.

**EDPB/WP29 WP248 rev.01 — nine criteria** (the practical test; **meeting two or more usually means a DPIA is required**):
1. **Evaluation or scoring** (profiling, prediction).
2. **Automated decision-making** with legal or similarly significant effect.
3. **Systematic monitoring.**
4. **Sensitive data or data of a highly personal nature.**
5. **Data processed on a large scale.**
6. **Matching or combining datasets.**
7. **Data concerning vulnerable data subjects** — *explicitly includes **employees*** (power imbalance).
8. **Innovative use / new technological or organisational solutions** (e.g. **LLM/AI**).
9. **Processing that prevents data subjects from exercising a right or using a service/contract.**

**ActionPulse maps to ≥4–5 criteria** (3 systematic monitoring · 5 large scale · 7 employees · 8 LLM; 4 sensitive data is likely, since free-text comms routinely contain health/union/political content). Verdict: **DPIA required.** Many national DPA "blacklists" (Art. 35(4)) also list employee-monitoring and AI processing explicitly — check the relevant supervisory authority's list.

### 2.2 What a DPIA must contain — Art. 35(7) (verbatim minimum)
- **(a)** "a systematic description of the envisaged processing operations and the purposes of the processing, including, where applicable, the legitimate interest pursued";
- **(b)** "an assessment of the **necessity and proportionality** of the processing operations in relation to the purposes";
- **(c)** "an assessment of the **risks to the rights and freedoms** of data subjects";
- **(d)** "the **measures envisaged to address the risks**, including safeguards, security measures and mechanisms to ensure the protection of personal data."

Procedural duties: **seek the DPO's advice** (Art. 35(2)); **where appropriate, seek the views of data subjects** or their representatives (Art. 35(9)); if residual risk stays high after mitigation, **prior consultation with the supervisory authority** (Art. 36); review when the processing changes.

### 2.3 How to run one for this pipeline (practical steps)
1. **Describe the flow (35(7)(a)):** draw the DFD (ingest adapters → extraction/LLM gateway call → assemble → encrypted store → delivery), list data categories (sender/recipient identities, message bodies, derived action items, evidence refs), purposes, recipients, retention, the gateway as processor, and any transfers.
2. **Necessity & proportionality (35(7)(b)):** justify each data category against the purpose; show **data minimisation** (redact DM bodies, send the gateway only what's needed); record the **lawful basis** + the **legitimate-interest balancing test**; justify the retention window.
3. **Risk assessment (35(7)(c)):** **this is where the threat model plugs in** — feed STRIDE + LINDDUN findings (§3) as the enumerated risks, scored by likelihood × severity to data subjects.
4. **Mitigations (35(7)(d)):** map each risk to a control (encryption at rest, key separation, access control, retention/expiry, redaction, gateway no-retention/ZDR contract, audit-metadata-not-payload logging, transparency notices, erasure mechanism).
5. **Sign-off & review:** DPO opinion, document residual risk, decide on Art. 36 consultation, set a review trigger (new source, new model, scope change). Keep it **versioned in-repo** alongside this brief.

---

## 3. Threat modeling — STRIDE vs LINDDUN applied to ActionPulse

**Use both on one DFD.** STRIDE = security (what an attacker breaks); LINDDUN = privacy (what the *system itself* does to data subjects). They are complements, not substitutes — STRIDE touches privacy only via "Information Disclosure." The cleanest illustration of the duality is **non-repudiation**: security *wants* it; privacy treats it as a *threat* (the data subject loses plausible deniability — directly relevant to ActionPulse's evidence-traced items).

**DFD elements:** *external entities* = message senders/participants (data subjects), digest readers, the LLM gateway; *processes* = ingest, extract/LLM, assemble, deliver; *data stores* = encrypted result store, logs; *trust boundaries* = corp-network edge, gateway boundary, recipient boundary.

### 3.1 STRIDE (security) — per element, with property violated
| Threat (property) | ActionPulse example | Primary mitigation |
|---|---|---|
| **S**poofing (authentication) | Rogue/spoofed LLM gateway harvests submitted text; spoofed user requests another's digest | mTLS / pinned gateway identity; authn on digest access |
| **T**ampering (integrity) | Stored digest/evidence altered to fabricate or hide a statement | Integrity checks (HMAC/AEAD), access control, validate model output |
| **R**epudiation (non-repudiation) | Can't prove who read a digest or triggered an LLM call | Log **access metadata** (not payloads) — reconciles with "never log payloads" |
| **I**nfo disclosure (confidentiality) | PII to gateway logs; over-broad read on store; **key stored next to ciphertext** | Encrypt in transit + at rest, **separate keys from data**, least-privilege |
| **D**oS (availability) | Flood gateway/ingest; blow the 15-RPM / cost cap | Rate limits, quotas, backpressure |
| **E**oP (authorization) | Low-priv user reads others' digests / reaches keys; path traversal in out-dir | RBAC, key isolation, harden API/MCP surface |

### 3.2 LINDDUN (privacy) — the harms STRIDE misses
LINDDUN PRO (linddun.org, 2024) categories — *classic noun in brackets*:
| Category (privacy property) | ActionPulse example | Mitigation direction |
|---|---|---|
| **Linking** [Linkability] (unlinkability) | Reader correlates a colleague across email+chat+threads via evidence chain → behavioural profile; gateway logs keyed by caller link batches longitudinally | Minimise cross-source joining; scrub gateway-log keys; purpose-bind |
| **Identifying** [Identifiability] (anonymity) | "De-identified" digest re-identifies via quasi-identifiers (team, role, meeting time) or **stylometry** (LLM preserves a phrasing) | Don't claim anonymisation; suppress quasi-IDs; treat output as personal data |
| **Non-repudiation** (plausible deniability) | Evidence-traced item (`evidence_id`+`source_ref`) becomes **undeniable attributed proof** usable in an HR/legal dispute over an informal DM | Limit attribution granularity; access control; retention limits |
| **Detecting** [Detectability] (undetectability) | A digest *section about a person* reveals they're under scrutiny — independent of content; diffing digests = membership inference on a sensitive topic | Suppress low-volume/sensitive subjects; aggregate carefully |
| **Data Disclosure** [Disclosure] (confidentiality) | Extracted PII **retained by the gateway/model** outside the pipeline's window; **prompt injection** in an ingested message forces PII into the digest (OWASP LLM01/LLM02) | Redact before send; ZDR/no-retention gateway contract; injection defences; output validation |
| **Unawareness & Unintervenability** [Unawareness] (transparency) | Senders/participants never consented and don't know their messages are LLM-summarised for someone else → **Art. 14** likely unmet | Transparency notice; honour data-subject rights; intervention path |
| **Non-compliance** (policy/consent) | No valid legal basis; retention/storage-limitation breach; missing DPIA; gateway routes cross-border | The §2 DPIA + §4 obligations + §5 retention/erasure |

> **Lightweight option:** if a full LINDDUN PRO pass is too heavy, **LINDDUN GO** (a 33-card deck, 7 suits) is a fast facilitated elicitation — useful for a first workshop, then deepen the high-risk flows with PRO + privacy threat trees.

---

## 4. Regulatory obligations — GDPR + EU AI Act + residency/banking

### 4.1 GDPR core
- **Roles.** The **bank is the controller** (sets purpose/means of processing its employees' comms). The **LLM gateway is a processor** if it processes on the controller's instructions and doesn't reuse the data → needs an **Art. 28 data-processing agreement** (incl. **28(3)(g)** delete/return at end). If the gateway provider repurposes inputs (e.g. training), it risks becoming an independent controller — avoid.
- **Lawful basis (Art. 6).** **Consent is generally invalid in employment** (no genuine free choice — WP29 Opinion 2/2017). Realistic basis = **legitimate interest (6(1)(f))** with a **documented three-part balancing test** (purpose / necessity / balancing against employee rights, incl. the "chilling effect" on confidential comms). Special-category content in messages (Art. 9) needs an **Art. 9(2)** condition — a real gap to design around (minimise/redact rather than rely on a shaky condition).
- **Principles (Art. 5).** Lawfulness/fairness/transparency; **purpose limitation**; **data minimisation (5(1)(c))** — send the gateway the minimum, redact DM bodies; **storage limitation (5(1)(e))** — short window (§5); **integrity & confidentiality (5(1)(f))** — encryption (§5); **accountability (5(2))** — document everything (ROPA Art. 30, DPIA, balancing test).
- **Transparency (Arts. 13/14).** Because messages are collected **from senders who aren't the digest user**, **Art. 14** (data obtained indirectly) almost certainly applies — proactive notice to those data subjects.
- **Security (Art. 32).** Names **pseudonymisation and encryption** as appropriate measures; risk-based.

### 4.2 EU AI Act — tier assessment
The Act stacks **four independent checks** (don't treat it as a simple pyramid): **prohibited (Art. 5)** · **high-risk (Art. 6 + Annex III)** · **transparency (Art. 50)** · **GPAI obligations (Chapter V, on the model provider)**.

- **Prohibited (Art. 5):** **emotion recognition in the workplace is banned** (except medical/safety). ➜ **Hard design line: ActionPulse must not infer employees' emotional/affective state.** Sentiment/engagement scoring of staff is a red zone.
- **High-risk (Annex III):** the **employment/worker-management** category covers AI for **recruitment, evaluation, promotion, task allocation by traits, and performance monitoring**. ➜ **As specified (action-item extraction + summarisation, not evaluating or ranking workers), ActionPulse is most likely *not* Annex III high-risk** — but it's **adjacent**: if outputs feed performance management or productivity scoring, it tips into high-risk (and then: risk-management system, data governance, technical documentation, logging, human oversight, accuracy/robustness/cybersecurity, registration). *Confidence: medium — this is a fact-specific call for counsel.*
- **Limited risk (Art. 50 transparency):** the realistic tier. Where the system interacts with people or produces AI-generated text, **disclose AI involvement**; mark AI-generated content where applicable. Pragmatically: label the digest as **AI-generated/AI-assisted**.
- **AI literacy (Art. 4):** **already in force since 2 Feb 2025** — providers *and* **deployers** must ensure staff operating the system have a **sufficient level of AI literacy**. The bank (deployer) must train relevant staff. *Confidence: high.*

**Timeline / recency (verify before relying):** Act in force 12 Jul 2024; prohibitions + AI-literacy applicable **2 Feb 2025**; GPAI rules **2 Aug 2025**; **Art. 50 transparency applicable 2 Aug 2026**. The **"Digital Omnibus" (provisional agreement May 2026)** is reported to **defer Annex III high-risk obligations from Aug 2026 to ~December 2027** and to grant pre-existing generative systems until ~Dec 2026 for machine-readable marking — **moving target, confirm against the final text.**

### 4.3 Residency & banking (high level)
- **Cross-border transfers (GDPR Ch. V):** if the "internal" gateway or any component sits outside the EEA, you need a transfer mechanism (adequacy/SCCs + a transfer-impact assessment). **No-cloud-egress + in-network gateway sidesteps this** — keep it.
- **Russia 152-FZ (data localisation):** Russian citizens' personal data must be recorded/stored in **databases located in Russia**, and **since July 2025 the duty binds processors, not just operators.** ActionPulse's in-network/no-egress design is consistent with this; an externally-hosted model would not be. *(Russia is outside GDPR; treat 152-FZ as the parallel regime.)*
- **Banking sector:** beyond data-protection law, expect **operational-resilience / outsourcing rules** — in the EU, **DORA** + **EBA outsourcing guidelines** (ICT third-party risk, exit/audit rights, register of arrangements); for a Russian bank, **Bank of Russia** requirements and **GOST**-aligned crypto/security. These reinforce: documented third-party (gateway) risk, strong crypto, auditability. *Confidence: medium — sector specifics need the bank's compliance team.*

---

## 5. Right-to-be-forgotten + at-rest / retention patterns

### 5.1 Data-subject rights (the deletion-relevant ones)
- **Erasure (Art. 17):** six grounds (incl. **17(1)(a) data no longer necessary** — the same trigger as storage limitation); **exemptions (17(3))** incl. legal obligation and **establishment/exercise/defence of legal claims**. **Response within one month** (Art. 12(3), extendable +2 months for complex requests with notice). Duty extends to **all copies** — ICO is explicit it reaches **backups** as well as live systems. **Art. 17(2)** (inform other controllers if made public) and **Art. 19** (notify each recipient) extend the reach; **Art. 28(3)(g)** is the lever to make the **LLM processor delete**.
- Neighbours: **access (Art. 15)**, **rectification (Art. 16)**, **restriction (Art. 18 — store-but-don't-use freeze)**. A clean **data inventory / "where is this person's data" map** serves access *and* erasure — build it once.

### 5.2 Deletion implementation patterns
- **Soft delete ≠ erasure.** A `deleted` flag that leaves data queryable/restorable does not satisfy Art. 17. Use a **two-phase TTL** (hide immediately → irreversible hard purge across all stores). Watch deferred-deletion engines (Cassandra tombstones / Lucene segment merges can resurrect or retain data).
- **Crypto-shredding (the recommended primary pattern).** Encrypt each subject's data under a **per-subject key**; erasure = **destroy that key** → ciphertext is unrecoverable. **NIST SP 800-88 Rev.2** classifies **Cryptographic Erase** as a valid **Purge**. Per-subject keys make it **surgical** (one person gone, others untouched) and are the standard answer for **immutable/append-only** stores and **backups**.
  - **Caveats (must hold or it isn't erasure):** data was **encrypted before storage** (no plaintext predates the key); **all key copies destroyed** (incl. KEK and backup/escrow); strong algorithm. **And the honest legal point: no binding EU ruling equates crypto-shred with Art. 17 "erasure."** Encrypted data is **pseudonymous, still personal data** (EDPB 01/2025; WP216). Crypto-shred works only because, once *every* key copy is gone, re-identification is no longer "reasonably likely" (Recital 26). The EDPB's 2026 coordinated-enforcement work lists **crypto-erasure** among acceptable responses to the backup problem — supportive *in context*, not a safe harbour. **Treat it as the dominant, defensible pattern under strict key hygiene — not a guarantee.** *Confidence: high on the premises; medium/risk-based on the equation.*
- **Propagate deletion** to backups, **logs**, search indexes, caches, replicas, and **derived/aggregated data**. For backups you can't instantly overwrite, the ICO accepts putting them **"beyond use"** (no other use, deleted on the established backup cycle, individual informed). Don't-log-payloads is the cleanest way to keep logs out of erasure scope.
- **LLM processor copies:** even a perfect erasure can't retroactively pull data from a model that **trained** on it; and major APIs retain inputs **~30 days** for abuse monitoring by default (longer if safety-flagged). ➜ **Minimise/redact what you send, prefer a no-retention/ZDR contract or on-prem model, and never assume API erasure is retroactive.**

### 5.3 Encryption at rest + retention best practice
- **Algorithm/mode:** **AES-256** in an **AEAD mode (AES-GCM)** — confidentiality + integrity; **never reuse a (key, nonce) pair**. *(Note: **SQLCipher**'s default is **AES-256-CBC + per-page HMAC-SHA512**, not GCM — integrity comes from its HMAC layer; that's a legitimate at-rest choice for a SQLite-backed store, just don't describe it as AEAD/GCM.)*
- **Envelope encryption:** data under a **DEK**, DEK wrapped by a **KEK** in a KMS/HSM; store wrapped DEK with the ciphertext. Enables cheap rotation and is the **structural prerequisite for crypto-shred**.
- **Key management:** **separate keys from ciphertext** (storing the key next to the data defeats the control); rotate on a cryptoperiod or suspected compromise; least-privilege + audit on key use.
- **Layer choice:** **field-level + per-subject keys** is the high-value option here — survives DB compromise *and* enables per-person crypto-shred; layer over full-disk for media-theft defence.
- **Retention (Art. 5(1)(e)):** **no GDPR-mandated numeric period** — set it by **purpose** and justify it. A **short rolling window with automated expiry** is strong evidence of storage-limitation + minimisation compliance **and** turns most erasure requests into no-ops. Build expiry in by default (**Art. 25**). Document periods in a **retention schedule + ROPA (Art. 30(1)(f): envisaged erasure time-limits)**. *(Honesty flag: don't claim "GDPR requires 7 days" — it doesn't; the duration must be purpose-justified.)*
- **Encryption ≠ minimisation/retention.** Encryption (Art. 32) mitigates **breach**, but encrypted data you no longer need is **still data you must delete**. Retention limits + crypto-shred discharge Art. 5(1)(e) and Art. 17 — encryption doesn't.

---

## 6. Recommended DPIA + threat-model outline for ActionPulse

**A. DPIA document (versioned in-repo, owned with the DPO):**
1. **Context & necessity** — purpose, why an LLM, why each data source; **lawful-basis record** + **legitimate-interest balancing test**; Art. 9 condition or the minimisation strategy that avoids needing one.
2. **Systematic description (35(7)(a))** — the DFD, data categories, recipients, retention, gateway-as-processor + DPA reference, transfers (and the no-egress posture).
3. **Necessity & proportionality (35(7)(b))** — minimisation (DM-body redaction, minimum-to-gateway), retention justification, transparency (Art. 14) plan.
4. **Risk assessment (35(7)(c))** — **import the STRIDE + LINDDUN register** (§3), each scored likelihood × severity-to-subjects.
5. **Mitigations (35(7)(d))** — control per risk (encryption at rest + key separation, RBAC, retention/expiry, crypto-shred erasure, ZDR/no-retention gateway, audit-metadata logging, AI-disclosure label, injection/output-validation defences).
6. **Compliance posture** — GDPR rights handling; **EU AI Act tier conclusion** (limited-risk + Art. 4 literacy; the prohibited/high-risk lines to *not* cross); residency (Ch. V / 152-FZ); banking outsourcing notes.
7. **Sign-off** — DPO opinion, residual-risk decision, **Art. 36** consultation call, review triggers.

**B. Threat model (the §4-risk input, kept as a living artefact):**
- One **DFD** with trust boundaries → **STRIDE per element** (§3.1) → **LINDDUN PRO** on high-risk flows (or **LINDDUN GO** workshop first) (§3.2) → risk-rank → mitigations → re-test on change. Tie each LINDDUN finding back to a GDPR article so the DPIA and threat model stay in lockstep.

**C. Top regulatory obligations (priority order):**
1. **Complete the DPIA before broader rollout** (Art. 35) — it's mandatory and a standalone fineable gap.
2. **Pin the lawful basis** (legitimate interest + balancing; not employee "consent") and **deliver Art. 14 transparency** to ingested-message senders.
3. **Do not infer employee emotion/sentiment** (Art. 5 AI-Act ban) and **don't let outputs drive performance evaluation** (Annex III high-risk line); **label the digest as AI-generated** (Art. 50) and **run AI-literacy training** (Art. 4, already in force).
4. **Implement erasure** (crypto-shred + per-subject keys; deletion propagation/backups-beyond-use; processor-deletion via the gateway DPA).
5. **Enforce minimisation + short justified retention with automated expiry**; **encrypt at rest with separated keys**; document in **ROPA + retention schedule**.
6. **Keep the no-cloud-egress / in-network posture** (satisfies Ch. V transfers + 152-FZ localisation + banking resilience).

---

## 7. Open questions / low-confidence (incl. legal-advice caveat)

- **Legal-advice caveat (highest priority):** none of this is legal advice. The lawful-basis choice, the AI-Act tier, the Art. 9 strategy, and the crypto-shred-as-erasure position all need **DPO + counsel** sign-off for the specific deployment.
- **Lead jurisdiction & regulator.** EU-baseline here; the actual lead supervisory authority, any national DPIA "blacklist" entries, and **152-FZ vs GDPR** precedence depend on where the bank and data subjects sit. *(Low confidence on which regime governs.)*
- **AI-Act tier is fact-specific.** "Limited risk" assumes extraction/summarisation only. Any drift toward evaluation, ranking, productivity scoring, or affect inference changes the answer. *(Medium confidence.)*
- **Crypto-shred = Art. 17 erasure** is **defensible best practice, not settled EU law.** Document the Recital-26 reasoning and the key-hygiene preconditions; consider DPO confirmation. *(Medium / risk-based.)*
- **Digital Omnibus timeline is in flux.** The Annex III high-risk deferral to ~Dec 2027 and the Art. 50 marking grace period are from a **provisional May 2026 agreement** — **re-verify against the enacted text** before relying on dates.
- **Special-category data in free text.** Whether ingested comms trigger Art. 9 in practice (and how to lawfully handle it) needs a real data sample + counsel — minimisation/redaction is the safer default than leaning on an Art. 9(2) condition.
- **Banking sector specifics** (DORA/EBA vs Bank of Russia/GOST) are summarised at a high level only; the bank's compliance function owns the detail. *(Medium confidence.)*

---

## 8. Sources

**GDPR & DPIA**
- GDPR consolidated text (EUR-Lex, CELEX 32016R0679): https://eur-lex.europa.eu/eli/reg/2016/679/oj — Arts. 5, 6, 9, 12–19, 25, 28, 30, 32, 35, 36, 83
- gdpr-info.eu (working locator): https://gdpr-info.eu/art-35-gdpr/ · /art-17-gdpr/ · /art-5-gdpr/ · /art-30-gdpr/
- EDPB/WP29 WP248 rev.01 — DPIA guidelines (nine criteria): https://ec.europa.eu/newsroom/article29/items/611236
- IAPP — "What's subject to a DPIA under the GDPR" (nine criteria, DPA lists): https://iapp.org/news/a/whats-subject-to-a-dpia-under-the-gdpr-edpb-on-draft-lists-of-22-supervisory-authorities
- ICO — When do we need to do a DPIA / Right to erasure / Storage limitation: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/ (accountability-and-governance/data-protection-impact-assessments-dpias/ · individual-rights/right-to-erasure/ · data-protection-principles/.../storage-limitation/)
- Art. 29 WP Opinion 2/2017 on data processing at work; Opinion 05/2014 on Anonymisation (WP216): https://ec.europa.eu/justice/article-29/documentation/opinion-recommendation/files/2014/wp216_en.pdf

**Threat modeling**
- LINDDUN (KU Leuven) — threat types / GO / PRO: https://linddun.org/ · https://linddun.org/threat-types/ · https://linddun.org/go/
- Deng et al. (2011), original LINDDUN paper: https://link.springer.com/article/10.1007/s00766-010-0115-7
- Wuyts et al. (2020), LINDDUN GO: https://conferences.computer.org/eurosp/pdfs/EuroSPW2020-7k9FlVRX4z43j4uE2SeXU0/859700a302/859700a302.pdf
- Microsoft Learn — STRIDE / Threat Modeling Tool: https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats
- OWASP — Threat Modeling Process (STRIDE → property table): https://owasp.org/www-community/Threat_Modeling_Process
- OWASP Top 10 for LLM Applications 2025: https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/

**EU AI Act**
- High-level summary; Annex III; Article 50; Article 4: https://artificialintelligenceact.eu/high-level-summary/ · /annex/3/ · /article/50/ · /article/4/
- Bird & Bird / Hogan Lovells — Art. 50 transparency guidance (2026): https://www.twobirds.com/en/insights/2026/taking-the-eu-ai-act-to-practice-reading-the-commissions-draft-article-50-guidelines

**Encryption / erasure / retention standards**
- NIST SP 800-88 Rev.2 (Media Sanitization — Cryptographic Erase): https://csrc.nist.gov/pubs/sp/800/88/r2/final
- NIST FIPS 197 (AES); SP 800-38D (GCM); SP 800-57 Pt.1 Rev.5 (key management): https://csrc.nist.gov/pubs/fips/197/final · https://csrc.nist.gov/pubs/sp/800/38/d/final · https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-57pt1r5.pdf
- OWASP Cryptographic Storage Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html
- EDPB Guidelines 01/2025 (pseudonymisation) & 02/2025 (blockchain — encrypted data still personal): https://www.edpb.europa.eu/system/files/2025-01/edpb_guidelines_202501_pseudonymisation_en.pdf · https://www.edpb.europa.eu/system/files/2025-04/edpb_guidelines_202502_blockchain_en.pdf
- SQLCipher design (Zetetic): https://www.zetetic.net/sqlcipher/design/
- OpenAI API data retention; Anthropic data retention: https://developers.openai.com/api/docs/guides/your-data · https://privacy.claude.com/en/articles/7996866

**Residency / banking**
- Russia 152-FZ localisation (processors bound since July 2025): https://securiti.ai/russian-federal-law-no-152-fz/ · https://learn.microsoft.com/en-us/compliance/regulatory/offering-russia-data-localization · https://konsugroup.com/en/news/new-requirements-personal-data-protection-russia-2025-07/
