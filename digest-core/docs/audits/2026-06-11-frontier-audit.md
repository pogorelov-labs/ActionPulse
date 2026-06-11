# ActionPulse — Frontier-Bar Audit (2026-06-11)

**Method:** produced by the `quality-loop` Claude Code plugin's `frontier-audit` skill
(`/Users/ruslan/msc_1/git/agent-plugins`), run as a dogfood against ActionPulse. Six **read-only**
auditor subagents graded `digest-core/src/digest_core/` (current working tree, branch
`feat/llm-output-cap`) against the versioned 12-dimension **frontier bar**, each covering two
dimensions, each citing `file:line` from real code.

**How to read:**
- Verdict: **meets** / ◑ **partial** / ✗ **missing** / **n-a** (not applicable to this codebase).
- `[offline]` = verifiable here without the corp network; `[requires corp]` = needs in-network validation.
- Evidence is code, not docs (docs may drift). Where the code couldn't confirm a claim, it's marked missing/n-a.
- **"Partial (PC-2 gated)"** means the capability exists but is intentionally dormant behind the unresolved
  PC-2 data-handling precondition — a *gated* state, not a failure.

This document is **findings only**. It is the input to the `enhancement-program` skill, which sequences
the fixes into waves. No code was changed to produce it.

---

## 1. Alignment matrix (summary)

| Dim | Verdict | Headline finding (evidence) | Class |
|---|---|---|---|
| **F1** Orchestration | ◑ partial *(PC-2 gated)* | Linear pipeline; RateBroker cross-model parallelism exists but judge hardcoded `None`, only 1 model paced (`run.py:556`) | offline |
| **F2** Context engineering | ◑ partial | No JIT retrieval — `ContextSelector` built with no `relevance_scorer` (`run.py:339`); chunks pre-stuffed | offline |
| **F3** Evals / judge | ◑ weak | Single-call **pointwise rubric** judge — the refuted pattern (`judge.py:15-44`); no κ/α; CI gate inert (`recall_floor=0.0`) | offline |
| **F4** Injection | ✗ **missing** | Untrusted email not spotlighted/delimited (`gateway.py:134-136`); citation gate **shadow-only, never drops** | offline |
| **F5** Reliability | ◑ partial | Token expiry undetected — 401 → bare `raise` (`gateway.py:370`); no circuit breaker; degradation good (PR4) | offline |
| **F6** Observability | ◑ partial | `start_http_server` swallows bind failure (`metrics.py:343`) → the April "metrics not available"; no OTel/spans | offline◑ |
| **F7** Process | ◑ partial | No closed error-analysis loop (traces→taxonomy→gold); ARCHITECTURE.md self-declared SoT, no verify step | offline |
| **F8** Memory | ✗ **missing** | No cross-run memory — daily product re-derives everything; a multi-day action can resurface (`run.py:863-941`) | offline |
| **F9** Test-time compute | ✗ missing *(opportunity)* | N=1 always (`temp=0.0`); citation gate is a real **partial verifier** → best-of-N upside unused | requires corp |
| **F10** Versioning / provenance | ◑ weak | No per-run provenance manifest (`run_meta` omits code SHA, model ID, prompt versions); prompts edited in-place | offline |
| **F11** Agentic security | ✗ missing | Zero OWASP/ASI mapping; no fuzz/property tests of the bs4 HTML parser (highest-risk untrusted surface) | offline |
| **F12** Delivery / supply chain | ✗ **missing** | Dockerfile builds from `pyproject.toml`, ignores `uv.lock`; no pip-audit/SBOM/checksums; carry-in is ad-hoc `ActionPulse.zip` | offline |

---

## 2. Ranked gaps (impact ÷ effort; offline-verifiable first)

1. **F10 — add a per-run provenance manifest** *(cheap, high audit value)*. Write code SHA + model IDs +
   prompt versions + config hash into `run_meta` (`run.py:217-235`). The pieces exist scattered
   (`_config_sha256` at `run.py:855`); just surface them. Extends P2 from evidence-provenance to
   system-provenance.
2. **F6 — stop swallowing the exporter bind failure** *(cheap; root-causes the April signal)*.
   `metrics.py:343` downgrades a failed `start_http_server` to a warning → silent unobservability. Fail
   loud or surface via healthz.
3. **F4 — spotlight/delimit untrusted email + move the citation gate from shadow to enforcing**
   *(highest security impact)*. The gate (`citation_gate.py:54-82`) already computes offset+SHA fidelity
   but only annotates `weak_evidence`; it never drops. Untrusted bodies are concatenated with a
   non-unique `---` and no "treat as data" guard (`gateway.py:134-136`).
