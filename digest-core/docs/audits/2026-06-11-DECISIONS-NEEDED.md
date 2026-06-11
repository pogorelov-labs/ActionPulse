# ActionPulse — Decisions Needed (enriched options)

Companion to [`2026-06-11-frontier-audit.md`](./2026-06-11-frontier-audit.md). These are the calls an
agent must **not** make unilaterally — each changes live behavior, expands a privacy/compliance surface,
or reconciles a conflict with an existing ADR. Each decision below carries **pre-researched options with
pros/cons, a marked recommendation, and citations**, so the owner can decide quickly. The recommendation
is a suggestion, not a default — pick deliberately.

Legend: 🔬 = research-grounded · ⚖️ = ADR/decision conflict · 🔒 = privacy/compliance surface · 🌐 = needs corp validation.

---

## D1 — Citation gate: enforcement mode 🔬🔒 (F4, blocks real P2)

**Decision:** keep the citation/evidence gate in shadow, or make it enforce — and if so, drop vs repair?
**Why it's yours:** P2 traceability is the product's stated soul, but enforcing can drop legitimate
items if the verifier is imperfect (it checks offset+SHA *fidelity*, not semantic correctness). Recall vs
guarantee is a product call.
**Current state:** shadow-only — annotates `weak_evidence`, never drops (`citation_gate.py:54-82`,
`run.py:583-591`). A non-generative `repair.py` (substring-only re-selection of an existing span,
judge-gated) already exists but isn't wired into the gate.

**Options**
- **(a) Keep shadow.** *Pros:* zero false-drop risk; observability of gate behavior. *Cons:* P2 is modeled
  but **not enforced** — unsupported/injected text still ships; the headline F4 risk stays open.
- **(b) Enforce-drop.** Drop items that fail verification. *Pros:* hard P2 guarantee; strongest injection
  containment. *Cons:* the fidelity-only verifier may drop legitimate items → recall loss.
- **(c) Enforce-repair, then drop.** Try `repair.py` to re-anchor a failing citation to a verbatim span;
  drop only if repair fails. *Pros:* keeps recall while enforcing fidelity; reuses existing machinery;
  matches the production pattern where unsupported sentences are "stripped **or rewritten**." *Cons:* more
  moving parts; a bad re-anchor could mis-attribute.
- **(d) Staged rollout (recommended).** Shadow → measure drop-rate → enable repair → enable drop, each
  flag-gated on the measured false-drop rate from shadow data.

