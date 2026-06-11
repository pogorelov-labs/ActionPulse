# Quality-program backlog — ActionPulse digest-core

Updated 2026-06-11 (fourth pass): fleet PRs #81/#82/#83 MERGED — composed main
verified green (753 passed ×2, eval-replay baseline OK). All fleet flags remain
off until EP-14 corp validation. D1–D7 resolved — see `ENHANCEMENT_PROGRAM.md`.
**D7 enacted 2026-06-11**: the open rows (post-decision survivors) are seeded
into Plane ACTPULSE, sanitized; ids in the table below.

## Done (W1 + W2 + fleet wiring, PRs #62–#72 + decision enactment + dep hygiene)

| id | title | landed in |
|---|---|---|
| EP-1 | Per-run provenance manifest | #63 |
| EP-2 | Surface metrics-exporter bind failure | #64 |
| EP-3 | Classify LLM credential expiry | #65 |
| EP-4 | Spotlight untrusted evidence + injection fixtures (flag off) | #70 |
| EP-5 (1-2) | Frozen offline baseline + κ/α agreement stats | #67 |
| EP-6 | Air-gap bundle + locked Docker + pip-audit CI | #68 |
| EP-7 | Cross-run dedup ledger (annotate-only) | #71 |
| EP-8 | OTel GenAI semconv spans (flag off) | #72 |
| EP-9 | HTML normalizer property fuzz | #69 |
| D1/D2/D3/D6 | Quarantine section · CI eval-replay gate · ledger default-on + ↻ · ADR-008 v2 + llm_budget visibility | decision-enactment PRs (2026-06-11) |
| EP-13 | Dependency bumps: all 9 pip-audit findings cleared + full regression | #80 |
| EP-12 | Fleet wiring (D4/PC-2): reranker support scores into the P2 gate; cross-model judge rescue for quarantine; flags default off, degrade-not-drop, replay sidecar channel. Live behavior corp-gated (EP-14 ①–⑥) | #81, #82 |
| EP-5 (3) | Hybrid judge per D5: reference-anchored eval vs gold (`eval-judge-run`, report-only) + pairwise library (EP-10 consumer); `eval.judge_mode` default `pointwise`; also fixed reranker/judge YAML sections never merging. First real calibration corp-gated (EP-14 ⑦) | #83 |
| EP-10 | Best-of-N selected by the citation gate: `extract.best_of_n` (default 1), gate-as-selector, pairwise tie-break, offline proof archived (`baselines/2026-06-11-best-of-n-offline.json`); ACTPULSE-65 closed. Live tuning corp-gated (EP-14 ⑧) | #86 |

## Open

| id | title | wave | gate / depends on | Plane |
|---|---|---|---|---|
| EP-14 | W3 corp validation pack — **visit checklist ready: `../VISIT_CHECKLIST_EP14.md`** (validation-pack format: carry-in / offline preflight / flag states / probes / success criteria / bring-back; offline preflight green 2026-06-11, bundle seals on visit day). Original items: carry `make bundle`; injection probes (threat-model §6); LLM endpoint curl check; re-record fixtures on main; EP-2 scrape reachability; EP-3 real-401; OTel collector decision; first `items_weak`/quarantine read-out. **Added by EP-12/EP-5(3) wiring:** ① `/rerank` exact path + payload/response shape (curl; flip `reranker.endpoint_path` if `/v1/rerank` or `/v1/score`); ② reranker live score distribution on real items → first `tau` read-out; ③ fleet RPM/latency under the 3-parallel key budget (broker penalties observed); ④ record a `--record-llm` run with `reranker.enabled` → verify `.fleet.json` sidecar replays; ⑤ judge live verdict quality vs `tau_repair` → first `items_repaired` read-out; ⑥ design the judge record/replay channel (judge disabled under replay today); ⑦ first reference-judge calibration vs gold (`eval-judge-run`, report-only) — κ + CI floor into `docs/audits/baselines/`; ⑧ best-of-N live tuning (EP-10): pick N + `sample_temperature`, raise `llm.stage_call_budgets.extractor` alongside, first sampled-candidate quality read-out vs the deterministic shot | W3 (requires corp) | next corp visit | ACTPULSE-66 |
| EP-15 | `recall_floor` calibration: export MM reactions → `eval-gold` → `eval-calibrate` → set floor > 0; gate flips (CI eval floor, `eval.judge_mode`) only after κ ≥ 0.41 with the bootstrap CI floor | W4-entry | D2 ✅ (second half); needs reactions data + EP-14 ⑦ | ACTPULSE-67 |
| EP-11 | Continuous failure→gold→issue loop (`backlog-loop` skill) | W4 | program steady-state | ACTPULSE-68 |
