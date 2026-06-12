# ActionPulse Terminal Design System

Rules for everything ActionPulse renders in a terminal: `install.sh` (bash), the setup
wizard (rich), `cli run` / `diagnose` output, and the future fleet live display
(`REDESIGN_PLAN.md`). Code-level doc → English; user-facing strings inside examples stay
Russian (product language).

**Evidence tiers** — every rule is marked:
- ★ **verified** — survived 3-vote adversarial verification against primary sources
  (deep-research run, 2026-06-11/12), or verified line-by-line against the rich 14.3.3
  wheel we ship;
- ◐ **sourced** — taken from primary sources (tool source code, official docs) by
  targeted research agents, single-pass, not adversarially verified;
- ◆ **house rule** — our decision where specs fork or evidence is absent. Change via PR
  to this file.

Provenance: deep-research workflow (24 sources, 117 claims extracted, 24/25 survived
verification) + 3 targeted source-reading agents (concurrent-task display anatomy of 9
tools; input conventions across 8 prompt libraries; rich 14.3.3 internals). Sources in §10.

---

## 1. Principles

| # | Principle | Tier |
|---|-----------|------|
| P1 | **Responsive over fast.** First output within 100 ms — print *before* slow work starts, not after. | ★ clig.dev |
| P2 | **Silence looks broken.** Anything that can exceed ~1 s shows liveness (spinner/progress). A good spinner makes a program *feel* faster — it communicates, it does not decorate. | ★ clig.dev |
| P3 | **Scrollback is sacred.** Split-region architecture: completed work is printed permanently into native scrollback; exactly **one** live footer animates below it. Never repaint history. | ★ Ink `<Static>`; cargo; indicatif |
| P4 | **Complexity legible, not noisy.** Show real counters (n/total, funnel numbers, elapsed, in-flight lanes) so the user senses the machinery — but cap lanes, collapse finished detail, and never scroll logs through the live region. | ◐ BuildKit, bazel, Claude Code |
| P5 | **Calm motion.** Every animation is throttled (§3); nothing animates that carries no information; one spinner style per product. | ★ Ink/Textual + ◆ |
| P6 | **Every frame degrades.** Full experience → 256-color → 16-color → ASCII → non-TTY append-only → CI last-frame → `TERM=dumb` plain. Each tier is designed, not accidental (§7). | ★ lipgloss ladder, clig.dev |
| P7 | **The terminal is the user's.** Restore cursor visibility, echo, and cooked mode on *every* exit path including exceptions; Ctrl+C always works and exits 130 with no traceback; never enable mouse reporting on line-oriented surfaces (§5). | ◐ prompt_toolkit/bubbletea/rich contracts |

---

## 2. Color

### 2.1 Semantic tokens

Color is referenced **only** through these tokens — never ad-hoc codes in feature code.
Implementations: bash variables in `install.sh`, a `rich.theme.Theme` for Python surfaces.

| Token | Meaning | truecolor | 256 | 16 | mono/ASCII |
|-------|---------|-----------|-----|----|-----------| 
| `pulse` | brand accent (banner, spinner, rules) | `#22d3ee→#a78bfa` gradient | `cyan`-range auto | `cyan` | plain |
| `ok` | success ✓ | terminal green | `green` | `green` | `✓`/`OK` text |
| `warn` | recoverable ⚠ | terminal yellow | `yellow` | `yellow` | `⚠`/`!` text |
| `err` | failure ✗ | terminal red | `red` | `red` | `✗`/`X` text |
| `dim` | secondary: hints, durations, paths | dim attr | dim | dim | parentheses |
| `em` | emphasis: values the user chose | bold | bold | bold | UPPER not used; plain |

Rules:
- **Color is never the sole carrier** ◆ — every state pairs a glyph/word (`✓`/`✗`/`⚠`,
  «не удалось»). This is also the mono/screen-reader story.
- **Default to terminal-palette named colors** (`green`, not `#22c55e`) for semantics ◆ —
  named ANSI colors inherit the user's theme, so they survive light *and* dark
  backgrounds without runtime background detection. Explicit RGB is allowed only for the
  brand gradient, which rich auto-downsamples. (The lipgloss `LightDark`/`AdaptiveColor`
  dual-token approach ★ is the alternative if we ever need fixed RGB semantics; default
  dark variant when detection fails.)
- **Corp constraint:** macOS Terminal.app has no truecolor — 256 max. rich detects via
  `COLORTERM`/`TERM` and downsamples RGB automatically ★ (verified: the wizard gradient
  renders as bright-cyan 16/256 codes under a pipe). Every token must look acceptable at
  256 and 16; check both before shipping a new style.

