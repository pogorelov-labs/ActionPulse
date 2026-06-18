# Mattermost integration — design & roadmap

**Status:** Design research. Nothing built. This document maps how to make Mattermost a first-class **source** and a richer **delivery target**, building on the validated PAT capabilities recorded in [`MATTERMOST_PAT_INTEGRATION.md`](./MATTERMOST_PAT_INTEGRATION.md). Endpoints, seam fits, and code references below have been cross-checked against the live `main` tree and against the Mattermost v4 API source-of-truth; where a claim is unproven on the corp build it is marked **confirm-on-server** and where a prior assumption was disproven it is marked **refuted** with the corrected design.

---

## 1. Where we are (validated baseline)

An inside-corp self-test on 2026-06-17 (recorded in `MATTERMOST_PAT_INTEGRATION.md`, exercised by `scripts/mm_pat_selftest.sh`) proved, for a **non-admin `system_user` PAT** (`roles=system_user`, not admin, not bot), that we can: read channel posts and threads (`GET /api/v4/channels/{id}/posts`, `GET /api/v4/posts/{id}/thread`), read without disturbing the owner's chat (a real self-DM showed `last_viewed_at` byte-identical before/after the GET — the only mark-read path is `POST /channels/members/{me}/view`, which we never call), read and write reactions (`GET/POST /api/v4/posts/{id}/reactions`), and deliver to the owner's own self-DM (`POST /api/v4/channels/direct [me,me]` → `POST /api/v4/posts`, with `post_id` captured and `DELETE` clean-up). The owner's channel scope is large: **749 Direct, 107 Private, 93 Open, 49 Group** (~998 total). The authenticated REST API is **corp-network-only** — the edge proxy 403s any external Bearer call, so all live ingest/delivery testing runs inside corp per ADR-012 ("code outside, run inside"); the incoming webhook remains the only externally reachable path. The standout property is the **read-side-effect win**: a daily REST poller reads the owner's chat *for* them without ever marking it read or firing notifications, so their unread badges and "I haven't seen this yet" state survive — something no human or channel-joining bot can offer.

Two adjacencies were re-examined and matter for the design below. The search backend was inferred to be opensearch from a public `/system/ping`, but `@handle`-search-as-mention-feed is **refuted** as unreliable regardless of backend (see §2.1). And the do-not-disturb GET property, while mechanically type-agnostic at the server, was only *observed* on a single type-D self-DM — generalizing it to ~50 private/open channels/day is **confirm-on-server** with a one-line non-destructive probe (see §6).

---

## 2. Ingestion design (MM as a source)

### Seam readiness (applies to all of §2)

The multi-source seam is genuinely ready. `SourceAdapter` is a two-member `Protocol` (`ingest/source_adapter.py:21-25`: a `name: str` and `fetch(digest_date) -> List[NormalizedMessage]`), `EWSSourceAdapter` is the reference implementation (`source_adapter.py:28-37`), and the live fetch already routes through `run_sources([EWSSourceAdapter(ingest)], ctx.digest_date, strict=True)` at `run.py:358`. A Mattermost source is therefore **one new adapter file** plus the cross-cutting plumbing in §4. There is **no digest-schema change** and **no pipeline re-architecture**.

Three cross-cutting prerequisites bind every ingestion subsection and are detailed once in §4: (a) the **mandatory markdown-safe normalization bypass** (`_normalize_messages`, `run.py:1390-1436` runs every body through `HTMLNormalizer` + `QuoteCleaner`, and `QuoteCleaner._remove_quotes_with_spans`, `quotes.py:692-732`, deletes everything from the first `>` line — which silently truncates markdown chat); (b) **honest `source_ref`** (today hardcoded `{"type":"email",...}` at `split.py:430-436`, verified in-tree); and (c) **threading `Envelope.source`** through (it is dropped today at `run.py:359` `messages_from_envelopes`). Each subsection notes where it depends on these.

`NormalizedMessage` (`ews.py:57-153`) is constructable purely by kwargs, so every MM mapping below is a field map, not a class change.

### 2.1 Mentions (highest signal)

An explicit `@`-mention of the owner — "@user can you confirm the release before 15:00?" — is the strongest "what needs my reaction today" signal in chat, on par with a direct email ask.

**How to build.** New `ingest/mattermost_source.py` adapter. Per matched post, map to `NormalizedMessage`: `msg_id = "mm:"+post.id` (namespaced so it never collides with an EWS `internet_message_id`; `evidence_id` hashing at `split.py:410-423` stays stable), `conversation_id = post.root_id or post.id` (native threading — §2.3/§4), `datetime_received` from `create_at` ms via `datetime.fromtimestamp(create_at/1000, tz=timezone.utc)`, `sender_email` = resolved author (best-effort — see risk below), `subject` = synthesized channel display name, `text_body = post.message` (raw markdown). Set `addressed_to_me` from a real MM-mention signal on the chunk (see the refuted alias hack below), and `source="mm"`.

