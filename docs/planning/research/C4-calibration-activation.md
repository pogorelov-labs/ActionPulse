# C4 · Safe Fleet-Activation + Calibration — Research Brief

> Research deliverable for **C4 · Safe fleet-activation state machine** (`RESEARCH_SPEC.md` §C4, `[P1 · L · 🟢→🏢]`, ↔A2).
> Question: what is the safe-activation architecture for turning on the dark fleet (reranker / LLM-judge / fused relevance / best-of-N / embedding-merge), calibrated by the reactions flywheel, with a defensible recall floor and a rollback rule?
> Scope note: C4 only **specs** the path; PC-2 + a calibration gate authorize the actual flip. This brief is method research, not a build order.
> Date: 2026-06-20. Confidence tags inline: **[H]** high / **[M]** medium / **[L]** low.

---

## 1. TL;DR

- The industry-standard safety progression is **shadow → canary → flip, guarded by counter/guardrail metrics with a written, automatic rollback rule** [H]. ActionPulse is the *hard* case on the rollout axis: **low traffic + internal tool**, so classic online A/B / sequential-significance gating is underpowered and largely **inapplicable** — the literature itself says to fall back on **offline gold sets + shadow diffs + manual review**, not statistical significance [H].
- The single most important architectural fact: **a reranker/relevance filter can only ever lower recall** (it re-orders/drops; it cannot recover evidence the first stage already surfaced). So the metric to *protect* is **recall**, and the gate is a **recall floor** [H].
- A recall floor is a **binomial proportion over the labeled positives only** — its confidence interval depends on the **number of labeled relevant items, not the corpus size** [H]. Certify the floor with a **Wilson (or Clopper–Pearson) lower bound**, never the Wald/normal approximation (it degenerates to zero width near recall 1.0) [H].
- For a *distribution-free, finite-sample* guarantee that ties a threshold to a recall target, **Conformal Risk Control (CRC)** bounds expected false-negative-rate ≤ α ⇒ expected recall ≥ 1−α; **Learn-then-Test (LTT)** additionally corrects for "I tried many thresholds" [H]. Caveat: CRC controls an **expectation**, not a per-batch high-probability floor [M].
- For the **judge-vs-human** validation, report agreement **with a confidence interval AND alongside per-class precision/recall** — because relevant items are rare, **Cohen's κ will be deflated by the prevalence paradox even at high observed agreement**, so a bare κ is misleading [H].
- The flywheel's reactions are a **weak, biased signal, not ground truth**. The decisive limitation: reactions exist **only on delivered items**, so they can measure **precision but are structurally blind to recall** — the very thing the gate needs. Recall must be measured on a **separately drawn, stratified, human-audited sample**, not on self-selected thumbs [H].

---

## 2. Rollout patterns (shadow → canary → flip + guardrails + rollback)

**Shadow / dark launch** — run the candidate in parallel, serve the incumbent, log+compare the candidate's output; users never exposed. Canonically "the safest test-in-production technique" (Huyen, *Designing ML Systems*) [H]. You compare prediction agreement, latency, errors, and segment-level regressions on the **full real input distribution at zero user risk** [H]. AWS SageMaker shadow tests expose exactly this (configure traffic %, comparison metrics, duration) but **deliberately prescribe no pass/fail threshold** — read that as *"no universal threshold exists,"* not an omission [H]. Cost is the catch: shadow doubles inference per request [H].

**Canary / progressive ramp** — staged exposure (a common heuristic: 1% → 10% → 50% → 100%), advancing on metric checks; a persistent **holdback** measures long-run effects [M]. Ramp *speed* is an explicit risk/throughput **dial, not a best practice** — aggressive (1%→25%→100%) ships faster but misses subtle regressions; conservative catches more, costs more [H]. Google Cloud Deploy and others pointedly refuse to recommend specific percentages [H].