### 2.2 Environment contract

We ship rich, so the contract **is rich 14.3.3 behavior** — documented here so bash
surfaces match it ★ (verified against the installed wheel, `rich/console.py`):

| Variable | Effect | Notes |
|----------|--------|-------|
| `NO_COLOR` (non-empty) | strips **color** (not bold/dim) | wins over `FORCE_COLOR`; empty string = ignored (rich ≥14.0, per no-color.org) |
| `FORCE_COLOR` (non-empty) | forces ANSI even when piped | **gotcha: `FORCE_COLOR=0` forces color ON** (rich follows strict force-color.org, not Node/chalk). Empty string forces OFF |
| `TERM=dumb` | no color, no cursor control, fixed 80×25, Live prints final frame only | |
| `COLUMNS`/`LINES` | override detected size | digits only |
| `TTY_COMPATIBLE=0/1` | overrides terminal detection (rich ≥14.0) | checked before `FORCE_COLOR` |

- `install.sh` must honor `NO_COLOR` the same way (non-empty disables) ◆ — today it
  checks presence; acceptable, fix opportunistically.
- The spec landscape is genuinely forked ★ (force-color.org vs Node/chalk vs bixense
  precedence); **our order is rich's order** and we do not deviate per surface ◆.
- If we ever add `--color=auto|always|never`, it maps to CLICOLOR semantics and overrides
  all env vars ★ (no-color.org FAQ: flags > env).

---

## 3. Motion

| Element | Spec | Tier |
|---------|------|------|
| Spinner | **One brand spinner**: braille `dots` (⠋⠙⠹…), 80 ms/frame (12.5 fps). ASCII fallback `- \ | /`. The pulse glyph `⌁` is a *static* brand mark, never animated. | ◆ (interval ★: rich `dots` = 80 ms) |
| Live region refresh | rich `Live`: default 4 fps is our floor; busy displays may raise to 10; **30 fps is the ceiling**, never unthrottled per-event renders. | ★ Ink 30 default / Textual 60 ceiling; rich Live=4, Progress=10 |
| First paint | < 100 ms after command start (P1): print the banner/first status line before network or file work. | ★ |
| Elapsed time | Show elapsed on any unit ≥ 1 s, format `3.1с` / `1m12s`; long-wait reassurance tick every 10 s for unbounded waits («всё ещё жду EWS… 30с»). | ◐ terraform 10 s "Still creating…" |
| Attention shift | A spinner that has run > 10 s may warm its color (`pulse` → `warn`) to acknowledge "this is longer than usual" without text noise. | ◐ Claude Code amber-after-10 s |
| Flicker-free recipe | (1) overwrite, don't clear; (2) one atomic write per frame; (3) DEC 2026 synchronized output where available. rich does (1)+(2) internally ★; (3) is not emitted by rich — acceptable, do not hand-roll. | ★ McGugan |
| What never animates | non-TTY, CI, `TERM=dumb` (§7). `NO_COLOR` does **not** disable motion — it is a color contract only. | ★ clig.dev / ◆ |

Progress bars vs spinners ◆: bar only when total is honest (`n/total` units known up
front); spinner + counters otherwise. **Never a percentage for an estimated total.**

---

## 4. Live work display — the core spec

