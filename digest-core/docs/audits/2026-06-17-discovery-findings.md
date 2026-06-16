# Discovery findings — ActionPulse digest-core

> Status: **DISCOVERY REGISTER** — research output, not a change plan. No code, no backlog rows
> were written. Every row below is either offline-reproduced, anchored to a `file:line`, or a concrete
> user scenario; corp-only claims are marked **[corp]**.
> Generated: 2026-06-17 · Base commit: `0e8d590` (origin/main, through PR #115) · Worktree:
> `docs/discovery-findings-2026-06-17` · Author: discovery session (principal-eng + product lens)

Sibling to the F1–F12 frontier audit / `ENHANCEMENT_PROGRAM.md` / `BACKLOG.md`. This register is
**strictly complementary**: it hunts what a pipeline-quality lens structurally cannot see — concrete
bugs nobody graded for, renderer/seam architecture debt, the **product/UX of the bytes a recipient
actually reads**, and privacy/scope inversions. Anything already in EP-1..15 / D1..D7 / F1..F12 is
treated as KNOWN and excluded (see the per-row "vs backlog" note; e.g. `recall_floor=0.0` is **not**
here — it is frontier F3 + EP-15).

Method: read both renderers + the live prompt + schemas against `origin/main`; rendered a synthetic
RU/EN digest through the **real** `MattermostDeliverer._format_digest` and `MarkdownAssembler` and
critiqued the actual bytes; three scoped sub-agents reproduced track-A/B/D claims with throwaway
fixtures. All examples here are synthetic; real corp artifacts are referenced by path/field/count only.

Severity legend: **blocker** · **major** · **minor** · **opportunity** (UX/product, no "wrong" behavior).

---

## TRACK A — Correctness bugs

| id | sev | finding | evidence / repro | why it matters | vs backlog |
|---|---|---|---|---|---|
| A1 | major | **`digest_date` computed in UTC, ignoring `time.user_timezone` (default Europe/Moscow) and `time.runner_tz`.** A run between local midnight and the UTC rollover stamps the **previous** day. | `run.py` `_resolve_digest_date("today")` → `datetime.now(timezone.utc).strftime(...)` (~run.py:1233); config knobs exist but are never read. Repro: Moscow 01:30 on 2026-06-18 → `digest_date=2026-06-17`. | Header, output filename, **and the dedup-ledger window** are all off by a day → a whole day's mail can land under the wrong date or be re-deduped wrong. | none |
| A2 | major | **User-facing item count conflates real + quarantined items.** The MM footer `items: N` and the `.md` `total_items` sum every section, *after* `_quarantine_weak_items` appended «Не подтверждено». | `mattermost.py:114` + `_count_items` `:202`; `markdown.py:84`; quarantine append `run.py:1736/1758`. Rendered bytes: 7 confirmed + 1 quarantined → footer `items: 8`. Worst case all-weak → `items: 2` with **zero** confirmed actions. | The one summary number a busy user trusts silently inflates "real actions" by the weak count. | quarantine itself is D1; the **count side-effect** was never graded |
| A3 | major | **MM split overflow: the part-header is prepended *after* each chunk is sized**, so a chunk within ~34 chars of the limit comes back over it. | `mattermost.py:173-177` (`_split_message` sizes ≤ `max_length`, then prepends `## … — часть i/total`). Repro at MAX=50: both parts return `len=82 > 50`. | Defeats the entire splitting contract; Mattermost can reject/truncate the oversized part → silent message loss. **[corp]** whether MM hard-rejects. | none (delivery splitting ungraded) |
| A4 | major **[corp]** | **MM length measured in code points, not bytes.** RU-first product; Cyrillic is 2 bytes/char in UTF-8. | `mattermost.py:146/155/186` all use `len(str)`. Repro: a 50-codepoint Cyrillic message = 100 bytes but counts as "1 part". | If MM's `MaxPostSize` (default 16383) is enforced in bytes, a digest that "fits" can be ~2× over → rejected/truncated. | none |
| A5 | minor | **Oversized single line hard-sliced mid-token** breaks markdown links across parts. | `mattermost.py:193` `line[start:start+max_length]`. Repro: `…[link]` / `(http://…)` split → renders broken. (Multi-byte UTF-8 is **not** corrupted — disproven.) | A long item title with a link delivers as broken markup. | none |
| A6 | minor | **Empty digest delivered to MM as a bare header** with no "nothing found" line; MM and `.md` disagree. | `mattermost.py:88-124` has no empty branch; `run.py:984` delivery not gated on non-empty. Rendered bytes: MM emits only `## Дайджест действий — 2026-06-17` + `_trace … items: 0_`, while `.md` correctly prints «За период релевантных действий не найдено». | On a quiet day the recipient sees an empty-looking header that reads like a broken run, not an "all clear". | none |
| A7 | minor | **EN `↻ repaired` mislabels the dedup-repeat marker.** `seen_before` (D3 dedup repeat) renders the string keyed `repaired`; the RU value is «повтор» (correct) but the **EN value is "repaired"** (a different concept — the fleet judge-rescue). | `mattermost.py:142` `self._s['repaired']`; `labels.py:107` (`"repaired": "repaired"`) / `:163` (`"повтор"`). Live in default config (`DEFAULT_LANGUAGE="en"`). Rendered EN bytes: `↻ repaired`. | One string key serves two meanings; EN output tells the user an item was "repaired" when it's a repeat. | the marker is D3; the **EN string collision** is new |
| A8 | minor | **One corrupt ledger line discards the entire 14-day dedup history** (the whole load is in one `try`). | `memory/ledger.py:51-67`. Repro: 2 valid + 1 bad line → 0 entries loaded. Off by default (`memory.dedup_ledger=false`). | A single bad byte silently resets dedup state; "degrade-not-drop" over-applies to wipe-all. | EP-7 is the ledger; this failure mode ungraded |
| A9 | minor | **No file lock on the ledger** → concurrent runs lose updates (truncate+rewrite, last-writer-wins). | `memory/ledger.py:87-90`. Off by default; daily runs normally serial. | Two overlapping runs drop one run's fingerprints. | EP-7 ungraded for concurrency |
| A10 | minor | **`.md` word-limit truncation collapses all markdown structure into one line.** `> max_words=400` triggers `" ".join(words)`, replacing every newline with a space. | `markdown.py:222-236` (+ `:202`). Repro: a 20-item RU digest (1797 words) → headings/blank-lines/evidence-refs destroyed. Note: `_count_words` counts `##`/`**` markup as words, so the limit trips early. | The persisted artifact becomes an unreadable wall on a busy day — exactly when it's most needed. | none |

Disproven (don't re-chase): section ordering is internally consistent (`_sort_sections` run.py:1601); mid-byte UTF-8 split does not corrupt; `America/Sao_Paulo` does **not** leak into the live `Digest` (only the dead Enhanced/hierarchical paths); ledger duplicate-fingerprint lines dedup cleanly; TTL boundary `<=` is deliberate.

---

## TRACK B — Architecture downsides / smells

| id | sev | finding | evidence | why it matters | vs backlog |
|---|---|---|---|---|---|
| B1 | major | **Dual-renderer divergence has already drifted.** Two hand-synced renderers (`markdown.py` `.md` vs `mattermost.py` MM) disagree on: **confidence** (`.md` capitalized «Высокая» via `_format_confidence().capitalize()` `markdown.py:214`; MM lowercase «высокая» `mattermost.py:206`); **quarantine** (MM tags «⚠ слабое обоснование», `.md` renders the «Не подтверждено» item with **no** weak marker at all); **meta** (`.md` shows «Статистика»/«Источники», MM shows the token footer). | side-by-side rendered bytes. | The "product" has two faces that diverge per surface; every future format change must be made twice and is already out of sync. | none (renderer parity ungraded) |
| B2 | major | **~270 lines of dead Enhanced renderer.** `write_enhanced_digest`/`_generate_enhanced_markdown` (the `EnhancedDigest`/V3 path with `my_actions`/`owners`) is never called; the live path is `write_digest`→`_generate_markdown` (`run.py:957-958`). `_generate_markdown` also carries dead `isinstance(dict)` branches everywhere (digest is always a `Digest` object live). | `markdown.py:301-567` dead; `:63-122` dead dict polymorphism. | Half the renderer file is a maintenance/audit trap; "what does a digest contain" has two divergent answers. | adjacent to known "hierarchical dormant" (memory), but the **markdown enhanced renderer** is a distinct dead surface |
| B3 | major | **The canonical committed example misrepresents the product and violates a live prompt principle.** `examples/digest-2024-01-15.md` shows «**Ответственные:** Иван Иванов» — but (a) its sibling `.json` has no owners/actors field, (b) the live `_generate_markdown` never renders owners, (c) it directly contradicts the live prompt's first rule «**Не додумывай исполнителей**». | `examples/digest-2024-01-15.{md,json}`; `extract_actions.v1.txt:6`; `schemas.py` `Item` has no owners. | The one example a new contributor/stakeholder reads to learn the product shows a named executor the system is explicitly designed *not* to invent. | seed thread A/P1 — resolves: live system correct, **example is the defect** |
| B4 | major | **Multi-source adapter seam is cosmetic.** `ingest/source_adapter.py` (`run_sources`, `EWSSourceAdapter`) + `ingest/envelope.py` exist and are unit-tested, but `run.py` imports none of them and calls `EWSIngest` directly. | `run.py` never references `run_sources`/`Envelope`; only `tests/test_source_adapter.py` does. | The roadmap's entire multi-source future (mm-source) rests on an abstraction that is decorative today — "the seam exists" is misleading. | not EP-12 (fleet); product-seam ungraded |
| B8 | major | **`threading.*` YAML config is silently ignored.** `_apply_yaml_config` has no `threading` branch, so `embedding_merge` (a PC-2-gated `/v1/embeddings` egress flag) set in the wizard-written `config.yaml` has **no effect and no warning**; only `DIGEST_THREADING_*` ENV works. | `config.py:805-885` (0 hits for a threading branch); the same comment block claims this class of bug was fixed for reranker/judge/eval/extract (EP-12) — PR12a's `threading` section was missed. | A privacy-relevant egress toggle where the documented config surface and actual behavior diverge silently. | EP-12 fixed *other* sections; threading is the gap it left |
| B5 | minor | **Vestigial «Источники» section in the live `.md`** lists `### Evidence ev-00X` / `*ID: ev-00X*` — the id twice, no content/quote/link. | `markdown.py:180-196`. Rendered bytes: 8 IDs × 2 empty lines. | Pure noise inflating the artifact; reads as an unfinished feature. | none |
| B6 | minor | **Config sprawl / dead knobs.** Entire `HierarchicalConfig` (15 fields + a YAML-merge branch) has zero readers; `DegradeConfig.mode`, the `JSONAssembler` module, and `judge.min_samples_per_stratum` are dead. | `config.py:391-453,543,596`; `assemble/jsonout.py` (tests-only); `run.py:957` uses `model_dump` not `JSONAssembler`. | Wizard-surfaced knobs imply features that don't exist; misleads operators/auditors about what the system does. | "dead jsonout/degrade" partly in memory; the **15 hierarchical knobs as live config surface** is the novel angle |
| B7 | minor | **Taxonomy mismatch between labels and the live prompt.** `labels.py` defines `STATUS` and `UNCONFIRMED` keys, but the live prompt emits only «Мои действия / Срочное / К сведению»; `STATUS` is never produced, and `UNCONFIRMED`'s sort weight (3) is dead because quarantine is appended post-sort. | `labels.py:27-52` vs `extract_actions.v1.txt:12,49-52`; `run.py:1758` append. | Two of five canonical sections are unreachable in the live path — taxonomy drift between rendering and extraction. | none |
| B9 | minor | **Dangling provenance: the program cites an audit not on `main`.** `ENHANCEMENT_PROGRAM.md:5` sources itself from `docs/audits/2026-06-11-frontier-audit.md`, which does not exist on `origin/main`. | `ls docs/audits/` (only injection-threat-model, BACKLOG, ENHANCEMENT_PROGRAM, baselines). | The quality program's own foundational document is uncommitted — its file:line anchors can't be re-verified by a reader. | doc-hygiene, ungraded |

---

## TRACK C — UX / product (the under-served axis; judged as a RU recipient on a Monday)

| id | sev | finding | evidence (rendered bytes) | why it matters | vs backlog |
|---|---|---|---|---|---|
| C1 | major (opp) | **The lede is buried: «Срочное» renders *below* «Мои действия» by design.** A P1 incident due *today* sits in section 2, under routine my-actions (incl. a medium-confidence "проверить отчёт"). | `SECTION_ORDER_BY_KEY` MY_ACTIONS=0 < URGENT=1 (`labels.py:47-52`); `_sort_sections` `run.py:1601-1610`. The prompt only files a section as «Срочное» on real urgency markers (`extract_actions.v1.txt:51`), so it *is* the lede. | A once-a-day reader scanning top-down hits routine items before the genuinely time-critical one. | product-decision, ungraded |
| C2 | opp | **Confidence on every item is low-signal clutter.** The prompt drops `confidence < 0.5` (`:68,88`), so the shown band is clipped to средняя/высокая/очень высокая — a 5-level vocabulary (incl. unreachable низкая/очень низкая) over a top-half distribution, repeated on every line. | rendered bytes: «\| уверенность: …» on all 8 items; `confidence_text` buckets `labels.py:218-229`. | «уверенность: высокая» mostly decorates; it rarely discriminates and competes with the actual action text. | not the L1 design work; ungraded |
| C3 | opp | **Two independent trust signals can contradict on one item.** `confidence` is the LLM's self-rating; `weak_evidence` is the citation gate's offset-verifiable support — orthogonal. A user can see «уверенность: очень высокая \| ⚠ слабое обоснование». | `schemas.py:64-69` (gate fields) vs prompt confidence; `mattermost.py:104,139`. | "Model is sure but evidence is weak" reads as self-contradiction and erodes trust in both signals. | none |
| C4 | major (opp) | **«Не подтверждено» is buried last *and* framed as untrustworthy.** A genuinely important but weakly-evidenced item (e.g. «требуется ваша подпись») is pushed below everything under «Не подтверждено / ⚠ слабое обоснование». | quarantine appended last (`run.py:1758`); rendered bytes show it after Status. | The D1 "withhold, never drop" choice can hide the very item that needed a human look; will it be read? | quarantine is D1; **whether burial serves the user** is the unasked product question |
| C5 | minor (opp) | **Operator metadata leaks into the user surface.** Every item carries `↳ ev: ev-001 \| [json](out/digest-2026-06-17.json#ev-001)`; the `[json]` link is a **local operator filesystem path** (`json_path=str(ctx.json_path)`, `run.py:985`) — dead for the recipient and a path/filename leak. | rendered bytes; `run.py:982-987`. | The recipient sees internal ids + a non-functional link to a file on the runner host. | P2 traceability is intended; **user-vs-operator audience** is the open question |
| C6 | opp | **No date salience.** «срок: 2026-06-17» (today) renders identically to a future date; raw ISO only, no «сегодня/завтра/просрочено». | rendered bytes (`срок: 2026-06-17` vs `2026-06-18` look identical); `mattermost.py:101`. | The reader can't scan "what's due today" — the highest-value question a daily digest should answer. | none |
| C7 | minor | **Numbered/bulleted inconsistency + per-section restart.** Мои действия/Срочное use `1. 2. 3.`; К сведению/Статус use `-`; numbering restarts each section (multiple "1."). | `mattermost.py:106`. | Mild visual inconsistency; "item 3" is ambiguous across sections. | none |
| C8 | opp | **Operator telemetry in the user footer** (`_trace … \| llm: 2 calls, 5123/16384 tok (31%)_`). D6 *deliberately* surfaced the llm budget; flag here only the **audience** question (a recipient is not an operator) and that the same footer carries the wrong `items` count (A2). | `mattermost.py:113-122`; D6. | Revisit whether token budgets + trace ids belong in the human-facing message. | **acknowledged D6** — surfaced, not re-litigated |

---

## TRACK D — Missed angles / inversions (skeptical staff-eng · bank IB/privacy auditor)

| id | sev | finding | evidence | why it matters | vs backlog |
|---|---|---|---|---|---|
| D1 | **blocker** (privacy) | **PDn-at-rest with no automatic retention.** Every run persists `out/digest-*.{json,md}` containing verbatim corp **subjects**, **sender display name + address** (`email_from`, U4 enrichment), verbatim **body quotes**, and ≤200-char body **previews** — indefinitely. Cleanup is 100% manual/opt-in (`actionpulse clean`, default keep 14d *if* ever run). | `run.py:957-958`; `schemas.py:50` (`email_from`), `:53` (spans/quote), `Citation.preview:20`; `maintenance.py:30` (`DEFAULT_KEEP_DAYS=14`, no auto-prune). Gitignored, but plaintext on disk. | An unattended workstation accumulates an unbounded, searchable corpus of third-party corp correspondence — the exact opposite of the stated "privacy-by-not-storing". Headline. | F8/EP-7 cover the *ledger* (hashes only); **the payload-bearing `out/` artifacts are orthogonal and ungraded** |
| D2 | major | **"≤7-day retention" is enforced nowhere, and the only retention numbers in code are 14 days.** `ARCHITECTURE.md` asserts «≤7 days (configurable)»; dedup TTL = 14 (`config.py:691`) and `clean` default = 14 (`maintenance.py:30`); neither auto-prunes `out/`. | as above + doc claim. | A stated IB control with no enforcing code and a documented value 2× the promise — a control-vs-implementation gap an auditor will flag. | none |
| D3 | opp (decision) | **Daily-batch cadence vs "what needs my reaction TODAY".** A P1 incident escalation surfaced once a day is already stale; same-hour urgent items don't fit batch delivery. | the «Срочное» taxonomy (`extract_actions.v1.txt:51`) admits time-critical items into a once-daily artifact. | The product's core promise ("today's actions") is in tension with its delivery model for the most urgent slice. | product-decision, ungraded |
| D4 | major (decision/privacy) | **Delivery has no per-recipient privacy binding.** A deeply personal action digest (derived from one mailbox) is POSTed to a single incoming webhook; if that webhook targets a shared channel, one user's personal actions/derived-titles are exposed to the channel audience. Single webhook, single user are baked in. | `config.py` one `get_webhook_url`; no recipient/identity binding; Agent-C delta #21/#22. | "My actions" delivered to a place others can read is a real IB exposure that depends entirely on operator config, with no guardrail. | multi-user is a documented Phase-4 cut; the **privacy of the current single-channel model** is the unasked question |
| D5 | minor | **No overflow cap at volume.** A huge day splits into many MM parts, each a separate POST → channel spam; no daily cap / "and N more" summarization for MM. | `mattermost.py:_split_message`; contrast `.md` `and_more_items`. | At 10× volume the delivery degrades to a wall of messages. | none |

---

## TOP THREADS TO DIG INTO (ranked by value ÷ effort)

1. **D1 — PDn-at-rest in `out/` artifacts** (blocker, privacy). Verbatim subjects/senders/quotes persisted indefinitely, manual-only cleanup. Highest value; the single most consequential thing the quality lens missed. Low-effort to *confirm scope*, medium to remediate.
2. **D2 — ≤7-day retention unenforced + doc overstates compliance** (major). Pairs with D1; a defensible auto-prune + a 7-day default closes both. Low effort.
3. **A1 — UTC `digest_date` off-by-one** (major). Mis-dates digests + corrupts the dedup window near midnight; one-line tz fix, high blast radius. Very low effort.
4. **A2 — item-count conflates real + quarantined** (major). The headline number lies whenever the gate fires; trivial to fix once decided. Low effort.
5. **C1 — lede buried (Срочное below Мои действия)** (major opp). A product call about section order; near-zero code, high user impact.
6. **B1 — dual-renderer divergence already drifted** (major). Confidence/quarantine/meta differ per surface; decide a canonical renderer before it drifts further. Medium effort.
7. **B3 — stale canonical example violates «не додумывай исполнителей»** (major). The reference doc actively teaches the wrong product; regenerate from the live path. Very low effort.
8. **A3 + A4 — MM split overflow + byte-vs-codepoint** (major). Silent message loss on large/Cyrillic digests; needs a byte-aware splitter and a header-budget. Low-medium effort, **[corp]** to confirm MM limits.
9. **A6 — empty-digest MM bare header** (minor, high polish value). A quiet day looks broken; one branch. Trivial.
10. **C5 + C8 — operator metadata (`ev:` ids, dead `[json]` link, token footer) in the user message** (minor/opp). A trust + leak cleanup gated on one product call about audience.
11. **B4 — cosmetic multi-source seam** (major, architecture). Decide: wire `run.py` through `run_sources` (commit to the abstraction) or delete it (admit email-only). Medium.
12. **C2 + C3 + C4 — confidence clutter, contradictory trust signals, buried quarantine** (opp). The trust-presentation cluster; needs an owner UX pass, not code first.

### Hand-off — how these slot into the enhancement-program waves (planning only)

These are **complementary to EP-1..15**, not extensions of them. A natural slotting, mirroring the
program's "offline-verifiable first" rule: a new **privacy/retention wave** should carry D1+D2 (auto-prune
`out/`, 7-day default, reconcile the doc) — it is offline-verifiable and arguably outranks several open
W3/W4 items for a bank deployment. A **correctness-hardening batch** (all offline, no flags) absorbs
A1/A2/A3/A4/A5/A6/A7/A10 — each is a single-surface fix with a before/after fixture, exactly the W1
character. A **renderer-debt batch** (W2-style refactor with a parity test) takes B1/B2/B5/B3 — collapse to
one canonical renderer, delete the dead Enhanced path, regenerate the example. **Architecture decisions**
(B4 seam, B6 config prune, B8 threading-YAML) are independent cleanups. The **track-C/D product items**
(C1–C4, C8, D3, D4) are **decision-gated** — they belong in DECISIONS-NEEDED below, not in a build wave,
because they change what the product *is*, not whether it's correct. Nothing here should be seeded as a
backlog row until the owner triages the product decisions, per the program's own sequencing discipline.

---

## PRODUCT DECISIONS NEEDED (owner's call — surfaced, not resolved)

1. **Retention policy & artifact persistence (D1/D2).** What is the real target — 7 days, 14, or
   privacy-by-not-storing-at-all? Should `out/` digests auto-prune (and the doc be corrected to match)?
   This is the load-bearing IB decision.
2. **Delivery privacy model (D4).** Is webhook→channel acceptable for a personal digest, or does
   delivery need per-recipient/DM binding and a guardrail against shared-channel exposure?
3. **Cadence (D3).** Does once-daily batch fit "what needs my reaction today", or is a same-hour
   urgent path needed for the «Срочное» slice?
4. **Section order (C1).** Should «Срочное» lead over «Мои действия»? (Today it's the reverse, by weight.)
5. **Confidence exposure (C2/C3).** Expose confidence at all? At 5 levels? How to avoid the
   model-confidence-vs-evidence-support contradiction (C3)?
6. **Quarantine presentation (C4).** Keep «Не подтверждено» buried last, surface it differently
   (e.g. a count + "review these"), or suppress — given it can hide an important item?
7. **User-facing operator metadata (C5/C8).** Should `ev:` ids, the local-path `[json]` link, the
   trace id, and the token budget appear in the human message at all? (D6 deliberately surfaced the
   llm budget — this revisits the *audience*, not that decision.)
8. **Multi-source seam (B4).** Commit to the abstraction (wire it now) or delete it (admit email-only
   until mm-source is greenlit)? The current decorative state is the worst of both.