4. **F3 — replace the single-call rubric judge** *(directly the refuted pattern)*. Move to
   pairwise / reference-anchored, add κ/α drift tracking, make the CI gate non-inert (`recall_floor`
   defaults to `0.0`).
5. **F12 — reproducible air-gap bundle**. Dockerfile must consume `uv.lock`; add pip-audit + SBOM +
   checksums to replace the ad-hoc zip.
6. **F5 — detect token/credential expiry**. Classify a 401 with an actionable "rotate token" error
   instead of a generic partial digest.
7. **F8 — cross-run dedup ledger**. A `.state/` delivered-items ledger keyed by `evidence_id` /
   content-hash with a TTL — fixes resurfacing *and* doubles as the privacy-decay mechanism.
8. **F9 — best-of-N on the citation dimension** *(biggest quality upside; requires corp)*. Sample the
   extractor N× (tokens unmetered; 15 RPM bounds N), keep the candidate maximizing offset-verifiable
   support. Other dimensions need an entailment/coverage verifier before sampling pays there.

---

## 3. Full per-dimension verdicts

### F1 — Orchestration & multi-agent decomposition · ◑ partial (PC-2 gated)
| item | verdict | evidence | note |
|---|---|---|---|
| Architecture follows task structure | meets | `run.py:686-806` | Linear coupled pipeline single-threaded; correct given data deps |
| Spawned agents get objective/format/guidance/boundaries | n-a | `run.py:359-385` | No agent spawning; one LLM call |
| Effort scaled to complexity | meets | `rate_broker.py:39-45`; `run.py:556,590` | Extractor budget=2; fleet/judge/reranker `None`, dormant behind PC-2 |
| Verification offloaded to separate capacity | partial | `rate_broker.py:121-158`; `run.py:548-562` | Buckets enable it; only 1 model paced live; judge `None` |

### F2 — Context engineering · ◑ partial
| item | verdict | evidence | note |
|---|---|---|---|
| Smallest high-signal token set | partial | `split.py:144,558`; `context.py:121` | Hard budget + caps, but verbose per-chunk evidence header (`gateway.py:258-267`) |
| Just-in-time retrieval | missing | `run.py:339-345`; `relevance.py:7-11` | All chunks pre-stuffed; embeddings/reranker JIT never wired (PC-2 off) |
| Three-level progressive disclosure (skills) | n-a | — | No Agent Skills; plain prompts loaded whole |
| System-prompt altitude | partial | `extract_actions.en.v1.txt:35-71` | Good taxonomy heuristics but enumerative; no canonical example |
| Untrusted content delimited/spotlighted | partial | `gateway.py:134-137,258-267` | Evidence in user role w/ `---`; no explicit spotlight markers (see F4) |

### F3 — Evaluation & LLM-as-judge · ◑ weak
| item | verdict | evidence | note |
|---|---|---|---|
| Gold set from real usage, grows from traces | meets | `eval/gold_set.py:1,59-78`; `cli.py:430` | Bootstrapped from MM emoji reactions keyed by `trace_id` |
| Judge calibrated vs human labels | partial | `judge.py:54-83`; `test_eval_judge_gold.py:81` | Compares predicted to gold; no live calibration loop |
| Reports precision/recall separately | meets | `judge.py:47-51,71` | P/R/F1 per-stratum |
| κ / Krippendorff α as drift trackers | missing | (grep: 0 hits) | Only P/R/F1/Brier; no agreement statistic |
| Binary over Likert | meets | `judge.py:18-20,43` | `{supported: bool}` |
| Release-gate pairwise/reference-anchored (not single-call rubric) | **missing** (refuted pattern) | `judge.py:15-20,37-44` | Single LLM call, pointwise 0–1 rubric |
| Position/verbosity bias mitigated | n-a | `judge.py` | No pairwise, so absent |
| CI eval gate | missing | `.github/workflows/ci.yml:43-63` | CI runs pytest only; `eval-judge` manual |
| Recalibrate on cadence | missing | `calibrate.py` | `recall_floor` default `0.0` → gate inert |