**Guardrail / counter-metrics** (Kohavi/Tang/Xu, *Trustworthy Online Controlled Experiments*) — metrics you "don't want to degrade but won't necessarily improve"; if the treatment moves a guardrail, **reduce trust or stop** [H]. Microsoft ExP alerts on **guardrails + OEC**, fires on a large move **and/or** p-value below threshold, and uses **equivalence bounds** (won't alert on a 2 ms regression, will alert on 0.1% of a key reliability metric); a Bing test that introduced a 404 bug was **auto-killed** when guardrails tripped [H].

**Automatic rollback triggers** — three families in use: **SLO-based** (e.g. `5xx > 1%`, p99 breach), **statistical-significance-based** (sequential p-value breach), **guardrail-breach-based** (degradation vs control-*now*). Implemented as circuit breakers (Argo Rollouts + Prometheus) or **feature-flag kill switches** (instant, faster than re-deploy) [H]. The sharpest neutral warning ("canary metrics lie"): non-representative traffic, **averages hide tail regressions**, and **HTTP-200-but-wrong** responses ("errors: none, conversion: down"); fix with `min_requests` floors, **canary-vs-control-now (never vs yesterday)**, and **deterministic written rollback criteria** [H].

**LLM-specific (2024–2026)** — the production loop is *prompt change → eval gate → traced traffic → regression detection*; "**eval regressions block deploys; without gates, eval is decoration**" [H]. Two LLM-specific guardrails matter here: **pin the exact model version for both the agent and the judge** so silent provider drift trips the eval instead of being absorbed [H]; and **treat the LLM-judge as an instrument you must validate against humans** — "LLM-judge alone is unreliable; with human calibration for borderline cases, it scales" [H]. Gap worth flagging: most LLM-CI vendor posts **don't address run-to-run non-determinism statistically** (multi-sample variance thresholds) — they lean on rubric precision [M].

**Why most of this needs adapting for ActionPulse:** online significance gating *requires volume*. Always-valid p-values get unreliable with imbalanced buckets (Wish caps usage at ≤5:1 treatment:control) and small n yields "silly estimates from noise" [H]. **For an internal, low-traffic digest the realistic stack is: offline gold set + shadow diff + SLO/error gates + manual spot-check** — exactly what the small-team eval literature prescribes [H].

---

## 3. Calibration methods (named, with when-to-use)

**Pick the operating point.** Each threshold is one point `(recall, precision)` on the PR curve; lowering it raises recall at precision's expense [H]. To honor "recall ≥ target," do the dual of **recall-at-fixed-precision**: fix recall ≥ target and take the **lowest threshold meeting it** (maximizing precision subject to the recall constraint) [H]. Use **F-β** only when you want a *soft* scalar trade-off; use the hard constraint when recall is an SLA [H]. Prefer **PR curves over ROC under class imbalance** (ROC flatters when negatives dominate) [M].

**Certify the recall floor — the core math.** Recall is a **binomial proportion over the positives only**; CI width is governed by the **count of labeled relevant items R**, not corpus size N [H]. So a 10k-item set with 12 positives gives the same recall CI as a 12-positive set.
- **Wilson score interval** — asymmetric, well-calibrated near p→1 and at small n; **the default** [H].
- **Clopper–Pearson (exact)** — guarantees coverage ≥ nominal but **over-covers (wider than needed)**; use when you must *guarantee* non-undercoverage [H].
- **Do NOT use Wald/normal-approx** — coverage collapses near 0/1 and it **degenerates to zero width when observed recall is exactly 1.0** (false certainty), the exact regime of a recall floor [H]. (Canonical reference: Brown–Cai–DasGupta 2001; Webber 2010 shows recall's CI depends on the relevant count and recommends a beta-binomial Bayesian posterior.)
- **Intuition for "recall ≥ 0.8 @ 95%":** you certify the **lower** CI bound, so you need enough *positives* that the Wilson/CP lower bound clears 0.8 — order ~30–40 mostly-recalled positives for an ~0.8 floor; **dozens-to-100+** for a 0.9 floor. With single-digit positives **no** method certifies a tight floor; the only fix is **more labeled positives** [M/H].

**Bootstrap CIs** — **percentile** (resample the eval set ≥1–2k times, take 2.5/97.5 percentiles) or, better near recall 1.0, **BCa** (bias-corrected & accelerated; corrects the skew a bounded-at-1 metric always has) [H]. Pitfall: bootstrap **inherits small-N** — replicates can contain ~0 positives → undefined/wild recall; use **stratified** resampling or fall back to analytic Wilson/CP when positives are scarce [H].

**Conformal prediction / risk control** — the tool that ties a threshold to a *proven* recall target, distribution-free and finite-sample:
- **Split/inductive conformal** gives marginal coverage `P(Y∈C(X)) ≥ 1−α` from an exchangeable calibration set; only assumption is **exchangeability** [H].
- **Conformal Risk Control (CRC, Angelopoulos et al. 2022)** extends this to **any bounded, monotone loss**: pick `λ̂ = inf{λ : (n/(n+1))·R̂_n(λ) + B/(n+1) ≤ α}`, guaranteeing `E[L] ≤ α`. With loss = fraction of true positives missed (**FNR**, a *worked example* in the paper), this **certifies expected recall ≥ 1−α** — verified: the guarantee is on the **expectation**, tight up to O(1/n) [H]. **Caveat:** "expected recall ≥ 0.8" ≠ "recall ≥ 0.8 on *this* batch w.p. 95%"; for the latter use the Wilson/CP lower bound or LTT [M].
- **Learn-then-Test (LTT, AOAS 2025)** frames threshold choice as **multiple hypothesis testing** under family-wise-error control — it handles non-monotone risk **and bakes in the multiple-threshold correction**, solving §3-calibration and the winner's-curse (below) at once [H].
- Maturity flag: Wilson/CP/bootstrap are decades-tested; **CRC (2022)/LTT (2021/2025) are newer**, rigorous but assume genuine **exchangeability (breaks under drift)** [M].

**Calibrating scores with limited labels** — **Platt scaling** / **temperature scaling** (1-ish parameter → data-efficient, safe on small sets); **isotonic regression is more accurate but data-hungry and overfits small sets** — avoid below a few hundred points [H]. To stretch a tiny label budget for rare positives: **score-stratified / active importance sampling** (oversample likely-positives) cuts estimator variance markedly — but you **must reweight** (Horvitz–Thompson), or the recall estimate is biased upward [H].

**Winner's curse / multiple testing** — scanning many thresholds and reporting the best on one small set yields an **optimistically biased** estimate; the picked threshold underperforms later (F1-tuning is especially prone) [H]. Rule: **never tune the threshold on training data, and confirm the chosen point on a held-out set** (scikit-learn's `TunedThresholdClassifierCV` enforces CV for exactly this) [H]. Principled alternatives: a fresh **confirmation set**, the **reusable-holdout** (Dwork et al.), or **LTT** (correction built in) [M/H].

---

## 4. Agreement & implicit-feedback pitfalls

**Inter-rater agreement (judge vs human).**
- **Cohen's κ = (p₀−pₑ)/(1−pₑ)**; range −1…1 [H]. The **Landis–Koch** scale (0.41–0.60 moderate, 0.61–0.80 substantial, 0.81–1.0 almost perfect) is the usual source of the "κ ≥ 0.4 = minimally acceptable" bar — but verified to be **explicitly arbitrary**: the authors "supplied no evidence… basing it instead on personal opinion… these guidelines may be more harmful than helpful." Competing scales conflict (McHugh 2012 calls κ<0.60 inadequate) [H]. **So κ ≥ 0.4 is a *chosen convention*; state it as such and justify the bar for the domain.**
- **The prevalence paradox is the decisive pitfall for ActionPulse** (relevant items are rare): verified that two tables with identical **60% observed agreement** give κ = **0.13 vs 0.26** purely from marginal skew, and κ "underestimates agreement on the rare category" → "overly conservative" [H]. Mitigations: **PABAK** (prevalence/bias-adjusted κ), **Gwet's AC1** (robust to prevalence; but a 2023 paper warns it's *not* a drop-in κ substitute [M]), and — most useful — **report per-class precision/recall, not a single agreement scalar** [H].
- **Krippendorff's α** when ≥2 raters / ordinal / missing data; cutoffs **α ≥ 0.80 rely, 0.667–0.80 tentative, <0.667 discard** — stricter and more general than κ [H]. **Always report a CI on κ/α**; both are unstable at small n (≤~50), and skewed marginals **roughly double** the n needed [H].
- **LLM-judge specifics:** verified GPT-4-class judges hit **">80% agreement, the same level as humans"** (Zheng et al., MT-Bench) [H] — but with named biases: **position, verbosity, self-enhancement/self-preference, weak math/logic reasoning** [H]. Mitigate: **swap answer order and count only order-consistent verdicts**, prefer concise-neutral and **binary** decisions over 1–5 Likert (practitioners reach >90% expert agreement in ~3 prompt iterations) [H]. **Treat the judge as a new annotator you must validate and re-validate** [H].

