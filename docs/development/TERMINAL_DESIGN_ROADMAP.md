# Terminal Design — Integration Map & Implementation Roadmap

Companion to [`TERMINAL_DESIGN.md`](TERMINAL_DESIGN.md) (the rules). This document is
(A) the **integration map** — where the rules bind to the development workflow so they
cannot silently rot, and (B) the **execution roadmap** — the PR-by-PR plan to bring every
surface to compliance, including the **English-by-default language program**.

**Both original tracks complete (2026-06-12, PRs #90–#98):** L1 + T1–T7 shipped; remaining work lives in L2/L3 (corp validation + docs translation), the fleet-lane rendering that activates with REDESIGN PR2, and the **U-track** below (owner UX comments on the shipped terminal experience, 2026-06-12).

Status legend: ☐ planned · ◐ in progress · ☑ done. Update this file in every PR that
advances a step (same discipline as `REDESIGN_PLAN.md`).

---

## A. Integration map — how TERMINAL_DESIGN.md becomes law

The design system binds at five layers. Documents alone rot; the structural layers
(3–5) are the real enforcement.

| # | Layer | Binding | Status |
|---|-------|---------|--------|
| 1 | **Agent/dev rules** | Root `CLAUDE.md` Golden Rule: "All terminal output follows `docs/development/TERMINAL_DESIGN.md`". `digest-core/CLAUDE.md` Code Style bullet pointing here. Key Documents lists both docs. | ☑ this PR |
| 2 | **Review gate** | `CONTRIBUTING.md` → "Terminal output checklist" (5 checks below) — reviewers apply it to any PR that touches user-visible output. | ☑ this PR |
| 3 | **Structural: the `ui` module** | All tokens/glyphs/console construction live in `digest_core/ui/` (T1). Feature code imports tokens; it never constructs styles. A rule that lives in code cannot be forgotten — this is the Lip Gloss "structure vs style" lesson applied. | ☑ T1 |
| 4 | **Conformance test** | `tests/test_terminal_conformance.py` (T1): walks the source tree — RGB/hex outside `ui/`, `Console(`/`Theme(` outside the factory, spinner literals outside `ui/`, inline Cyrillic in renderer files (labels.py exempt; deliberate bilingual lines carry `# i18n-ok`). Runs in `make test` → CI. | ☑ T1 |
| 5 | **Architecture law** | `ARCHITECTURE.md` **ADR-013** — terminal surfaces follow the design system; split-region ProgressSink architecture; EN default; no mouse; append-only degradation. | ☑ T7 |

### Reviewer checklist (the §2 of CONTRIBUTING.md)

1. New user-visible strings: English; report-bound strings go through `assemble/labels.py`,
   never inline (after L1).
2. Colors/glyphs only via `ui` tokens (after T1); state always carried by glyph+word,
   never color alone.
3. Anything that can exceed ~1 s shows liveness; anything animated is throttled and
   TTY-gated; non-TTY path exists and is append-only.
4. No mouse reporting; Esc cancels the question, Ctrl+C aborts with exit 130 and no
   traceback; cursor restored on every exit path.
5. Long values truncate per §6.2 (end-ellipsis for messages/URLs, tail-preserving for
   paths); nothing relies on terminal width > 80.

---

## B. Current state (audited 2026-06-12)

| Surface | Design-system state | Language state |
|---------|--------------------|----------------|
| `install.sh` | ✅ compliant | ❌ Russian strings |
| Setup wizard | ✅ compliant | ❌ Russian strings |
| `cli run` | ❌ raw structlog JSON (no progress UI at all) | mixed (logs EN, digest RU) |
| `cli diagnose` | ◐ plain echo | EN |
| Digest (the report) | n/a | ❌ RU **hard-wired into section identity** (see below) |
| README / quick-start docs | n/a | ❌ Russian |

**The language coupling, measured** (this is why L1 is a refactor, not a string swap):

- The LLM is *instructed* in English for all qwen models (`run.py` per-model prompt map)
  but the prompt **rules force Russian output**: "All section titles and all item text in
  the output must be in Russian" (`extract_actions.en.v1.txt`).
- Section identity **is** the Russian title string: `run.py` `SECTION_ORDER =
  {"Мои действия": 0, "Срочное": 1, "К сведению": 2, "Не подтверждено": 3}`;
  `mattermost.py` groups on `section.title == "К сведению"` / `"Статус"`;
  `markdown.py` hardcodes `"## Мои действия"` / `"## К сведению (FYI)"` in the enhanced
  path; stage-degradation messages («Сбой при…», «Дайджест неполный.») are injected into
  digest output from `run.py`.
- Blast radius: 30 RU-literal matches across 12 test files; `tests/mock_llm_gateway.py`
  emits RU titles; `eval/corpus/*.baseline.json` and all committed LLM recordings are RU.
- Citation gate invariant that must survive any language change: **quotes stay in the
  source language of the email** (verbatim-substring rule) — translation of quotes would
  break `evidence_id`/offset validation (P2 traceability).

---

## C. Roadmap

Two tracks. **L-track first** — it moves every user-visible string exactly once; doing
T-track first would build pretty output around strings about to change.

### L-track — English by default

#### L1 — `feat/english-default` *(shipped: #91 → restacked as #92 after the stacked-merge incident, merged 2026-06-12)* ☑

Decision (owner, 2026-06-12): English is the default for README, controls (installer +
wizard + CLI), prompts, and reports. **Reports switchable to Russian via user settings**
(`report.language` in `configs/config.yaml`, asked by the wizard).

Design — canonical keys, not string swaps:

1. **`assemble/labels.py` (new):** canonical section keys (`my_actions`, `urgent`, `fyi`,
   `status`, `unconfirmed`) + per-language titles (en/ru) + `normalize_title()` accepting
   *both* languages case-insensitively + digest header + stage-degradation messages per
   language. Rendering always re-derives the displayed title **from the key** — output
   language is deterministic even if the LLM disobeys, and RU fixtures/recordings keep
   working through normalization.
2. **`report.language`** config (`en` default, `ru` opt-in; env `DIGEST_REPORT_LANGUAGE`),
   example config updated; wizard question 7 with default `en`.
3. **Prompt v2 for EN output:** new `extract_actions.en.v2.txt` — EN instructions + EN
   output titles constrained to exactly `My actions / Urgent / FYI`; **quote rule
   unchanged** (source language, verbatim). RU path keeps the existing per-model map
   (en.v1: EN instructions, RU output). `.changelog` updated — prompt output language is
   a contract change, hence the version bump (ADR-009 discipline).
4. `run.py`: ordering via `normalize_title`; degrade messages via labels; prompt
   selection becomes `(model, report.language)`.
5. `markdown.py` / `mattermost.py`: headers + grouping via keys/labels.
6. Strings: `install.sh`, `setup_wizard.py`, `setup_autodetect.py` notes, README.md → EN.
   Root `CLAUDE.md` Language section rewritten (EN default; `report.language: ru` is the
   user setting; deeper `docs/` translation = L3 backlog).
7. **Eval & replay safety:** eval corpus/baselines stay RU — eval paths pin
   `report.language="ru"` so `make eval-replay` compares like-for-like; LLM recordings
   replay regardless (recorded RU titles normalize). Config-hash change forces one
   idempotency rebuild — expected, documented in PR.
8. Tests: labels unit tests (normalization both languages, unknown passthrough);
   default-EN rendering tests; RU-asserting tests get explicit `language="ru"` or
   flipped expectations — case by case, no blanket sed.

Risks & honesty: EN extraction *quality* is unmeasured (gold F1 0.601 was RU-pipeline) —
corp item **C1**; wizard question count changes (piped E2E protocols update);
`SECTION_ORDER` for «Статус» appears only in mattermost grouping today — keep behavior.

#### L2 — corp validation of EN extraction ☐
Run the replay corpus + a live day with `report.language=en` inside the perimeter;
compare item counts/citation validity vs RU baseline; record in
`CORP_VALIDATION_FINDINGS`. Only after L2 do we call EN reports production-grade.

#### L3 — docs translation backlog ☐
`docs/installation/QUICK_START.md`, `docs/README.md`, runbooks → EN (product docs follow
the new default; runbooks last — they are operator-facing and currently RU-fluent).

### T-track — terminal UX implementation

#### T1 — `ui` module + tokens + conformance test ☑ *(feat/ui-tokens, 2026-06-12)*
`digest_core/ui/`: `theme.py` (rich `Theme` with §2 tokens), `glyphs.py` (✓/✗/⚠/⌁ +
ASCII fallbacks), `console.py` (factory honoring the §2.2 env contract; single shared
`Console`), spinner constant. Wizard + cli adopt it (mechanical). Conformance test
(map layer 4). Exit: zero hardcoded styles outside `ui/`.

#### T2 — ProgressSink event seam ☑ *(feat/progress-sink, 2026-06-12)*
§4.4: `ProgressSink` protocol; `run.py` emits `on_stage_start/end` (with funnel counts
already computed for `stage_durations_ms`), `on_llm_attempt`, `on_delivery`. `NullSink`
default — **zero visual change**, structlog untouched. Exit: event-sequence test from
replay fixture asserts the full funnel.

#### T3 — PlainSink + `--progress` flag ☑ *(feat/progress-plain, 2026-06-12)* — first visible win
Append-only line per transition (terraform model): `✓ INGEST  124 messages (3.1s)`.
`--progress=auto|live|plain|none`, auto = TTY detection; CI ⇒ plain. Exit: `cli run
--dry-run` reads like a build log instead of JSON; matrix row "non-TTY" satisfied.

#### T4 — RichLiveSink ☑ *(feat/progress-live, 2026-06-12)* — the §4.1 footer
History funnel lines + one ≤8-line Live footer (`refresh_per_second=10`,
`vertical_overflow="ellipsis"`, 1-col margin), spinner + elapsed + stage counters,
amber-after-10s, failure freeze with log tail. Exit: pty-captured E2E of a replay run;
degradation matrix verified per §7 (TTY/ASCII/pipe/CI/dumb).

#### T5 — LLM & lane counters ☑ *(feat/progress-llm-counters, 2026-06-12; per-attempt tokens + lane RPM rendering activate with the fleet gateway hooks, REDESIGN PR2)*
`on_llm_attempt` → footer tokens ↑/↓, `попытка→attempt n/2`, single-lane RPM display.
Interface shaped per §4.3 so REDESIGN_PLAN PR2 (fleet/RateBroker) plugs lanes in without
renderer changes. Exit: replay-driven test shows attempt/token counters.

#### T6 — wizard select menus + diagnose tokens ☑ *(feat/wizard-menu-adr, 2026-06-12)*
First real menu = the L1 language question → adopt §5.2 keymap (questionary or
hand-rolled 2-option selector; **no mouse**). `diagnose` adopts ✓/✗ tokens. Exit:
arrows/j-k/Esc behavior matches the table; piped protocol still scriptable.

#### T7 — ADR + matrix CI job ☑ *(feat/wizard-menu-adr, 2026-06-12 — ADR-013; `terminal-matrix` CI job runs the progress/ui/conformance tests under `TERM=dumb` + `NO_COLOR=1`)*
ADR in `ARCHITECTURE.md` (verify next free number); optional CI job running the sink
tests under `TERM=dumb`, `NO_COLOR=1`, piped stdout to lock the degradation matrix.

### U-track — run-experience follow-ups (owner comments, 2026-06-12)

Four owner comments on the shipped UX, each with its design decision recorded here
*before* implementation (same discipline as the original tracks). One PR per step,
no stacking.

#### U1 — setup finale speaks `actionpulse` ☑ *(feat/setup-finale-actionpulse, 2026-06-12)*

Owner: *"when setup is finished it should NOT confuse user with commands to run python
or digest_core.cli — just print user friendly actionpulse command."*

- The wizard "⌁ Done" panel now prints `actionpulse diagnose / run --dry-run / run /`
  (bare menu) — never `uv run python -m digest_core.cli …`; the obsolete
  `set -a && source` line is gone entirely (the CLI auto-loads
  `~/.config/actionpulse/env` since the launcher PR).
- **Self-healing launcher:** `make setup` from a bare checkout used to leave no
  `actionpulse` command at all — the finale would have advertised a command that
  didn't exist. `_ensure_launcher()` mirrors install.sh: writes the
  `~/.local/bin/actionpulse` shim **only when missing** (never overwrites an existing
  launcher), requires `uv`, and reports PATH state. Panel variants: on-PATH (commands
  only) · off-PATH (one-line `~/.zshrc` fix appended) · no-uv (module form as the
  honest fallback — the only place it survives).
- Same sweep: env-file header comment (`generated by 'actionpulse setup'`),
  `install.sh --no-wizard` hint (`actionpulse setup` — the launcher is installed
  before that branch), README reconfigure hint. Deeper docs (`docs/testing/*`,
  `docs/operations/*`) are RU operator docs = L3 backlog, deliberately untouched.

#### U2 — intra-stage liveness + retry/error counters ☑ *(feat/progress-intra-stage, 2026-06-12 — C1+C2 together, same seam)*

Owner: *"stages should be responsive to errors and update the UI constantly with
status"* + *"there should be counts of retries and errors for each stage."*

Design (amends `TERMINAL_DESIGN.md` §4.2/§4.4 in the same PR):

- **Vocabulary, minimal and honest** — two new sink events:
  `on_stage_progress(stage, done, total|None, unit, detail)` (data progress; producers
  pass numbers, renderers own wording) and `on_stage_retry(stage, attempt, max_attempts,
  reason)` (a transient failure that scheduled a retry — the event that makes a silent
  60 s backoff legible). Non-retry errors (e.g. messages skipped during EWS
  normalization) are not live events — they ride `on_stage_end` counts.
- **Producers:** EWS paging loop (`page n · m messages`, tenacity `before_sleep` →
  retry events, skipped-message counter), `_normalize_messages` (n/total), evidence
  split (n/total threads), LLM gateway (quality-retry = second `on_llm_attempt`,
  transient 429/5xx retries = `on_stage_retry` with the wait); THREADS/SELECT stay
  silent (sub-second — cargo's 500 ms rule: no bar for work that fast).
- **Throttling at the renderer, not the producer** (§3 holds): per-message events for
  a few hundred messages are fine; `RichLiveSink` stores state and Live pulls at
  10 fps. `PlainSink` prints retries always (rare, honest) and progress only as a
  ≥10 s "still running" reassurance line (terraform model). No total → no percentage,
  ever (§3).
- **Counts:** `retries`/`errors` keys join the §4.2 vocabulary, rendered on the
  permanent ✓/✗ line only when nonzero (warn-colored suffix), and land in
  `run_meta["stage_health"]` + `ews_fetch_stats` (pages/retries/skipped) →
  `trace-*.meta.json`, so the corp runbook read-out gains them. Glyph: `↻` for retry
  suffixes (no collision: MM trace lines use the *word* `repaired`/`повтор`, not the
  glyph).
- Fleet-ready: events carry the stage string; §4.3 lanes add `on_lane_update` later
  without touching this vocabulary.

#### U3 — thought-out run options in the menu ☑ *(feat/menu-run-options, 2026-06-12)*

Owner: *"main option run should propose most thought-out options — time period and
other useful params."*

- "Run digest" opens **one** §5.2 selector (no interrogation): Today (default,
  Enter-through) · Rolling 24h · Yesterday · Pick a date… (validated `YYYY-MM-DD`
  prompt; empty/Esc backs out) · Re-run today (`--force`, bypasses idempotency skip)
  · Repeat last run (label shows the stored params) · Back (`cancel_value` — Esc
  dismisses, never runs).
- Last-run params persist to `~/.config/actionpulse/last_run.json` (date/window/force,
  written after a successful menu run; the option label always shows the absolute
  stored date — no silent "yesterday drift").
- Dry-run stays a separate one-shot menu item; `--out/--state/--model` stay config/CLI
  concerns (the menu is for the daily decision, not plumbing).

#### U4 — interactive digest reader ☑ *(feat/digest-reader, 2026-06-12)*

Owner: *"available digest result reading view inside actionpulse terminal …
interactible, read message topics, authors names, distilled contents."*

- **Posture decision — §5.1 reaffirmed** after a real 3-way comparison:
  (i) line-oriented drill-down on `choose()` (digest → section → item → detail card
  printed into scrollback, Esc walks back up); (ii) `$PAGER` handoff (non-interactive
  wall of text — fails the "interactible" ask); (iii) bounded alt-screen browser
  (most app-like, but discards scrollback — and scrollback-as-evidence is a product
  principle (P2), §5.1 would need amending for a short-lived view that gains little).
  **Chosen: (i).** Every card the user opens stays in scrollback as copyable evidence.
  Revisit trigger unchanged: a long-lived dashboard.
- **Data model:** the artifact today carries `title/due/confidence/evidence_id/
  source_ref.msg_id/evidence_spans[].quote` but **no subject/author** (the extractor
  prompt never returns `email_subject`; the optional schema field exists but is
  unpopulated on the live path). Decision: **enrich at assemble time** from in-memory
  normalized messages via `msg_id` — populate `Item.email_subject`, add
  `Item.email_from` (schema-additive; `exclude_none` keeps old artifacts compatible;
  single-artifact reading; no dependency on an ingest snapshot at read time). Subjects/
  authors/quotes already appear in the delivered report — on-disk artifact + screen is
  the same privacy class; still never in logs.
- Surfaces: `actionpulse read [DATE]` (newest `out/digest-*.json` by default, lists
  all dates) + a menu item + "Read it now?" offered after a successful menu run.
  Item lists page at 8 + "More…" (the ≤9 quick-select invariant holds). Non-TTY
  `read` degrades to printing the rendered digest (append-only).
- Reader chrome is English (terminal surface); digest content renders as stored
  (`report.language` artifacts unchanged); no new report-bound strings.

#### U5 — one data home: stop scattering runtime files ☑ *(feat/data-home, 2026-06-12)*

Owner: *"why temp files, logs, configs, etc. are all scattered across different
directories that are NOT INSIDE ~/ActionPulse? Keep all relative files in one place."*

Audited scatter (live run from an arbitrary cwd): digests/trace-meta/idem sidecars →
**cwd-relative `./out`**; logs → `~/.digest-logs/run-*.log` (one file per run, unbounded;
`/tmp/digest-logs` fallback); EWS sync watermark + dedup ledger → **cwd-relative
`.state/`** (a real correctness bug — the incremental watermark silently resets when the
user runs from a different directory); `last_run.json` → `~/.config/actionpulse/`.

Decision — a single **data home** for everything regenerable:

- `paths.py`: `data_home()` = `$ACTIONPULSE_HOME` → the install checkout root (when
  running from a git checkout — the `~/ActionPulse` the owner means) → XDG fallback
  `~/.local/share/actionpulse` (wheel installs must not write into site-packages).
  Subdirs `var/out`, `var/logs`, `var/state` (gitignored).
- Defaults rewired (explicit `--out`/`--state`/config always win): CLI `run`/`read`,
  menu run/read, log dir, `sync_state_path` (config default becomes unset → resolved
  into `var/state`), `last_run.json` (legacy location read as fallback), diagnostics
  search roots.
- **Deliberate exceptions, documented:** secrets env stays at `~/.config/actionpulse/env`
  — inside the checkout, `git clean -xdf` would delete the only copy of the user's
  tokens and an accidental `git add -f`/archive could leak them; systemd units want a
  path that survives re-clones. CA chain stays in `~/.ssl` (TLS convention); the
  launcher must stay on PATH (`~/.local/bin`). `configs/config.yaml` already lives in
  the checkout.
- Legibility: new **`actionpulse paths`** command + the menu config view print the full
  path map, so "where is everything" has a one-keystroke answer.
- Config-default change (`sync_state_path`) changes `config_sha256` → one idempotency
  rebuild after upgrade (expected, same class as the U4 version bump).

#### U6 — maintenance: cleanup + logging toggle in the menu ☑ *(feat/maintenance-menu, 2026-06-12)*

Owner: *"provide a way through menu to clean temp and log files, or to turn off
logging/telemetry (default ON)."*

- Menu item **Maintenance** → one §5.2 selector: disk usage read-out (var/out, var/logs,
  var/state + legacy `~/.digest-logs`, `/tmp/digest-logs`) · clean logs (incl. legacy
  dirs) · clean digests older than 14 days · clean everything regenerable (confirm; never
  touches secrets/config) · toggle file logging.
- `actionpulse clean` CLI twin (scriptable; the menu calls the same helpers).
- **Logging toggle** = new `observability.log_to_file` (default **true** — matches the
  owner's "default ON"); `setup_logging(enabled=False)` keeps the console contract but
  writes no file; toggling rewrites `configs/config.yaml` (wizard-generated, comment-free
  — safe to round-trip).
- **Telemetry honesty:** there is no phone-home telemetry to turn off — logs and
  Prometheus/healthz ports are local, OTel tracing is **off by default**. The maintenance
  screen states this instead of pretending to disable something.

#### U7 — LLM failure explainer ☑ *(feat/explain-failure, 2026-06-12)*

Owner: *"if something goes wrong but LLM endpoint is working — send logs and useful data
to LLM in a well-structured prompt, print a short explanation."*

- **`actionpulse explain [--trace-id|--date]`** (default: the most recent run) + a menu
  offer after a failed/partial menu run (mirrors the U4 "read now?" offer): collect the
  run's `trace-*.meta.json` (status/error/degraded_stages/`stage_health`/llm trace/
  budget) + the tail of its **already-redacted** structured log + env facts → one
  compact JSON-mode call (`prompts/explain_failure.v1.txt`) → render
  `likely cause / explanation / next steps` as a card. Answer language follows
  `report.language`.
- Rides the existing `LLMGateway.judge()`-style generic call on its own stage budget
  (`explain`) and the shared RateBroker — retries, 429 penalties, auth classification,
  record/replay all inherited. Privacy: payloads/secrets never reach the prompt (logs
  are redacted at write time; meta is sanitized already); the prompt embeds *our* run
  telemetry, never email bodies.
- **Agent-CLI-under-the-hood: analyzed and rejected.** An opencode-style headless agent
  would need its own LLM access (corp gateway is the only reachable endpoint, ADR-012),
  would receive `LLM_TOKEN` as a third-party binary (supply-chain + secret-handling
  surface), and brings agentic tool-use we don't need for a single deterministic
  collect→ask→print step. Our gateway already owns retries/rate-limits/recording. If a
  future feature genuinely needs multi-step tool use, revisit with a pinned, vendored
  runtime — not for this.
- ADR-008 note: explain is a separate command, not a pipeline stage — it never competes
  with the run's 2-call budget; RPM broker still applies. Corp-only by nature (ADR-012):
  offline it fails fast with a clear message. Corp validation: runbook §9 gains a C5 row.

#### U8 — "chat with your inbox" ☐ *(analysis only — deliberately deferred)*

Owner: *"analyze how to take that even further — to chat with your inbox/messages."*

Shape: `actionpulse ask "<question>"` over the latest ingest snapshot/digest with
citation-quoting answers (Extract-over-Generate holds: answers must quote evidence).
**Why not now:** honest retrieval is the blocker — today's SELECT ranks by digest
priority, not query relevance, so ask would stuff the same top chunks regardless of the
question and hallucination pressure rises exactly where P1/P2 forbid it. Query-relevance
retrieval is the fleet's embeddings/reranker work (REDESIGN PR2); build `ask` on top of
that, reusing the U7 prompt/gateway plumbing and the dump-ingest snapshot as the corpus.
Interactive multi-turn also re-opens the §5.1 posture question — design there first.

### Dependency graph

```
L1 ──► T1 ──► T2 ──► T3 ──► T4 ──► T5 ──► (REDESIGN PR2 lanes)
        │                    
        ├──► T6 (after L1 wizard strings; menu lib decision)
        └──► T7 (anytime after T1; ADR after T4 proves the architecture)
L2 (corp) — independent, gates calling EN reports production-grade
```

## D. Quality gates (every PR on both tracks)

1. Full suite + lint green on the exact branch (repo law) — no PR before baseline.
2. Any visual change: pty-captured before/after in the PR description; degradation
   matrix rows touched by the change exercised (`NO_COLOR=1`, `| cat`, `TERM=dumb`).
3. `make eval-replay` green (RU-pinned after L1).
4. Wizard/installer changes: piped-protocol E2E in isolated `$HOME` re-run.
5. Single focused commit per PR; this roadmap's status column updated in the same PR.
6. **No stacked PRs in this repo.** CI fires only on base=`main` PRs, and the #91 incident
   proved the merge-order hazard: the child merged into its base 14 s after the base merged
   to main → MERGED label, commit unreachable from main (restacked as #92). If a stack is
   unavoidable: merge bottom-up child-first, then verify with
   `git merge-base --is-ancestor <sha> origin/main`.

## E. Corp-validation items (require the perimeter)

| # | Item | Track |
|---|------|-------|
| C1 | EN extraction quality vs RU baseline (replay corpus + one live day) | L2 |
| C2 | Token palette on Terminal.app 256-color (visual pass of wizard + T4 footer) | T4 |
| C3 | EN digest rendering in corp Mattermost (webhook, emoji, markdown) | L1 follow-up |
| C4 | U2 intra-stage telemetry on a real run (paging counters, retry warming, `stage_health` read-out) + U4 reader over a real digest (Cyrillic subjects/authors, truncation) — runbook §9.4 | U2/U4 |
| C5 | U7 `actionpulse explain` against a real failed/partial run (verdict quality on real telemetry; one extra gateway call) — runbook §9.4 | U7 |