### F4 — Injection & untrusted-content safety · ✗ missing
| item | verdict | evidence | note |
|---|---|---|---|
| Untrusted email spotlighted/delimited | missing | `gateway.py:134-136,258-266` | Raw after non-unique `---`; no data/ignore-instructions framing |
| Least privilege (no content-driven tool/action) | meets | `gateway.py` (no tools); `deliver/mattermost.py:57-61` | JSON-only call; sole egress webhook POST |
| Egress renders content inert | partial | `deliver/mattermost.py:59-61,98-113` | POSTs raw markdown; injected link/mention renders live, no auto-exec |
| Adversarial fixtures present | missing | `tests/fixtures/emails/` | No injection fixtures |
| Containment gate prevents unverified output | partial | `citation_gate.py:54-82`; `run.py:583-591` | Offset+SHA containment but **shadow**: annotates `weak_evidence`, never drops |

### F5 — Reliability & failure handling · ◑ partial
| item | verdict | evidence | note |
|---|---|---|---|
| Checkpoint/resume vs restart | partial | `run.py:655-713,890-941` | Idem sidecar skips unchanged re-runs; no mid-pipeline resume |
| Adapt to transient (not abort) | meets | `gateway.py:363-369,292-308`; `run.py:1092-1097` | 429→penalize+retry, 5xx→retry, per-stage degrade |
| Token/credential expiry → actionable error | missing | `config.py:187-192`; `gateway.py:347-370` | env-only read; 401 → bare `raise` |
| Circuit breaker | missing | `gateway.py:292-297` | `tenacity stop_after_attempt(2)` only |
| Shadow / rainbow / flag rollout | missing | `config.py:489-518`; `run.py:580-591` | Shadow gate annotates; no parallel new-vs-old path |
| Graceful degradation per stage | meets | `run.py:1022-1089,351-426` | PR4 policy, pure + testable |

### F6 — Observability · ◑ partial
| item | verdict | evidence | note |
|---|---|---|---|
| OTel GenAI semconv (`gen_ai.*`) | missing | `metrics.py` (whole) | Prometheus only; no `gen_ai.*` |
| Span-per-call + span-per-stage | missing | `run.py:1280-1288`; `logs.py:59-76` | Durations as histogram + log; no spans |
| Decision-tracing without payloads | meets | `logs.py:98-135`; `run.py:1256-1277` | structlog JSON, redaction |
| Quality metrics as first-class series | meets | `metrics.py:162-204,318-330` | citation_validation_failures, weak_evidence, support_score |
| Exporter works in target env | partial | `metrics.py:332-344,25-39` | `start_http_server` swallows bind failure → silent "metrics not available" |

### F7 — Process & knowledge · ◑ partial
| item | verdict | evidence | note |
|---|---|---|---|
| Error-analysis loop (traces→taxonomy→assertions) | partial | `replay_harness.py:1-7,88-107` | Metric-regression harness; no open/axial coding into gold |
| Prompts versioned + eval-diffed | partial | `prompts/*.changelog`; `eval/changelog.py`; `prompt_eval.py:195` | Versioned w/ changelog; no A/B eval-diff |
| Docs as a *verified* SoT | partial | `ARCHITECTURE.md:1-6` (2026-03-30) | Asserts SoT but no verify-vs-code mechanism |
| Failure taxonomy + transcript review | partial | `ARCHITECTURE.md:699-722` §8 | Static stage-error table, not a review loop |

### F8 — Memory architecture · ✗ missing
| item | verdict | evidence | note |
|---|---|---|---|
| Episodic/semantic/procedural split | missing | (no module) | No long-term memory store |
| Cross-run dedup (don't resurface yesterday's item) | missing | `run.py:863-875,921-941` | Idem sidecar dedups same-day rebuilds only |
| Thread continuity across days | missing | `threads/build.py`; `ingest/ews.py:454` | Rebuilt per-run; watermark is a fetch cursor |
| User-feedback memory | missing | `eval/gold_set.py:16` | MM reactions for offline eval only, not runtime memory |
| Importance scoring + TTL/decay | missing | `select/ranker.py:372` | "decay" is per-run recency, not stored-state TTL |
| Decay-as-privacy | n-a | — | No retained state to expire; privacy via not-storing (defensible) |
| Cross-run state (`.state/`) | partial | `ews.py:502-535`; `run.py:203-205` | ISO watermark + idem SHA sidecar; no item-level state |

### F9 — Test-time compute · ✗ missing (opportunity)
| item | verdict | evidence | note |
|---|---|---|---|
| Best-of-N where a hard verifier exists | missing | `gateway.py:140-170` | One call + 1 conditional quality retry; N=1 |
| Verifier selects among samples | missing | `citation_gate.py:48-72`; `run.py:580-591` | Gate annotates one digest, shadow; never selects |
| Sampling enabled (temp/top_p) | missing | `config.py:113-118`; `gateway.py:335` | temp `0.0`; no top_p/n |
| Self-consistency / majority vote | missing | (none) | `repair.py:53-55` substring-only, judge-gated |
| Sampling on independent buckets | n-a | `rate_broker.py`; `config.py:134` | Buckets exist but unused for sampling |