**Detection — refuted and corrected.** The original plan to use `POST /api/v4/teams/{id}/posts/search` of `@handle` as the mention feed is **refuted**: REST posts carry no server-computed mention list (verified against the v4 `Post` schema — no `mentions` field), and `@handle` search is a username-*token* match that drops dash/short/stop-word handles and false-positives on any literal text occurrence, even on opensearch (the standard ES analyzer splits on hyphens and applies the same stop-word filter). **Do** derive mentions by **client-side parsing of `post.message`** on posts already pulled via `GET /channels/{id}/posts` (match the owner's `@handle`/display-name variants with word boundaries); the owner's canonical handle comes from `GET /api/v4/users/me` (admin-free, validated). Treat `/posts/search` only as an optional recall aid with documented blind spots, never the sole feed. **Confirm-on-server:** date-scoped search (`after:`/`before:`) honors `time_zone_offset`, must be paginated to a short page (default `per_page=60`, relevance-ordered not chronological), and has a known off-by-one (mattermost#23370) — so if search is used at all, post-filter every hit by re-checking the literal `@handle` and unit-test the day boundary against the pipeline's UTC-ms window.

**Broadcasts — refuted as a free byproduct.** `@channel`/`@all`/`@here` do notify the owner (verified in server `notification.go`), but they are *not* the owner's literal handle, so the `@handle` path never returns them, and they cannot be recovered "without a computed mention list." If wanted, treat broadcasts as a **separate, explicitly-computed recall path**: enumerate the owner's member channels, GET the window, string-match the broadcast tokens. For `@here` you cannot prove from a read that the owner was online at post time, so **drop or down-weight `@here`** rather than treating it as confirmed `addressed_to_me`. Do not collapse a mass ping into the same weight as direct addressing.

**`addressed_to_me` mechanism — refuted shortcut.** The proposed "inject the owner alias into `to_recipients` so the email `addressed_to_me` path fires" is **refuted** as a side-channel: it simultaneously flips the ranker's `user_in_to` (`select/ranker.py:184-199`), injects a synthetic `To:` line into the LLM evidence header (`gateway.py:326-335`), and adds the `+3.0` selection weight (`config.py:310`; `select/context.py:201-202`) — and still does **not** guarantee `my_actions` placement (the LLM assigns sections from the prompt taxonomy, not the flag). **Do** set `addressed_to_me=True` directly on the MM chunk from the parsed-mention/DM-to-owner signal, leave `to_recipients` honest, give MM mentions their own bucket/weight or a capped share so they cannot starve email `threads_top`/remainder under the shared budget, and make the ranker's `user_in_to` derivation source-aware.

**Author identity — refuted as guaranteed.** `GET /api/v4/users/{id}` returns a **sanitized** User; email visibility is gated by `PrivacySettings.ShowEmailAddress`, and for a non-admin PAT a hardened server returns an empty email with a 200 (silent blank, not an error). Bot authors have no human email. **Do** key author identity on stable `user_id` + `username`, treat email as optional/nullable (never drop/down-rank on a blank), and add a one-time capability probe that surfaces "email hidden by server policy" instead of presenting blanks as truth. Batch resolution via `POST /api/v4/users/ids` is **confirmed non-mutating** (a POST-as-query read; "Requires an active session but no other permissions") and must not be confused with `POST /channels/direct`, which *creates* a DM.

**User value.** Surfaces the highest-intent, most time-sensitive chat signal — someone explicitly waiting on the owner — alongside email asks, with verbatim evidence and a linkable `post_id`, instead of being lost in ~998 channels.

**Normalization bypass:** required (§4). **Dedup/threading:** `conversation_id = root_id or post.id` enters `ThreadBuilder` Strategy 1 natively (§2.3); a mention is also a channel post, so it dedups against any channel-ingest copy on `msg_id` (`select/context.py:303-309`).

### 2.2 DMs (highest signal, highest privacy bar)

A 1:1 DM ("can you approve X by EOD") is the most action-dense surface in the company and is structurally invisible to an email-only digest.

**How to build.** Same adapter. Selection must **never enumerate all 749**: list the owner's channels (`GET /api/v4/users/me/teams/{team_id}/channels` or cross-team `GET /api/v4/users/me/channels`), and **pre-gate on `Channel.last_post_at`** (an int64 ms-epoch field on the Channel object, server-maintained on every post — **confirmed** present and reachable by the non-admin PAT) to skip DMs with no activity in the window before paging posts. Per active channel, page `GET /api/v4/channels/{id}/posts` over the window (§2.3). Map: `conversation_id = channel_id`, `addressed_to_me=True` for any DM post **not authored by the owner** (a DM to you *is* addressed to you — the handle-free signal), `subject = ""` (DMs have none; `markdown.py:148-155` tolerates it), `source="mm"`. **Filter `delete_at>0` tombstones at ingest** and pick the latest `update_at` on edits.

> **Field-attribution correction:** `last_post_at` is a **Channel** field (it *is* on the channels-list response and is the right windowing signal). `last_viewed_at` is a **ChannelMember** field, is *not* on the channels-list, and is the owner's read-marker — the wrong signal for activity. Use `Channel.last_post_at` only.

**User value.** A DM asking for an approval is exactly the my-actions/urgent signal the product exists for. Even the privacy-safe minimum (the owner's own DM posts) lets the digest reflect commitments the owner themselves made in chat.

**Risks & gating.** A DM contains a counterparty's authored messages — **third-party PII** the owner has no employer-ownership basis to feed an LLM, and the repo has **no consent primitive** (the architecture defers it to Phase 4). DMs are therefore hard-defaulted **OFF** behind a dedicated consent acknowledgement, with a privacy ladder (owner's-own-posts-only → per-DM allowlist → full ingest); see §6. **Normalization bypass:** required and load-bearing (a truncated DM is silent data loss). **Threading:** `conversation_id = channel_id` groups one DM into one thread.

### 2.3 Public/Private channels (volume + noise)

Channels are lower-signal and noisier; they are opt-in at best.

**How to build.** Bound the scan with a config-driven strategy, defaulting to an explicit **allowlist** (`channel_allowlist: List[str]`, mirroring `EWSConfig.folders`, `config.py:51`). An optional "active-member" mode lists the owner's channels, pre-gates on `last_post_at`, and hard-caps `max_channels_per_run` (log on truncation; order by activity so the cap keeps the most-recent). Read is **GET-only and date-windowed client-side**: MM has **no server-side `create_at` filter** (unlike EWS `datetime_received__gte` at `ews.py:312`). Reuse `EWSIngest._get_time_window` (`ews.py:259-287`) ×1000 to `[start_ms, end_ms)`; page `?page/?per_page` (server cap 200) and **early-stop** by iterating the response's `order` array newest→oldest until `create_at < start_ms` (mirroring `ews.py:345-346`).

> **Pagination corrections (confirmed against v4 source):** the response is a `PostList` (`{posts: map, order: [ids]}`) — newest-first ordering lives in the **`order` array only**; the `posts` map is unordered, so the early-stop break must iterate `order`. The `per_page` max of 200 is **server-enforced** (the OpenAPI spec says default 60, no documented max), so the page-until-short loop is the right shape — don't rely on a 4xx for over-200. Drive pagination off page length (`< per_page ⇒ done`), **not** `has_next` (false-negative on some builds). Do **not** use `?since` as a `create_at` window: it selects posts by `update_at` (edits + soft-deleted tombstones), is capped at 1000, is mutually exclusive with page/per_page, and **leaks deleted-post tombstones to a non-admin PAT** (the plain GET path excludes `delete_at>0` unless an admin-only flag is set). Use `?since` only as a coarse change-feed cursor, then re-filter on `create_at` and drop `delete_at>0` locally.

**User value & noise control.** Most channel posts are noise. Filter system/bot posts **at the adapter** (`post.type` prefix `system_`, `post.props.from_bot`) so they never become a `NormalizedMessage`. Lean on the existing budget (`per_thread_max=3`, `max_total_chunks=20`, `config.py:302-303`) and `ContextSelector` negative patterns (`context.py:86-94`), extended with MM noise. The principled chat-noise filter is the dormant `RelevanceScorer` (`relevance.py`), but it is **corp-gated** behind PC-2 (`enable_relevance`) — do not enable here. **Normalization bypass:** required. **Threading:** native via `root_id`.

### 2.4 Call-to-action extraction (the chat prompt/schema)

**No digest-schema change.** `Item`/`Section`/`Digest` (`llm/schemas.py:39-93`) are source-neutral; `source_ref` is a free dict requiring only a `type` key (`gateway.py:840-844`); the three sections are canonical keys (`my_actions`/`urgent`/`fyi`, `assemble/labels.py`), title-independent. The verbatim-span (P2) rule and citation gate are source-agnostic.

**How to build.** Two binding points, no extra LLM call (ADR-008: one shared extractor pass over merged evidence). (1) **Evidence header**: `gateway._prepare_evidence_text` (`gateway.py:252-340`) emits a hardcoded email header (From/To/Cc/Subject/Importance/AddressedToMe). Branch on `chunk.source_ref['type']=="mm"` to render a chat header instead (Channel/Thread/`From: @handle`/PostedAt/AddressedToMe/Reactions), omitting email-only fields rather than printing `N/A`; keep the email header byte-identical (replay/eval invariance — the EP-4 spotlight-invariance note at `gateway.py:256-261` is the precedent). (2) **Prompt**: author `extract_actions.chat.{en,ru}.v1` (register in `prompt_registry.py`; select via the source flag in `_load_extract_prompt`) that keeps the **exact** JSON contract, P2 rule, and three canonical section titles, but changes only: the intro ("extract from informal chat"), the addressee rule (an `@`-mention/direct question/imperative reply-request = primary `my_actions`, no deadline needed), reaction semantics (the **owner's** `:+1:`/`:white_check_mark:` = handled → suppress/down-rank), brevity (a one-line "@user pls review PR" is a valid CTA), and chat few-shots. A new `prompt_version` is mandatory (stamped at `run.py:537`, into provenance, plus a CHANGELOG entry). Source is **server-driven, not LLM-echoed**: drive `source_ref['type']` from `message.source` at chunk creation and overwrite the LLM's echoed type from the cited chunk after return (the evidence map is in scope at `gateway.py:828-832`).

**Thread context.** Supply a bounded reply-chain preamble (root excerpt + immediate parent + target, each ≤~200 chars) inside the chat evidence block, **not** the whole thread (token budget; ADR-008). Keep the citable surface = target message only (P2 spans resolve against `text_body`), so the model cites the target, not the preamble.

**Refuted/uncertain verdicts to honor here.** Whether a dedicated chat prompt actually out-recalls the email prompt on real MM CTAs is **uncertain and untestable offline** — there is no chat-CTA gold corpus, only a structural eval (`eval/prompt_eval.py`) and an email-domain reaction-gold path (`eval/gold_set.py`). Ship the chat prompt **behind a flag as a candidate**, and decide via an inside-corp A/B (both prompts over the same chat evidence → self-DM → harvest reactions → compare), stratified by `@`-mention/thread asks, with a kill criterion if the email prompt is already within the κ noise floor. Separately, the citation gate's **weak-evidence rate on short/emoji-laden chat is uncertain** — the default `_find_offset` (`citation_gate.py:139-147`) is a verbatim `find()` + one whitespace-collapse fallback (no NFKC, no smart-quote folding, no emoji tolerance), so emoji-between-words, model typo-correction, and smart-vs-straight quotes break offset resolution more often than on prose email. **Measure** on a real message-length distribution inside corp; consider an NFKC + quote-fold + emoji-tolerant fallback tier before declaring `weak_evidence`. (The P2 *correctness* invariant itself is safe: spans resolve because the **same `text_body` object** feeds both the splitter and the citation map — not because the string is "unchanged.")

---

## 3. Delivery design (MM as target)

Make MM a richer target via an `auth_mode: webhook|api` switch on `MattermostDeliverConfig` (`config.py:252`), **webhook default**, and a sibling `MattermostApiDeliverer` that reuses `_format_digest`/`_split_message` verbatim and only swaps the transport. Branch in `_stage_deliver` (`run.py:988-1013`) on `auth_mode`; the existing try/except already degrades-not-drops, and the receipt must keep the `status`/`error` keys (`run.py:1010-1011`) so the richer dict stays backward-tolerant.

**Target options.**

| Option | Mechanism | Audience proof | Admin? | Verdict |
|---|---|---|---|---|
| **(a) Self-DM** *(recommended P0 for api mode)* | `POST /channels/direct [me,me]` → `POST /posts` | Provably the owner alone — structurally closes the D4 privacy guard (`mattermost.py:81-89`) | No | **Validated** |
| (b) Per-recipient private channel via bot | `POST /channels` (type P) → add member → post | Known membership list | **Likely yes / confirm-on-server** | **Uncertain** |
| (c) Plain bot to a shared channel | bot `POST /posts` | Channel members | Bot provisioning | Future |

> **Option (b) corrected:** the prior "needs admin" framing is itself **uncertain** — on a *default* config a `system_user` can create a private channel and, as its auto-assigned channel admin, add members; but (i) it is unvalidated on this v11.3.0 corp build (the self-test only proved the DM path) and the admin may have restricted `create_private_channel`/`manage_private_channel_members`, and (ii) the target user must already be a **team member** or the add returns "No team member found" (team-add leans admin). If (b) 403s, a **bot does not fix it** (a bot is equally non-admin); the right fallback is per-recipient `POST /channels/direct`. Gate (b) behind a live inside-corp probe.

**Chat formatting / threading.** Reuse the block-per-section structure (`mattermost.py:111-146`). `thread_mode=root_replies`: post the header block → capture its `id` → post each section as `POST /posts` with `root_id=<header id>`. **Confirmed:** a non-admin PAT that is a channel member can create threaded replies (`create_post` is a default Member permission). Two corrections: **no nested threading** — `root_id` must reference the top-level **root** (reuse the header's `id` for every reply; passing a reply's id returns 400 "Invalid RootId"); and **client collapse rendering for long bodies is confirm-on-server** (the API guarantees the relationship, not how the corp desktop/mobile client collapses a long thread — needs a visual check inside corp).

**`post_id`↔item capture for EP-15.** Extend the api-mode receipt to carry `channel_id`, `root_post_id`, and per-section `{section_key, post_id, evidence_ids}` (section identity by canonical key via `normalize_section`, never display title). Persist it into `run_meta['delivery_receipt']` (`run.py:1206-1207`) **and** a new `delivered-posts.jsonl` mirroring the `memory/ledger.py` JSONL+SHA-256+TTL pattern, keyed by item fingerprint → `{post_id, channel_id, delivered_at}`. A later EP-15 pass reads it, calls `GET /posts/{id}/reactions`, and folds acks back to `evidence_id`. **Confirm-on-server:** a non-deterministic field (`post_id`, timestamps) in the receipt must not perturb replay-determinism tests that hash artifacts.

**Reactions-as-ack.** New post per run is the **recommended default** over `update_in_place` — editing a post would destroy prior reactions/threading and break the EP-15 ack history. `update_in_place` (`PUT`/patch) stays opt-in; **confirm-on-server** whether patch preserves or clears reactions.

**`@`-escaping (MANDATORY, gap today).** No escaping helper exists in `deliver/` or `assemble/` (grep-empty). `POST /posts` parses `@handle`/`@all`/`@here` from message **text** and notifies, with no per-post opt-out, and `item.title` is emitted raw at `mattermost.py:122-132`. Add `_escape_mentions()` applied to every evidence-derived string before assembly. **Confirmed at v11.3.0 source level:** backtick code-span wrapping genuinely suppresses the server-side mention *parser* (code spans are skipped in `getExplicitMentions`), so it is real notification suppression, not just rendering — **with one hard caveat**: suppression holds only if the backticks form a *valid* CodeSpan, so the fence must be longer than any backtick run inside the quoted content (use a fence-length-aware escaper, unit-tested against adversarial content). Escaping must happen **before first send** — a delivered notification cannot be recalled (delete is soft; notifications already fired). In a self-DM the blast reaches only the owner, but escaping is non-negotiable for any channel target and is the right default everywhere.

**`auth_mode`.** Webhook stays the externally-reachable default; api is corp-only (ADR-012). Add `get_pat()`/`get_base_url()` mirroring `get_webhook_url()` (secrets via ENV only). For `delivery_target=self_dm`, the D4 audience is provably the owner — gate the standing privacy warning off.

---

## 4. Data model & cross-source architecture

The minimal change set is four narrow, well-isolated edits, **fully testable offline** via `--replay-ingest` with a synthetic `source="mm"` snapshot (no corp network needed for the data-model layer):

1. **`NormalizedMessage.source`** — add one kw-only field `source: str = "email"` (keeps every existing positional construction byte-compatible; **confirm-on-server**/unit-test that the frozen-dataclass `__init__`/`__post_init__` and `_content_sha256` are unchanged for existing snapshots). The MM adapter sets `source="mm"`.
2. **Stop dropping `Envelope.source`** — `envelopes_from_messages` tags it (`envelope.py:29`) but `messages_from_envelopes` discards it (`run.py:359`). Stamp `envelope.source` onto `message.source` at that seam (the minimal route; localizes the contract to one function).
3. **Markdown-safe normalization branch** — in `_normalize_messages` (`run.py:1390-1436`), when `source=="mm"` skip `HTMLNormalizer.html_to_text` (MM is markdown, not HTML) and skip `QuoteCleaner.clean_email_body` (which truncates at the first `>` line); apply only unicode-normalize (`html.py:332-344`) + truncate (`html.py:346`). ~6-line conditional, not a new class. The email path stays byte-identical.
4. **Authoritative `source_ref.type`** — un-hardcode `{"type":"email",...}` (`split.py:430-436`): set `source_ref['type']` from `message.source` at chunk creation, add `channel_id`/`team_id`/`post_id` keys for MM, and **overwrite the LLM's echoed type from the cited chunk** server-side (do not trust model output; the prompt rule becomes generic "copy `source_ref` from the cited block"). The renderer already prints `source_ref.get('type')` (`markdown.py:148`), so a correctly-typed item renders `Source: mm` for free.

**`Envelope.source` threading** is the keystone for (3) and (4); it also makes P2 traceability honest (item → `mm` vs `email`) and lets delivery/UI label MM items.

**Cross-source dedup — do NOT auto-merge by body hash.** A forwarded email and its MM echo have different bodies (so a body checksum won't catch them), and a topic genuinely discussed in both is *legitimately two evidence items*. Instead: (a) **namespace MM ids** as `mm:{post_id}` so the un-namespaced `msg_id` joins (`_enrich_items_from_messages` `run.py:1752`, `_content_sha256` `run.py:1314`) cannot cross-collide — mandatory, not cosmetic; (b) reuse the existing subject-normalizer + similarity + `duplicate_sources` machinery (`threads/build.py:37,330`) to **merge threads (never drop messages)** and surface "seen in email + MM". **Confirm-on-server/test:** no downstream code regex-parses `msg_id` as an InternetMessageId (the angle-bracket stripping at `ews.py:359` is email-specific).

**Threading — refuted assumption, corrected.** The claim that `conversation_id = root_id` keeps the synthesized channel-name subject out of MM grouping is **refuted on two counts.** (i) A **root post has empty `root_id`**, so `conversation_id` must be `root_id or post.id`, and mid-thread replies must resolve to the true thread root (never the reply's own id) — otherwise roots fall through to the subject-similarity path. (ii) Even when Strategy 1 wins (`build.py:202-205`), the **unconditional** `_merge_by_semantic_similarity` step (`build.py:88,271-371`) re-buckets *every* thread group by normalized subject and merges on ≥0.7 short-body similarity — so two MM roots in the same channel share the identical synthesized subject and terse bodies ("ok", "+1", "done") get wrongly merged. **To actually make the subject inert for MM you must change the threading step**, not just the normalizer: skip subject-bucketing/semantic-merge for messages that already carry a real `conversation_id` (or `source=="mm"`), feeding only `subj_`-prefixed threads into step 4. This is a source-aware branch in `build.py` — the "normalizer-only" framing is insufficient.

**Mixed-source digest shape.** One digest, source-tagged items; the assembler needs **zero changes** (`markdown.py:148` already prints the type). Keep the three canonical sections source-agnostic — do **not** add a separate "Mattermost" section (that sorts by the low-signal channel axis). MM borrows the subject slot for channel name and `@handle` for the `from` slot so the existing `msg_id`-join enrichment (`run.py:1745-1767`) works; whether `email_subject`/`email_from` generalize to `source_subject`/`source_from` is an owner call (affects artifact byte-compatibility).

**`--sources` selector.** `sources: List[str]` is plumbed end-to-end (`cli.py:92-93` → `run.py:140,1090`) but `_stage_ingest` ignores it and hardcodes EWS. Wire the adapter list from `ctx.sources`. **Critical seam correction (refuted):** do **not** globally flip `strict=False` at `run.py:358`. `strict` is per-call and gates whether an exception reaches the degradation policy (`source_adapter.py:40-65`); a blanket `False` would also swallow EWS **config errors** that today crash the run intentionally (verified: the config-vs-operational distinction lives downstream in `_is_operational_error`/`degradation_policy`, `run.py:1498-1524`, reached only via `_guard`, and is asserted load-bearing in `test_stage_ingest_seam.py:145-163`). Keep EWS strict (config errors crash) and let MM degrade: either run two `run_sources` calls (EWS `strict=True`, MM `strict=False`) and merge envelopes, or make strictness a per-adapter `required` flag.

---

## 5. Cadence & real-time

Today's cadence is entirely external: a systemd timer (`deploy/actionpulse-digest.timer`, `OnCalendar=*-*-* 08:00:00`) or cron invokes a stateless oneshot `cli run`. There is no in-process scheduler, so cadence is a deploy concern, not a runtime rearchitecture.

**Recommendation: two-track REST polling, websocket rejected.**

- **Track A (unchanged):** the once-daily full digest stays exactly as-is — the "plan my day / what happened" product.
- **Track B (additive, optional):** a separate lightweight intraday "urgent MM ping" on a tighter timer (recommend every 30–60 min, Mon–Fri work hours) via a **new thin CLI subcommand** (`cli mm-ping`), **not** the 8-stage pipeline. It reads GET-only (DMs + member channels, with the `last_post_at` pre-gate and a per-poll watermark), finds same-hour `@`-mentions/DMs by **client-side parse** (§2.1), and self-DMs a short "N items need your reply" nudge (`@`-escaped). Default **rule-based, zero LLM**; an optional one-classify-call mode stays within ADR-008 (15 RPM is a per-key budget *across* runs, each poll is its own `RateBroker` with extractor≤2). Track B must have its **own watermark + `post_id` ledger** — it must not reuse the date-keyed daily artifact path (`run.py:252-253`), or the post-ingest content-skip (`run.py:1104-1123`) would suppress repeat same-day runs.

**Websocket rejected for the owner identity.** Authing a websocket as the owner flips them perpetually **online** (an observable presence side-effect to colleagues) and — per the PAT research and MM issue #7372 — **suppresses the owner's own DM/`@mention` emails**, plus introduces a long-lived process the codebase has no shape for. REST GET is the validated presence-neutral path (a GET is not a websocket; `last_viewed_at` is untouched). If low-latency push is ever justified, it belongs to a **dedicated bot** identity, not the owner PAT, and only as a reaction-harvest/queue-for-next-batch listener that never makes its own LLM call.

**Confirm-on-server:** the non-mutation of `GET .../posts?since=...` across private/open channels at scale (only the single self-DM was observed); whether parse-based mention recall is good enough for an *urgent* alert (a miss is worse than in a daily digest); and that polling the active subset (DMs + allowlist) is operationally acceptable — the rate limiter is IP-keyed and off by default, but a multi-worker fleet sharing one egress IP could trip 429s (back off on `X-RateLimit-Reset`).

---

## 6. Privacy, consent & security (bank lens)

**Per-source ingestion ladder, not a binary toggle.** Gating is keyed off PAT scope as three independent tiers:

| Tier | Source | Default | Basis | Gate |
|---|---|---|---|---|
| LVL3 | Public/Private **channels** (allowlist) | OFF (empty allowlist) | Employer-owned work artifacts — same basis as email | Allowlist; enforce **before** any GET |
| LVL3.5 | **Mentions** (owner `@handle`) | OFF | Same as channels | `read_mentions` flag |
| LVL4 | **DMs / group-DMs** | **HARD OFF** | Third-party PII — *no* employer-ownership basis | Dedicated `dm_consent_acknowledged` ack |

**DMs are the hardest gate.** A DM/group-DM carries a counterparty's authored messages — third-party PII fed through an LLM with **no consent machinery** in the repo (deferred to Phase 4). The only defensible launch posture is **DMs default-OFF**, matching the existing architecture exit criterion "No DM content leaks into digest." The opt-in ladder is owner's-own-posts-only → per-DM allowlist → full ingest (discouraged, acknowledged). Apply a verbatim **quote cap** (`mm.max_quote_chars`, e.g. 280) to any surviving counterparty text, and classify **group-DM (type G)** as a DM (multi-party private) — a filter that only checks type D leaks group conversations.

**Minimum consent story.** Documented-basis (channels/mentions = email-equivalent) + DMs-excluded-by-default + verbatim caps + an **audit trail via `source_ref.type`** (so a reviewer can tell a DM item from a public-channel item) + the §4 `Envelope.source` threading. No `ConsentManager` is required to ship channels+mentions; DMs wait behind the explicit ack.

**Retention is essentially free — with one hole.** MM content becomes the same `digest-*.json/.md` artifacts that the shipped 7-day prune (`maintenance.prune_artifacts`, called at run end) already deletes via `RETENTION_GLOBS` — **provided** MM doesn't invent new artifact filenames outside that fixed tuple (**confirm/test**). The one un-pruned hole: `--dump-ingest` writes full raw `text_body`/`body_norm` to disk (`_serialize_message`, `run.py:1666`) and pruning is **deliberately skipped** for dump/replay (`run.py:1813`) — so a MM dump snapshot is un-pruned raw third-party content. Document dump snapshots as dev-only/gitignored/operator-deleted, and consider excluding DM content even in dump mode. Owner call: tighter `keep_days` or redact counterparty identity for MM-sourced items.

**PAT vs bot.** A personal PAT is the owner's **full `system_user` identity** over ~998 channels and **never expires** — a single leak reads everything the human can, and the config allowlist is the *only* thing gating it (so **allowlist-enforcement-before-GET is load-bearing, not advisory**). A **bot** is least-privilege (scoped at the *server* to invited channels), independently revocable/auditable, and removes the delivery `@`-mention blast surface. **Recommendation:** move **delivery to a bot** (enables clean `post_id` capture for EP-15); if **ingestion** must use the personal PAT (reactions/self-DM are owner-scoped), treat it as a break-glass credential. **Confirm-on-server:** whether server-side scope can be applied to a bot token for ingestion (drives the bot-vs-PAT decision).

**Token handling.** `MM_PAT` via **ENV only**, never YAML (Golden Rule), reusing the `get_*()` env-indirection pattern; secrets stay in `~/.config/actionpulse/env` (outside the git checkout). The log-redaction field list (`logs.py _redact_sensitive_data`) currently lacks `pat`/`bearer` — **add them**, and never put the raw token in a structured-log kwarg. The previously-exposed token must be **rotated** (prefer minting a fresh PAT on a dedicated bot account).

**The masking boundary is documented but NOT implemented — refuted.** The architecture §16 "masking boundary" (PII masked before inference; `x-redaction-policy: strict`) is **not real**: `gateway.py:485-489` sends only `Authorization: Bearer` + config headers, no redaction header, no client-side masking — so email bodies *already* reach the LLM verbatim. MM channels/mentions add **no new** boundary concern (same email-equivalent basis); **DMs do** add third-party exposure, and the only effective control is **exclude-by-default + quote caps + allowlist**, not masking. Action: correct the architecture §16 claim before any MM-source PR lands; only set a gateway redaction header if the corp gateway actually honors it (**confirm-on-server**).

**What gates each direction.** *Ingest:* the ladder above (DMs hardest). *Delivery:* the `@`-escape (mandatory both directions once chat content enters items) + audience proof (self-DM is provably owner-only; option (b)/bot need a known membership list).

---

## 7. Product/UX

**Does chat belong?** Yes — but only its **high-signal slice**. `@`-mentions of the owner and DMs-to-owner are as actionable as a direct email; general-channel chatter is noise and is opt-in at best. The product's question is "what needs my reaction today," not "what came from where."

**Signal/noise.** Filter system/bot posts at the adapter; lean on the existing budget caps; gate channels behind an allowlist. The `RelevanceScorer` is the principled filter but stays corp-gated (PC-2).

**Source labeling.** Recommend a lightweight **per-item source glyph** (e.g. `✉` email / `💬 #channel` mm) derived from `source_ref.type`, **not** a verbose "Source:" line dominating layout and **not** a dedicated section. New label keys (`source_email`, `source_mm`, `channel_word`) live in `labels.py` EN/RU tables — never inline (house rule).

**Section vs interleave.** **Interleave** MM items into the existing `my_actions`/`urgent`/`fyi` (+ unconfirmed quarantine) sections by urgency — a chat `@`-mention asking for a decision belongs next to the email that asks the same. A dedicated "Mattermost" section would re-sort by the low-signal channel axis and is rejected.

**Reaction feedback UX.** Because api-mode delivery captures a `post_id` per section, the owner can react on a digest item (`👍`/`👎`) and have that ack flow back to the evidence via the EP-15 calibration loop — impossible over the write-only webhook. Scope the harvest to the **digest post** (clean feedback), not the owner's whole channel set (noisy).

---

## 8. Phased roadmap

Dependency-ordered. Lead with the validated, low-risk delivery rail; layer ingestion by signal-then-privacy; defer real-time.

| Phase | Scope | Unlocks | Effort | Corp-gated? | Key risk / decision |
|---|---|---|---|---|---|
| **P0** | **API self-DM delivery + `post_id` capture + `@`-escape** | Threaded persistent digest; provably-private audience (closes D4); the `post_id` map every later phase needs | ~1–1.5 d, low | Live POST only (escape/format testable offline) | Fence-length-aware `@`-escaper (mandatory, no helper today); receipt stays backward-tolerant on `status`/`error` |
| **P1a** | **Data-model core** (§4 steps 1–4: `source` field, `Envelope.source` threading, markdown-safe branch, authoritative `source_ref`, `mm:` namespacing, threading-step source branch) + wire `--sources` (EWS strict / MM degrade) | Honest provenance; mixed digest renders for free; MM citations validate | ~1–2 d, medium | **No** — fully offline via `--replay-ingest` | Don't globally flip `strict=False`; subject-similarity merge must be source-branched in `build.py`, not normalizer-only |
| **P1b** | **Mentions ingest adapter + reaction-harvest (EP-15) + chat prompt v2** | The value core: chat `@`-mentions in `my_actions`/`urgent`; reactions tune extraction quality | ~1–1.5 wk, medium-high | **Yes** (search/parse + reactions GET) | Mention recall via **client parse** (search refuted); author email best-effort; A/B the chat prompt behind a flag |
| **P2** | **Channels ingest (allowlist opt-in) + CTA prompt tuning** | Project-channel actions; exploits the read-without-mark-read win | ~3–4 d incremental | Yes (precision tuning) | Noise floods buckets without aggressive addressed-only rule + caps; `?since` tombstone/edit semantics |
| **P3** | **DMs ingest (consent-gated)** | The most action-dense surface | ~2–3 d (reuses P1 plumbing) | Yes | Third-party PII, no consent primitive — hard-OFF default + ack + quote caps + at-rest tightening |
| **P4** | **Real-time** *(only if a measured need appears)* | Same-hour urgent nudge | Track B ~medium (separate command); full WS = multi-week | Yes | WS rejected for owner identity (presence + email suppression); rule-based REST poll preferred |

---

## 9. Open product decisions

These are the genuine owner calls (not effort questions):

> **RESOLVED 2026-06-18 (owner).** Source scope and field naming are decided:
> - **Ingest all three sources** — mentions + channels + DMs (not channels+mentions-only).
> - **DMs gated** — behind per-DM opt-in, a one-time consent acknowledgement, and quote caps (counterparty ingestion treated as acknowledged per-DM opt-in under employer-device policy).
> - **Field rename** — the live `Item.email_subject`/`email_from` generalize to **`source_subject`/`source_from`** (this PR); old artifacts stay readable via pydantic validation aliases.
> - **Ingestion auth** — via the **personal PAT** (config-scoped break-glass), not a server-scoped bot.
>
> **Re-sequenced roadmap:** rename now (this PR) → mentions adapter → channels adapter → DMs + consent. The three source adapters are corp-validation-gated.

1. **Which sources to ingest, and in what order** — *resolved:* all three (mentions + channels + DMs), in that order, DMs gated. Channel default stays allowlist-empty (recommended).
2. **Bot vs personal PAT** — delivery should move to a bot (enables `post_id` capture, least privilege); *resolved for ingestion:* break-glass personal PAT (config-only scoping).
3. **Consent basis for DMs** — *resolved:* per-DM opt-in + one-time consent acknowledgement + quote caps; counterparty ingestion acknowledged per-DM under employer-device policy.
4. **Cadence** — daily-only vs add Track B intraday nudges; if Track B, rule-based (recommended v1) vs one-classify-call, and the poll cadence/work-hours window.
5. **Mixed-digest UX** — interleave by urgency (recommended) vs a dedicated Chat section; per-item glyph (recommended) vs a verbose source line. *Field-naming resolved:* `email_subject`/`email_from` → `source_subject`/`source_from` (this PR; back-compat aliases keep old artifacts readable).
6. **Build order** — does MM-as-source ship before or after MM-as-richer-delivery? They are independent (api-delivery P0 vs ingest P1), but `@`-escaping is required either way once chat content enters items.

---

### Appendix: verdict ledger (what this design must respect)

| Item | Verdict | Design consequence |
|---|---|---|
| `GET .../posts` pagination: `page/per_page` (server-cap 200), newest-first in `order` array, ms-epoch `create_at`, no date filter | **Confirmed** | Client-side window + early-stop on `order`; page-until-short, not `has_next` |
| `POST /users/ids` non-mutating; distinct from `POST /channels/direct` (creates DM) | **Confirmed** | Safe batch author resolution; never POST `/channels/direct` on read |
| GET leaves `last_viewed_at` untouched (do-not-disturb) | **Confirmed** (mechanism + 1 self-DM); scale = **confirm-on-server** | One-line non-destructive probe on a real P + O channel before the poller |
| `Channel.last_post_at` usable as activity pre-gate, non-admin reachable | **Confirmed** (corrects "ChannelMember/`last_viewed_at`") | Pre-gate on `Channel.last_post_at`; never `last_viewed_at` |
| `?since` returns edits + soft-deleted tombstones; not a `create_at` window | **Confirmed** | `?since` only a coarse cursor; drop `delete_at>0`; re-filter `create_at` |
| `POST /users/{id}` email always resolvable for non-admin PAT | **Refuted** | Key on `user_id`+`username`; email optional/nullable |
| `@handle` search = reliable mention feed (opensearch) | **Refuted** | Client-side parse of `post.message`; search = optional aid only |
| Broadcasts surface free via `@handle` path | **Refuted** | Separate enumeration path; down-weight/drop `@here` |
| Owner-alias-in-`to_recipients` = clean `addressed_to_me` switch | **Refuted** | Set flag directly on MM chunk; source-aware ranker; own bucket/cap |
| `conversation_id=root_id` keeps synthesized subject out of grouping | **Refuted** | `root_id or post.id`; source-branch the `build.py` semantic-merge step |
| Global `strict=False` preserves EWS crash-on-config-error | **Refuted** | Per-adapter strictness; EWS strict, MM degrade |
| Backtick-wrap suppresses server-side mention parser | **Confirmed** (fence-length caveat) | Fence-length-aware escaper, before first send |
| `root_id` renders a threaded reply | **Confirmed** (no nested; client collapse = confirm-on-server) | Reuse header `id` for all replies; visual check inside corp |
| Option (b) private-channel-via-bot needs admin | **Uncertain** (likely works default; build-specific) | Live probe; fallback is per-recipient DM, not a bot |
| Markdown-safe normalize ⇒ "string identical so `find()` validates" | **Refuted** (rationale, not safety) | P2 holds because same `text_body` feeds both stages; add NFKC/quote/emoji-tolerant fallback |
| Chat prompt out-recalls email prompt; weak-evidence rate acceptable | **Uncertain** (untestable offline) | Ship behind flag; inside-corp A/B + measured weak-rate, kill criterion |