This section is the blueprint for `cli run` (today: raw structlog JSON — non-compliant)
and the fleet display (REDESIGN_PLAN PR2+). Verified substrate ★: split-region — finished
items printed permanently above, one animated footer below (Ink `<Static>`/Gatsby/Tap;
cargo's status lines above its 1-line bar; indicatif `MultiProgress::println`).

### 4.1 Layout

```
✓ INGEST     124 письма из EWS                                  (3.1с)
✓ NORMALIZE  124 → 119 после очистки                            (0.4с)
✓ THREADS    119 писем → 37 тредов                              (0.2с)
✓ EVIDENCE   37 тредов → 41 чанк (≤3000 ткн)                    (0.8с)
✓ SELECT     41 → 28 чанков в бюджете 7000 ткн                  (0.1с)
⠹ LLM        извлечение действий · попытка 1/2      1m12s · ↑6.4k ткн
  └ qwen35-397b-a17b · 1 in-flight · RPM 3/15
```

- **History lines** (permanent, one per finished stage): `✓ STAGE  funnel-числа  (длительность)`.
  The funnel numbers (`124 → 119`, `37 тредов → 41 чанк`) ARE the "sense of complexity
  under the hood" — real counts, not theater ◆.
- **Footer** (the only animated region): current stage + spinner + elapsed + stage-specific
  counters; optional lane lines beneath (§4.3). Hard cap: **8 lines** ◆ (precedents:
  bazel `--ui_actions_shown` default 8 ◐, Claude Code task list shows 5 ◐). Caps prevent
  resize artifacts (§6.3) and keep the eye on one place.
- On failure: footer line freezes into a permanent `✗ STAGE — причина` line + the last
  log lines indented below (install.sh `show_step_log_tail` is the precedent).

### 4.2 Counter vocabulary

| Counter | Format | When |
|---------|--------|------|
| Units | `n/total` (`28/41 чанков`) | total known ★ cargo |
| Funnel | `вход → выход` per stage | always — this is our signature ◆ |
| Elapsed | `(3.1с)` history / `1m12s` footer | ≥ 1 s |
| Tokens | `↑6.4k ткн` (prompt), `↓1.2k` (completion) | LLM stages ◐ Claude Code |
| Rate budget | `RPM 3/15` per model lane | fleet ◆ (15 RPM cap is product law, ADR-008) |
| Attempts | `попытка 1/2` | LLM retry ladder |
| In-stage progress | `247 messages · page 3` (footer only) | bounded producer loops — EWS paging, normalize, evidence split ◆ (U2) |
| Retries / errors | `↻2 retries` / `⚠1 error` warn suffix on the permanent line | **nonzero only** — silence means healthy; same numbers land in `run_meta.stage_health` ◆ (U2) |

### 4.3 Fleet lanes (REDESIGN_PLAN)

One line per **model lane**, not per call ◆ — "intra-model serial / cross-model parallel"
means lanes are the honest unit of parallelism:

```
⠼ FLEET      скоринг 41 чанка                      18s · 3 модели
  ├ qwen35-397b-a17b   ⠙ extract   1 in-flight · RPM 3/15 · ↑6.4k
  ├ bge-reranker       ⠸ score     28/41 · RPM 11/60
  └ e5-embeddings      ✓ done      41/41 (2.1с)
```

Completed lanes show `✓` for one refresh cycle, then collapse into the history funnel
line when the stage ends. Lane cap 4; beyond that, aggregate: `+2 модели · 7 in-flight` ◆
(cargo's name-list-in-one-line is the precedent for aggregation ◐).

### 4.4 Event seam (implementation contract)

`run.py` already records `stage_durations_ms` — rendering must subscribe, not scrape ◆:

- A `ProgressSink` protocol: `on_stage_start(name, meta)`, `on_stage_end(name, counts,
  duration_ms)`, `on_llm_attempt(model, attempt, tokens)`, `on_lane_update(lane, state)`.
- **Intra-stage events (U2)** ◆: `on_stage_progress(stage, done, total|None, unit,
  detail)` — data progress from bounded producer loops; producers emit numbers, renderers
  own wording and throttling (Live pulls state at 10 fps; PlainSink collapses to a ≥10 s
  "still running" reassurance line — terraform model ◐; per-message events for hundreds of
  messages are fine, per-token events are not). `on_stage_retry(stage, attempt, max,
  reason)` — a transient failure scheduled a retry; the footer warms **immediately**
  (error responsiveness must not wait for the 10 s attention shift) and PlainSink prints
  one warn line per retry (rare by construction). Producers never call sinks directly —
  every emission goes through the swallow-and-log `progress.emit()` helper (a broken
  renderer must never break the pipeline).
- `run.py` emits events; sinks render. structlog JSON logging is **unchanged** — logs are
  a parallel channel, never printed through the live region (P3; rich `redirect_stdout`
  exists ★ but our logs go to stderr/file by design).
- Sinks: `RichLiveSink` (TTY), `PlainSink` (non-TTY/CI: one append-only line per
  transition — the terraform model ◐), `NullSink` (`--quiet`). Selection: auto by TTY,
  override `--progress=live|plain|none` ◆ (flag name precedent: BuildKit
  `--progress=auto|tty|plain` ◐).

### 4.5 rich Live parameters (pin these) ★

`Live(get_renderable=…, refresh_per_second=10, transient=False, vertical_overflow="ellipsis")`
- `get_renderable` pull-model (Progress does the same internally);
- non-TTY: rich prints **the final frame exactly once at stop()** — our PlainSink replaces
  it before that matters, but the default is safe;
- never `vertical_overflow="visible"` — documented un-clearable;
- footer stays ≤ 8 lines ≪ terminal height, so the eraser math never meets scrollback.

---

## 5. Input

### 5.1 Posture ◆

ActionPulse stays **line-oriented Q&A** (wizard) + streaming output (run). No full-screen
alt-buffer TUI: our sessions are short, scrollback is evidence (P2 of the product:
traceability), and alt-screen discards it. Revisit only if a long-lived dashboard ships.

### 5.2 Select menus (when a question has enumerable options)

Conventions per the cross-library table ◐ (charm huh/bubbles/gum, questionary,
InquirerPy, prompt_toolkit, fzf, Textual):

| Key | Action | Basis |
|-----|--------|-------|
| `↑/↓` | move | universal |
| `j/k` | move | default in charm + questionary + prompt_toolkit widgets ◐ |
| `Enter` | confirm | universal |
| `Space` | toggle (multi-select) | universal |
| `Esc` | cancel **current question** → restores default; never exits the program | ◆ (libraries fork here; full-screen apps abort, form libs unbind — we pick "back") |
| `Ctrl+C` | abort program, exit 130, «файлы не изменены» | ★ shipped contract |
| `1–9` | quick-select | only for ≤ 9 options ◆ (opt-in in questionary/prompt_toolkit ◐) |
| type-to-filter | only for > 9 options, replaces `j/k` | the j/k-vs-filter conflict is documented ◐ questionary |

Esc at a **top-level navigation menu** (the bare `actionpulse` launcher) dismisses the
menu — normal exit 0 — because there is no outer question to fall back to; it must never
commit the highlighted action (a cancel gesture never runs something). Pass
`cancel_value` to `ui.select.choose()` for this; without it Esc restores the question's
default (wizard semantics above). Ctrl+C remains abort/130 in both contexts ◆.

Implementation note: the §5.2 keymap ships in `digest_core/ui/select.py` (no external
dependency; raw keys via `os.read(fd, 1)` — buffered `stdin.read(1)` hangs under cbreak);
questionary remains an acceptable alternative if menus outgrow it ◆.

### 5.3 Mouse ◆ (evidence-backed: do not enable)

Zero of the surveyed line-oriented prompt libraries enable mouse reporting (huh, gum,
questionary, InquirerPy, prompt_toolkit default) ◐. Mouse tracking hijacks native text
selection/copy (shift/option-click workarounds) and the scroll wheel ◐ Textual FAQ — in
an evidence-traced product, breaking copy-paste of the digest/scrollback is a real cost
for no gain on Q&A surfaces. Mouse is a full-screen-app affordance (Textual, fzf) we
don't have. **Rule: no mouse reporting on any current surface.**

### 5.4 Text input

Readline/emacs keys (`Ctrl+A/E/W/U`, `Alt+B/F`) are a de-facto universal default ◐ —
they come free with prompt_toolkit/questionary and rich's `input()`-based prompts; never
override them. Secrets: masked, confirmed twice, Enter-keeps-existing on re-runs
(shipped wizard contract ◆).

### 5.5 Exit hygiene ★/◐

- Hide-cursor must pair with guaranteed show-cursor on every exit path — rich `Live`
  does this via context manager (verified `live.py`: `stop()` restores cursor even on
  exception) ★; bash surfaces own their cleanup (`install.sh` spinner clears its line).
- User abort = exit 130, typed message, no traceback, no partial files (wizard ships
  this; every future surface inherits it) ◆.

---

## 6. Responsive layout

### 6.1 Width

- rich re-reads `os.get_terminal_size()` **per render** (no SIGWINCH handler) ★ — new
  frames adapt within one refresh tick; nothing to implement, but never cache width ◆.
- Non-TTY width fallback: 80 columns ★ (rich default 80×25). Design all output to be
  readable at 80; use full width only for tables/rules.
- Bash surfaces: no closed right-edge boxes (alignment math with Cyrillic + fallback
  glyphs is fragile) — open-left lines and rules only ◆ (shipped invariant).

### 6.2 Truncation ◐

| Content | Rule | Precedent |
|---------|------|-----------|
| Messages, subjects, URLs | end-ellipsis `…` | rich `overflow="ellipsis"`, cargo |
| File paths | tail-preserving: `…/sub/dir/file.c` (snap to `/`) | git `diff.c` |
| Crowded live footer | drop counters first, keep label + spinner; below ~15 cols suppress the bar entirely | cargo's 15-col floor |

Single `…` (U+2026), ASCII `...` fallback. Degradation order inside one line ◆:
counters → durations → funnel detail → label (the label never truncates below 12 chars).

### 6.3 Resize honesty ★

Live-region corruption on shrink is **structural** (McGugan: reflowed lines can't be
erased once scrolled past the top). Mitigations we adopt, in order: footer ≤ 8 lines;
1-column safety margin (never paint to exact width); `vertical_overflow="ellipsis"`;
accept that aggressive resizing may orphan a frame — never attempt repaint-history hacks.

---

## 7. Degradation matrix

| Capability | TTY+UTF-8+256 | TTY ASCII locale | non-TTY (pipe) | CI env | `TERM=dumb` |
|------------|---------------|------------------|----------------|--------|-------------|
| Color | tokens §2 | tokens §2 | none (rich strips ★) | none | none |
| Glyphs ✓✗⚠⌁ | unicode | `OK X ! ~` | unicode ok | unicode ok | ASCII |
| Spinner | dots 80 ms | `-\|/` | **none** | none | none |
| Live footer | ≤8 lines | same | append-only lines (PlainSink) | append-only + final summary | final frame only ★ |
| Prompts | rich/questionary | same | read stdin (scripted) | read stdin | plain `input()` |
| Detection | — | `locale charmap` | `isatty()` | `CI` env ◐ Ink | `TERM` |

Existing precedents: `install.sh` implements column 1–3 and the locale check; the wizard
implements piped-stdin scripted mode (E2E-tested answer protocol).

---

## 8. Performance budget

- One atomic write per frame ★ (rich buffers internally — do not bypass `Console`).
- Throttle table: spinner 12.5 fps · Live 4–10 fps · ceiling 30 fps (§3).
- Refresh throttling is universal practice ◐: cargo 100 ms (+500 ms initial delay —
  don't even show a bar for sub-half-second work ◆), BuildKit 100–150 ms, bazel 1 s.
- Log volume: the live channel renders **state**, never log lines; logs go to their own
  stream/file. A footer that scrolls logs is a bug (P3/P4).

---

## 9. Surface compliance map

| Surface | Status | Gap → next action |
|---------|--------|-------------------|
| `install.sh` | ✅ compliant (spinner/steps/degradation/tty-gating) | `NO_COLOR` empty-string nuance §2.2 — opportunistic |
| Setup wizard | ✅ compliant (banner, steps, validation, masked secrets, 130 contract) | select menus → §5.2 when first menu appears |
| `cli run` | ❌ **non-compliant** — prints raw structlog JSON | implement §4: ProgressSink + RichLiveSink/PlainSink + `--progress` flag (the next UX PR) |
| `cli diagnose` | ◐ plain echo with ✓/✗ glyph tokens | colorize opportunistically |
| `actionpulse` launcher menu | ✅ compliant (§5.2 selector, Esc=dismiss via `cancel_value`, Ctrl+C=130, glyph fallbacks, masked config view) | — |
| Fleet display | — (REDESIGN_PLAN PR2+) | build against §4.3 from day one |

---

## 10. Sources

**Adversarially verified (deep-research, 2026-06-11/12):** no-color.org · bixense.com/clicolors
· force-color.org · clig.dev (latency/progress/CI rules; *do not* cite it for an exact
color-degradation contract — that claim was refuted 0-3) · charmbracelet/lipgloss +
colorprofile (degradation ladder, adaptive colors) · charm.land "The Next Generation"
(structure/style separation) · textualize.io "7 things I've learned" (60 fps, flicker
trio) · contour-terminal.org + Parpart gist (DEC 2026) · vadimdemedes/ink (`<Static>`,
30 fps, CI contract) · charmbracelet/bubbletea (Elm architecture).

**Targeted source reading (2026-06-12):** cargo `progress.rs`/`job_queue` · uv
`reporters.rs` · BuildKit `progressui/display.go` · Claude Code docs (interactive-mode,
fullscreen, changelog) · opencode.ai/docs/tui · pnpm `reportProgress.ts` · npm config ·
bazel manual · terraform `hook_ui.go` · gh `iostreams.go` · indicatif MultiProgress ·
huh/bubbles/gum keymaps · questionary/InquirerPy/prompt_toolkit/fzf bindings · xterm
ctlseqs (SGR mouse) · rich 14.3.3 wheel (live.py, progress.py, console.py, text.py) ·
git `diff.c` · rich #971/#1265, indicatif #182, tqdm #783, curl #14565 (resize).

**Known gaps (honest):** screen-reader behavior with live regions — unresearched beyond
the color/glyph pairing rule; opencode in-app counters — unverified; Claude Code's "187
spinner verbs" — community lore, unverified. Open the next research round before relying
on any of these.