### F10 — Versioning & provenance · ◑ weak
| item | verdict | evidence | note |
|---|---|---|---|
| Prompts immutable versioned (new version, not in-place) | partial | `prompts/extract_actions.v1.txt`; commit `cf593c6`; `*.changelog:5` | Versioned by filename, but v1.2 edits land inside `.v1` |
| Eval-gated promotion | missing | `.github/workflows/ci.yml:37-61` | eval tooling exists but never gates |
| Output traces to exact prompt version | meets | `run.py:372,1004`; `jsonout.py:57`; `schemas.py:261` | `prompt_version` in digest JSON + run_meta |
| Gold sets / fixtures versioned as datasets | partial | `eval/corpus.py:6,24`; `gold_set.py:59` | Corpus frozen; no version/hash; gold loose JSONL |
| Manifest: code SHA | missing | `run.py:217-235`; `diagnostics.py:140` | No git SHA captured in run path |
| Manifest: prompt versions | partial | `run.py:381` vs `run_meta` (`run.py:217`) | In digest JSON, not the trace meta manifest |
| Manifest: model IDs | missing | `run.py:217-235`; `schemas.py` | Model only in sanitized config |
| Manifest: config hash + flag state | partial | `run.py:855` (`_config_sha256`) | Only in `.idem.json` sidecar, not run_meta |
| Manifest models SLSA materials (all inputs) | missing | (no in-toto/SLSA) | trace meta = evidence-provenance only |

### F11 — Agentic security taxonomy · ✗ missing
| item | verdict | evidence | note |
|---|---|---|---|
| OWASP Agentic Top-10 (ASI01–10) review gate | missing | (grep: 0) | No taxonomy mapping anywhere |
| Untrusted-parser (normalize/html) fuzz/property-tested | missing | `tests/test_html_normalization.py`; no `hypothesis` | Example-based only; malformed-HTML fallbacks (`html.py:116-136`) reached only by hand-picked fixtures |

### F12 — Delivery process & supply chain · ✗ missing
| item | verdict | evidence | note |
|---|---|---|---|
| Eval failure → (gold row + tracked issue w/ evidence link) | missing | `eval/prompt_eval.py:24-35` | `EvalIssue` is in-memory severity only |
| SLOs stated (delivery + quality floor + error budget) | partial | `docs/README.md:117,124` | Latency targets only; no quality floor / error budget |
| Reproducible bundle: locked deps | partial | `uv.lock`; `docker/Dockerfile:11` (`pip install -e .`) | Lockfile exists but build path ignores it |
| pip-audit in pipeline | missing | `.github/workflows/ci.yml`; `pyproject.toml` | No vuln scan |
| SBOM generated | missing | (grep: 0) | Absent |
| Checksums / build manifest for carry-in | missing | `deploy/`; `Makefile` | Ad-hoc `ActionPulse.zip` at repo root |
| Mapped to OWASP agentic supply-chain risk | missing | CI / deploy | No attestation |

---

## 4. Notes

- **"Partial" on F1/F2 is mostly by design.** The fleet, JIT retrieval, and the judge are dormant
  *because* they are PC-2-gated and flagged off — the audit distinguishes "gated and dormant" from
  "missing." Resolving PC-2 (a product/compliance decision) is the unlock, not new code.
- **The audit independently confirmed two prior research findings in live code:** F3's judge *is* a
  single-call pointwise rubric (the pattern a 2026 research pass refuted as "best-aligned"), and F4's
  untrusted-content handling is the predicted weak point.
- **`requires corp` items** (F9 best-of-N timing under the 15 RPM cap; the actual corp metrics-scrape
  reachability for F6) cannot be settled offline and must be validated in-network.

## 5. Provenance

- Auditors: 6 read-only subagents (Read/Grep/Glob/Bash), each scoped to 2 dimensions, returning a
  verdict table with `file:line` evidence; synthesis done single-threaded.
- Bar version: `frontier-bar.md` v0.1 (2026-06-11) — flags that some dimensions are `grounded`/`covered`
  rather than `strong-primary`, and that the bar's own re-research is due quarterly.
- No code was modified. This is the input artifact for the `enhancement-program` skill.