**Recommendation:** **(d) → landing on (c).** Use the shadow data already being collected to measure the
real drop rate, enable repair once it's low, then enforce-drop for the residual. This is the
groundedness-guardrail norm (a faithfulness check before ship; unsupported sentences stripped or
rewritten) applied incrementally. Behind a flag; replay/eval must exercise both gate states.
**Sources:** [RAG faithfulness/guardrails (FutureAGI)](https://futureagi.com/glossary/faithfulness/) ·
[AI guardrails (Coralogix)](https://coralogix.com/ai-blog/ai-guardrails/) · frontier-bar F4/F9.

---

## D2 — Judge architecture 🔬 (F3, blocks a trustworthy eval gate)

**Decision:** what replaces the single-call **pointwise rubric** judge (a 2026 research pass refuted it as
"best-aligned")?
**Why it's yours:** the options trade cost/latency against human-agreement and bias-robustness; the right
mix depends on how the judge is used (daily signal vs release gate).
**Current state:** one LLM call, pointwise 0–1 rubric (`judge.py:15-44`); reports P/R but no κ/α; gate
inert (`recall_floor=0.0`).

**Options**
- **(a) Keep pointwise + calibrate.** Add κ/α drift tracking (via bundled `scripts/agreement.py`), fix the
  floor. *Pros:* cheap O(N), no rewrite. *Cons:* scores drift between runs; more vulnerable to adversarial
  outputs; the refuted "best-aligned" pattern.
- **(b) Reference-anchored pointwise.** Score the draft against a gold/reference where gold exists.
  *Pros:* better alignment than bare pointwise; still O(N); reuses the existing gold set. *Cons:* only
  where gold exists; error propagation if the reference is weak.
- **(c) Pairwise for gate decisions.** Compare candidate vs reference/prior version. *Pros:* grounds each
  response in the other → **best human agreement** (RewardBench 90.5 vs 88.0). *Cons:* **O(N²)** to rank;
  position bias (run both orders); more biased toward verbosity/tone; higher cost/latency.
- **(d) Multi-judge ensemble.** *Pros:* robustness/debiasing. *Cons:* most expensive; complex.

**Recommendation:** **tiered — (b) for the daily/dashboard signal, (c) for release-gate decisions only**
(where the candidate set is tiny, so O(N²) is bounded), with both-orders position-bias control. This is
the established production split (pointwise for dashboards, pairwise for gates, reference-anchored when
gold exists). κ/α remain **drift trackers**; the gate is pairwise + precision/recall.
**Sources:** [pairwise vs pointwise trade-offs (Spheron)](https://www.spheron.network/blog/llm-as-judge-evaluation-pipeline-gpu-cloud/) ·
[hamel.dev evals](https://hamel.dev/blog/posts/evals/) · position bias [arXiv 2602.02219](https://arxiv.org/html/2602.02219) ·
debiasing [arXiv 2508.09724](https://arxiv.org/pdf/2508.09724) · frontier-bar F3.

---

## D3 — Eval gate: threshold + hard vs advisory 🔬 (F3, depends on D2)

**Decision:** what does the CI eval gate block on, and when does it go from advisory to hard?
**Why it's yours:** a hard gate that's mis-set either blocks good releases or waves through regressions;
the floor is a risk-appetite call.
**Current state:** `recall_floor=0.0` → gate inert; eval is a manual CLI, never in CI.

**Options**
- **(a) Stay advisory (floor 0.0).** *Pros:* never blocks. *Cons:* gate is theater.
- **(b) Hard gate at a baseline-derived floor now.** *Pros:* immediate regression protection. *Cons:*
  gates on a judge not yet shown to agree with humans → false blocks.
- **(c) Advisory until the judge clears κ ≥ 0.41 vs human labels, then hard at `baseline − margin`
  (recommended).** *Pros:* principled — **don't gate on an unaligned judge**; becomes real once trustworthy.
  *Cons:* requires the calibration loop (D2) first.

**Recommendation:** **(c).** Run `agreement.py` against a human-labelled sample; keep the gate advisory
until κ ≥ 0.41 (Landis–Koch "moderate"), then set `recall_floor` from the gold-set baseline minus a small
margin and block CI on regression. **Sources:** [hamel.dev evals](https://hamel.dev/blog/posts/evals/) ·
frontier-bar F3 (the no-gate-below-moderate rule). **Depends on:** D2.

---

## D4 — Cross-run memory + retention TTL 🔒 (F8, product vs privacy)

**Decision:** add cross-run memory to stop resurfacing items — and if so, what retention, and is feedback
memory in scope?
**Why it's yours:** any retained product state is a new privacy surface in a bank environment; "how long
to remember" is a compliance call, not an engineering default.
**Current state:** none — the daily product re-derives everything from the current window
(`run.py:863-941`); a multi-day action can resurface. Today's privacy posture is "remember nothing."

**Options**
- **(a) No memory (status quo).** *Pros:* strongest privacy posture; simplest. *Cons:* resurfacing bug;
  no continuity/feedback memory (the product gap).
- **(b) Minimal dedup ledger, short TTL (recommended).** Store only `{content-hash → first-delivered
  date}`, no payload; expire after a configurable TTL (default ~14 days). *Pros:* fixes resurfacing with
  the smallest footprint; **TTL is the privacy-decay mechanism** (forgetting-as-a-feature). *Cons:* a
  genuinely recurring item past TTL can reappear (acceptable).
- **(c) Richer memory (thread continuity + feedback).** Longer retention, more state. *Pros:* best product
  experience; mechanizes the feedback flywheel. *Cons:* larger privacy surface; needs a signed retention
  policy.

**Recommendation:** **(b) now; (c) only after a retention-policy sign-off.** Hash-keyed, no payload stored,
TTL configurable. **Owner must set:** the TTL value, and whether feedback memory (c) is in scope.
**Sources:** [mem0](https://github.com/mem0ai/mem0) · [generative agents (arXiv 2304.03442)](https://arxiv.org/abs/2304.03442) ·
frontier-bar F8 (TTL-as-privacy).

---

## D5 — Best-of-N investment & scope 🔬🌐 (F9, biggest quality upside)

**Decision:** invest in test-time sampling, and at what scope, given the 15 RPM cap?
**Why it's yours:** it trades determinism + latency (under a hard rate cap) for quality, and only pays
where a verifier selects — a cost/quality call that needs corp timing to size.
**Current state:** N=1, `temperature=0.0` (`gateway.py:140-170`). The citation gate is a real **partial**
verifier (fidelity, not semantics).

**Options**
- **(a) Don't invest.** *Pros:* simplest; fully deterministic. *Cons:* leaves the biggest measurable lever
  unused; tokens are unmetered.
- **(b) Best-of-N on the citation dimension only (recommended first step).** Sample the extractor N×, keep
  the candidate maximizing offset-verifiable `support_recall`. *Pros:* exploits unmetered tokens + a hard
  verifier — **the regime where best-of-N provably pays**. *Cons:* 15 RPM bounds N (latency); improves only
  the citation dimension; sampling breaks bit-determinism (flag it; replay forces N=1).
- **(c) (b) + a semantic verifier (entailment/coverage).** *Pros:* extends payoff beyond citations.
  *Cons:* needs a deterministic-enough semantic verifier (build/research); more cost.

**Recommendation:** **(b) as a flag-gated corp experiment** — measure the feasible N under 15 RPM and the
`support_recall` lift before committing; pursue **(c)** only if (b) shows lift and a hard semantic verifier
proves feasible. **Sources:** [Large Language Monkeys (arXiv 2407.21787)](https://arxiv.org/abs/2407.21787)
(coverage pays *with a verifier*) · [self-consistency (arXiv 2203.11171)](https://arxiv.org/abs/2203.11171) ·
frontier-bar F9. **Needs corp:** N timing under the rate cap.

---

## D6 — `max_retries` (corp set 5) vs ADR-008 call budget ⚖️ (F5 reconciliation)

**Decision:** reconcile the corp-applied `max_retries=5` with ADR-008 (max 2 LLM calls/run: 1 primary +
1 quality retry, each with 1 internal transient retry).
**Why it's yours:** it looks like an ADR violation but may be a different axis (transient-HTTP retries vs
call budget); resolving it sets a real reliability vs ADR-fidelity stance.
**Current state:** corp validation raised retries to 5 for transient stability; ADR-008 specifies the
call budget. The code's `max_retries` semantics must be confirmed first.

**Options**
- **(a) `max_retries` *is* the transient-HTTP retry, not the call budget.** Keep the 2-call budget; allow
  more transient retries. *Pros:* no real ADR conflict (orthogonal axes); resilience on a flaky gateway.
  *Cons:* more wall-clock on a bad gateway; must confirm the code path.
- **(b) Honor ADR-008's "1 internal retry" literally.** *Pros:* tight, fast-fail. *Cons:* corp experience
  showed transient errors needed more.
- **(c) Split the concepts explicitly (recommended):** separate `transient_retries` (HTTP, default ~3)
  from `call_budget` (ADR-008, =2) in config. *Pros:* removes the ambiguity that created the apparent
  conflict. *Cons:* small config/code change + an ADR-008 note.

**Recommendation:** **(c)** — verify the current `max_retries` semantics, then split the two so the
2-call budget is explicit and transient retries are tuned from corp experience (~3, not 5). **Depends on:**
confirming code semantics first (`gateway.py` retry path).

---

## D7 — LLM response caching (corp REC-004) vs determinism (PR1) ⚖️

**Decision:** add response caching to save the scarce RPM, or preserve PR1's determinism stance?
**Why it's yours:** caching and determinism only conflict if done naively; the safe design is a real
choice with an invalidation cost.
**Current state:** no cache; identical evidence sets re-call the LLM. The gateway meters *requests* (RPM is
the scarce resource), not tokens.

**Options**
- **(a) No cache (status quo).** *Pros:* preserves determinism/replay simplicity. *Cons:* wastes RPM on
  identical inputs.
- **(b) Content-addressed cache** keyed by (exact normalized evidence + prompt version + model id +
  params). *Pros:* **deterministic by construction** (same key → same value), saves RPM/latency on repeats.
  *Cons:* invalidation complexity; must be bypassed in replay/eval to avoid masking changes.
- **(c) (b) but live-mode only, never in replay/eval (recommended).** *Pros:* keeps eval/replay honest
  while saving live RPM. *Cons:* two code paths.

**Recommendation:** **(b)+(c)** — a content-addressed cache, auto-bypassed in replay/eval. Deterministic by
construction, so it does **not** conflict with PR1. Only worth building if identical evidence sets
actually recur in practice (measure first). **Sources:** PR1 determinism (ActionPulse); RPM scarcity
(gateway meters requests). 

---

## How to use this file

- Resolve in priority order: **D1 and D2 are the highest-stakes** (P2 enforcement; trustworthy evals).
  D3 depends on D2; D6 needs a quick code-semantics check first.
- Each resolved decision unblocks the corresponding `enhancement-program` wave item. Record the choice +
  rationale inline here (turn the recommendation into a decision), then the implementing PR cites this file.
- New decisions surfaced later **must follow this same enriched format** (options + pros/cons + citation +
  recommendation) — a bare "TBD" is not an acceptable decision item.
