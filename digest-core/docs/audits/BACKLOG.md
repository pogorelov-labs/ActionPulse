# Quality-program backlog — ActionPulse digest-core

Seeded 2026-06-11 from `ENHANCEMENT_PROGRAM.md` (one row per EP item). Plane seeding deferred
pending D7 — when approved, file one ACTPULSE issue per row and record the id here.

| id | title | wave | Fn | verification path | flag | status |
|---|---|---|---|---|---|---|
| EP-1 | Per-run provenance manifest | W1 | F10 | offline-verifiable | — (additive) | in progress (this session) |
| EP-2 | Surface metrics-exporter bind failure | W1 | F6 | offline-verifiable (corp scrape check → W3) | `observability.fail_on_exporter_error` | in progress (this session) |
| EP-3 | Classify LLM credential expiry actionably | W1 | F5 | offline-verifiable (real 401 → W3) | — (classification only) | in progress (this session) |
| EP-4 | Spotlight untrusted email + adversarial fixtures | W2 | F4 | offline (live probes → W3) | `llm.spotlight_evidence` (off) | todo (gate flip blocked by D1) |
| EP-5 | Judge agreement stats + calibration loop | W2 | F3 | offline (gold growth → corp) | `eval.judge_mode` (pointwise) | todo (D5/D2 gate steps 3-4) |
| EP-6 | Reproducible air-gap bundle | W2 | F12 | offline-verifiable | — (build path) | todo |
| EP-7 | Cross-run dedup ledger | W2 | F8 | offline-verifiable | `memory.dedup_ledger` (off) | todo (default-on blocked by D3) |
| EP-8 | OTel GenAI semconv spans | W2 | F6 | offline (collector → corp) | `observability.otel_enabled` (off) | todo |
| EP-9 | Property/fuzz tests for HTML normalizer | W2 | F11 | offline-verifiable | — (tests) | todo |
| EP-10 | Best-of-N selected by citation gate | W3 | F9 | offline harness; live requires corp | `extract.best_of_n` (1) | planning only (D6, D4) |
| EP-11 | Continuous failure→gold→issue loop | W4 | F7 | offline | — | planning only |
