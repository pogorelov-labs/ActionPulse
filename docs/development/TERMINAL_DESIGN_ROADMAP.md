# Terminal Design — Integration Map & Implementation Roadmap

Companion to [`TERMINAL_DESIGN.md`](TERMINAL_DESIGN.md) (the rules). This document is
(A) the **integration map** — where the rules bind to the development workflow so they
cannot silently rot, and (B) the **execution roadmap** — the PR-by-PR plan to bring every
surface to compliance, including the **English-by-default language program**.

**Both tracks complete (2026-06-12, PRs #90–#98):** L1 + T1–T7 shipped; remaining work lives in L2/L3 (corp validation + docs translation) and the fleet-lane rendering that activates with REDESIGN PR2.

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