**Implicit feedback / reactions (the flywheel signal).**
- Thumbs are **weak, biased labels**: extreme **sparsity** (explicit signals can be ~0.3% of interactions), **self-selection** (only strong-opinion users react; rate liked items more than disliked), and **ambiguity** (a 👎 conflates wrong / irrelevant / bad tone / mistimed) [H]. RLHF work confirms human preference labels are noisy even when deliberate (annotator agreement **63–77%**) [H].
- **Missing-Not-At-Random / positive-unlabeled:** "no reaction" ≠ "not relevant" — you only get feedback on what you *showed* (exposure/selection bias) [H]. The standard debias is **Inverse Propensity Scoring (IPS)** weighting by exposure — but IPS is **high-variance/fragile** and, crucially, was proven at consumer scale; **its prescriptions don't transfer to tens–hundreds of events** [H].
- **Feedback loops ("rich get richer"):** calibrating thresholds on reactions tightens the filter toward what already gets reactions → it stops surfacing under-reacted-but-important classes and the "gold" set **drifts to confirm the current policy** [H].
- **Engagement ≠ utility (Goodhart):** optimizing reactions optimizes a *proxy*. A perfectly-surfaced urgent item may be acted on and **never reacted to** — a great digest can earn *fewer* reactions [H/L].
- **Low-volume regime:** classic A/B is underpowered; **flat-prior Bayesian tests are actively dangerous** (false "97.5% to beat control" early). The remedy is **informative priors / Bayesian shrinkage**, **pool/aggregate across users·sections·time**, prefer **simple heuristics over learned rankers/bandits**, and **never act on a single item's 👎** [H]. (Bandits — ε-greedy / Thompson / LinUCB — need volume and a clean reward; not appropriate at this scale yet [H].)
- **Reactions as gold labels — the headline hazard: survivorship.** Reactions exist only on **delivered** items, so they measure **precision but are structurally blind to recall** (you can't see the relevant items the filter dropped) [H]. **Combat:** treat reactions as one weak signal; **estimate recall only via a periodic uniform/stratified random sample sent for human audit**; collect **structured reasons** to disambiguate 👎; **don't optimize the reaction directly** [H].

---

## 5. Recommended state machine for ActionPulse

A six-state machine per dark feature (reranker / judge / fused-relevance / best-of-N / embedding-merge), each independently flagged. Maps onto the C4 protocol (spec machine + calibration store; dry-run flip offline; define rollback). **PC-2 is a hard precondition on every CALIBRATE→ARMED transition** (it gates the dark fleet regardless).

```
        reactions flywheel (precision signal)  +  periodic random-sample human audit (recall signal)
                                   │                              │
                                   ▼                              ▼
 DARK ──► SHADOW ──► CALIBRATE ──► ARMED ──► CANARY ──► LIVE
   ▲         │           │ (PC-2)    │          │         │
   └─────────┴───────────┴──────────┴──────────┴─────────┘  rollback = flip flag back one state (instant)
```

1. **DARK** — feature off (today's state). Baseline = current pipeline output.
2. **SHADOW** — run the candidate in parallel on **every** run; log candidate vs incumbent **item sets** + retrieved-context sets; serve incumbent only. Watch latency / extra LLM-call budget (ADR-008: ≤2 calls/run) and prediction-agreement. **Exit gate:** stable, no infra regression, candidate diff is sane. *Pattern: shadow diff is the low-traffic substitute for A/B* [H].
3. **CALIBRATE** — on a **held-out human-labeled gold set** (the flywheel harvest is the seed; **top up with a stratified random audit so positives aren't only delivered items**): (a) pick the operating threshold as **recall-constrained → lowest threshold meeting target**; (b) **certify the recall floor** with a **Wilson/Clopper–Pearson lower bound over the labeled positives** (and/or **CRC on FNR** for an expected-recall certificate); (c) if many thresholds were tried, **confirm on a fresh split or use LTT**; (d) for the *judge* feature, validate **judge-vs-human** = agreement-with-CI **plus per-class precision/recall** (κ alone is paradox-prone here). **Exit gate (the recall-floor gate):** certified recall lower bound ≥ floor (start **0.8** as a *convention*, set the real number from the gold-set analysis) **AND** judge κ ≥ a stated, justified bar (e.g. ≥0.6, with CI) **AND** PC-2 cleared. *This is the defensible "reactions → recall floor → flip" bridge.*
4. **ARMED** — flip enabled behind a flag for a **holdback-excluded** slice; written rollback criteria committed before exposure.
5. **CANARY** — expose to a small slice / subset of sections; compare **candidate-vs-incumbent on the same inputs now** (never vs yesterday). At this traffic, **rely on SLO/error gates + guardrails + manual spot-check, not significance** [H].
6. **LIVE** — full flip; keep a **permanent holdback** + scheduled re-eval.

**Guardrail metrics (monitored every state ≥ SHADOW):** certified **recall lower bound** (primary, must stay ≥ floor); item-count delta vs incumbent (catches over-filtering); judge–human agreement on the rolling audit; latency / LLM-call count / cost; citation-validity (P2 must not regress). **Counter-metric discipline:** any guardrail breach **reduces trust / halts**, per Kohavi.

**Rollback rule (deterministic, written, automatic):** flip the feature flag **back one state** (instant kill switch) if **any** of: certified recall lower bound drops below floor on the latest audit; item-count collapses beyond an equivalence bound; judge–human agreement falls under its bar; citation-validity / SLO breach. Re-entry requires re-passing the CALIBRATE gate. *Kill-switch over re-deploy because it's instant* [H].

**Recalibration cadence:** judges/thresholds drift (~60–90 days reported); **re-run CALIBRATE on a refreshed gold set monthly–quarterly**, alert when agreement κ or recall LB crosses its bar [M]. Each human-audited item and each corrected production trace becomes a **permanent eval case**.

**Explicitly do NOT (yet):** learn a ranker/bandit from reactions, or compute the recall number from self-selected thumbs — survivorship makes that unmeasurable; or run online significance gating — traffic is too low [H].

---

## 6. Open questions / low-confidence

- **Recall floor value & required #positives** — 0.8 is a *convention* (single-source for RAG context-recall), not derived; the real floor and the number of labeled positives needed to certify it must come from ActionPulse's own gold set [M]. With single-digit positives per audit, **no method certifies a tight floor** — does the flywheel + audit produce enough positives per cycle? **Open** [H that it's a risk].
- **Expectation vs per-batch guarantee** — CRC certifies *expected* recall ≥ 1−α; whether that suffices for a privacy-/trust-sensitive "we don't silently drop your urgent item" promise, vs a per-batch Wilson lower bound, is a **product/threat-model call** [M], ties to C10/PC-2.
- **κ bar for the judge** — 0.4 vs 0.6 is genuinely unsettled in the literature; given the prevalence paradox the bar should arguably be set on **PABAK/AC1 + per-class recall**, not raw κ — needs a decision [M].
- **Non-determinism of the LLM judge/best-of-N** — sources don't settle how to gate run-to-run variance statistically; **multi-sample variance thresholds** are an untested gap for our harness [M/L].
- **Does the reranker even help here?** Neutral benchmarks show reranker gains are **slice-dependent and can vanish/invert when first-stage recall is already high** — SHADOW should answer "for which slices, by how much," not assume a win [M].
- **Audit sampling cost** — the recall-blind-spot fix (periodic human-audited random sample) has real labeling cost at low volume; cadence/size is an **open budget question** [M].
- **Exchangeability under drift** — conformal guarantees assume exchangeability; corp-comms distribution shifts (reorg, quarter-end) may break it — **untested** [M].

---

## 7. Sources

Rollout / experimentation
- Microsoft Research — Patterns of Trustworthy Experimentation (guardrails+OEC, equivalence bounds, auto-shutdown): https://www.microsoft.com/en-us/research/group/experimentation-platform-exp/articles/patterns-of-trustworthy-experimentation-during-experiment-stage/
- Kohavi/Tang/Xu, *Trustworthy Online Controlled Experiments* (guardrail/counter-metrics — book summary): https://medium.com/@arpita.k20/book-summary-trustworthy-online-controlled-experiments-4910812a9860
- AWS SageMaker — Monitor a shadow test (shadow metrics; no prescribed threshold): https://docs.aws.amazon.com/sagemaker/latest/dg/shadow-tests-view-monitor-edit-dashboard.html
- Statsig — Shadow deployment (same-input dual-run, not A/B; for teams without A/B resources): https://www.statsig.com/perspectives/shadow-deployment-comparison
- Statsig — Sequential testing / always-valid p-values (mSPRT; decision-vs-measurement; per-metric power): https://docs.statsig.com/experiments/advanced-setup/sequential-testing
- "Canary Metrics Lie More Than You Think" (tail-latency, 200-masks-failure, canary-vs-control-now): https://medium.com/@Nexumo_/canary-metrics-lie-more-than-you-think-d95db27de236
- "Building an Internal LLM Eval Harness" 2026 (eval regressions block deploys; pin judge+agent versions; 5% LLM canary): https://logiciel.io/blog/llm-eval-harness-internal-build-2026
- The Pragmatic Engineer — evaluation-driven development for small teams (offline gold set + assertion evals): https://newsletter.pragmaticengineer.com/ (eval-driven dev guidance)

Calibration / recall floor / conformal
- Brown, Cai & DasGupta 2001 (binomial-interval comparison) via TDS summary: https://towardsdatascience.com/five-confidence-intervals-for-proportions-that-you-should-know-about-7ff5484c024f/
- Binomial proportion CIs — Wikipedia (Wald zero-width near 0/1; Wilson asymmetric/safe; Clopper–Pearson exact/conservative): https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval
- Webber 2010, "Approximate Recall Confidence Intervals" (recall CI depends on relevant count; beta-binomial): https://arxiv.org/abs/1202.2880
- Angelopoulos & Bates, "A Gentle Introduction to Conformal Prediction": https://arxiv.org/abs/2107.07511
- Angelopoulos et al. 2022, "Conformal Risk Control" (E[loss]≤α; FNR worked example; verified expectation-only, O(1/n)): https://arxiv.org/abs/2208.02814
- Angelopoulos et al., "Learn then Test" (AOAS 2025; thresholds as multiple testing under FWER): https://arxiv.org/abs/2110.01052
- scikit-learn — Tuning the decision threshold (`TunedThresholdClassifierCV`; never tune on training data): https://scikit-learn.org/stable/modules/classification_threshold.html
- TorchMetrics — Recall At Fixed Precision (constrained operating-point metric): https://lightning.ai/docs/torchmetrics/stable/classification/recall_at_fixed_precision.html
- "Thresholding Classifiers to Maximize F1" (winner's-curse/optimism in empirical thresholds): https://arxiv.org/pdf/1402.1892

Agreement (judge vs human)
- Cohen's kappa — Wikipedia (Landis-Koch "arbitrary… may be more harmful than helpful"; verified prevalence paradox 0.13 vs 0.26): https://en.wikipedia.org/wiki/Cohen%27s_kappa
- McHugh 2012, "Interrater reliability: the kappa statistic" (stricter bars; κ<0.60 inadequate): https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/
- Feinstein & Cicchetti 1990, "High agreement but low kappa: two paradoxes": https://pubmed.ncbi.nlm.nih.gov/2348207/
- Krippendorff's alpha — methodological notes (0.80/0.667 cutoffs): https://www.k-alpha.org/methodological-notes
- Zheng et al. 2023, "Judging LLM-as-a-Judge with MT-Bench" (verified ">80% agreement, same level as humans"; position/verbosity/self-enhancement biases): https://arxiv.org/abs/2306.05685
- Hamel Husain, "Creating an LLM-as-a-Judge That Drives Business Results" (align-to-expert; binary>Likert; raw agreement misleading under imbalance → precision/recall): https://hamel.dev/blog/posts/llm-judge/
- Evidently AI — LLM-as-a-judge guide (validate judge as a classifier vs ground truth): https://www.evidentlyai.com/llm-guide/llm-as-a-judge

Implicit feedback / recsys bias
- Joachims et al. 2017, "Unbiased Learning-to-Rank with Biased Feedback" (position bias; IPS): https://www.cs.cornell.edu/people/tj/publications/joachims_etal_17a.pdf
- Saito et al. 2019, "Unbiased Recommender Learning from MNAR Implicit Feedback" (MNAR / positive-unlabeled / exposure bias): https://arxiv.org/abs/1909.03601
- Chen et al., "Bias and Debias in Recommender Systems: A Survey" (feedback-loop amplification): https://arxiv.org/pdf/2010.03240
- Casper et al. 2023, "Open Problems and Fundamental Limitations of RLHF" (noisy prefs; 63–77% annotator agreement; comparison feedback can't express intensity): https://arxiv.org/abs/2307.15217
- "System-2 Recommenders," FAccT'24 (engagement ≠ utility): https://arxiv.org/pdf/2406.01611
- Herlocker et al., "Evaluating Collaborative Filtering Recommender Systems" (recall can't see un-surfaced/unrated items — survivorship): https://grouplens.org/site-content/uploads/evaluating-TOIS-20041.pdf
- Eppo — "Hidden Dangers of Non-Informative Priors in Bayesian A/B Testing" (low-n false confidence; shrinkage): https://www.geteppo.com/blog/hidden-dangers-non-informative-priors-bayesian-ab-testing
- Microsoft Data Science — "Beyond thumbs up and thumbs down" (thumbs are weak/ambiguous; add structured reasons + human review): https://medium.com/data-science-at-microsoft/beyond-thumbs-up-and-thumbs-down-a-human-centered-approach-to-evaluation-design-for-llm-products-d2df5c821da5

RAG / reranker recall (applied)
- Pinecone — "Rerankers and Two-Stage Retrieval" ("retrieve more, return less"; high-recall stage 1): https://www.pinecone.io/learn/series/rag/rerankers/
- Liu et al. 2023, "Lost in the Middle" (U-shaped context-position effect — academic justification for reranking down): https://arxiv.org/abs/2307.03172
- Redis — RAG evaluation guide (context recall as "silent regression"; 0.8 floor convention): https://redis.io/blog/rag-system-evaluation/
- "From BM25 to Corrective RAG" (measured: too-small candidate pool starves recall — Recall@5 0.458 @ 20 candidates): https://arxiv.org/html/2604.01733v1

> Verification note: CRC (expectation-only, FNR example, O(1/n)), the binomial Wald-zero-width / Wilson / Clopper–Pearson facts, MT-Bench ">80%, same as humans," and the Landis-Koch "arbitrary" + prevalence-paradox (0.13 vs 0.26) claims were each re-fetched and confirmed against primary sources. The GroupLens PDF would not parse on re-fetch; its survivorship/recall claim is corroborated by Saito et al. 2019 and Chen et al.'s survey, so it stands on multiple sources. Several arXiv PDFs returned garbled bodies to the fetcher → those rely on abstract/HTML mirrors (confidence tagged accordingly).
