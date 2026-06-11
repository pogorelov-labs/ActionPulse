# Quality-program backlog — ActionPulse digest-core

Updated 2026-06-11 after the owner decision interview (D1–D7 resolved — see
`ENHANCEMENT_PROGRAM.md`). Open items are seeded into Plane (ACTPULSE) per D7;
issue ids recorded below when filed.

## Done (W1 + W2, PRs #62–#72 + decision enactment)

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

## Open

| id | title | wave | gate / depends on | Plane |
|---|---|---|---|---|
| EP-12 | Fleet wiring (PC-2 resolved): reranker support scores + cross-model repair + judge into `run.py` | W3-prep (offline) + corp validation | D4 ✅; flags stay off until corp run | TBD |
| EP-5 (3) | Hybrid judge: reference-anchored release gate + pairwise selection path | W2-tail | D5 ✅; needs EP-12 judge plumbing | TBD |
| EP-10 | Best-of-N extraction selected by the citation gate (offline harness first) | W3 | D6 ✅ (ADR-008 v2); EP-12; baseline #67 | TBD |
| EP-13 | Dependency bumps for the 9 pip-audit findings (cryptography, idna, lxml, pytest, urllib3) + full regression | offline | owner acknowledged via D-interview | TBD |
| EP-14 | W3 corp validation pack: carry `make bundle`; injection probes (threat-model §6); LLM endpoint curl check; re-record fixtures on main; EP-2 scrape reachability; EP-3 real-401; OTel collector decision; first `items_weak`/quarantine read-out | W3 (requires corp) | next corp visit | TBD |
| EP-15 | `recall_floor` calibration: export MM reactions → `eval-gold` → `eval-calibrate` → set floor > 0 | W4-entry | D2 ✅ (second half); needs reactions data | TBD |
| EP-11 | Continuous failure→gold→issue loop (`backlog-loop` skill) | W4 | program steady-state | TBD |
