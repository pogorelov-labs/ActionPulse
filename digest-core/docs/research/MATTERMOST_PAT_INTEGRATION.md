# Mattermost PAT integration — capabilities, identity model, and read side-effects

> **Status: research, not yet built.** This is a decision-grade reference for a possible Mattermost Personal Access Token (PAT) integration (read source + delivery). It reflects documentary/source-level verification against the Mattermost v4 API reference and the v11.3.0 server source, plus the verified ActionPulse code paths. Items the verdicts could not confirm on the *specific* corp instance are marked **confirm on the live server**. Nothing here was tested by exercising a token — and the prototype PAT recorded in the 2026-06-17 study **was pasted in chat and is exposed; it MUST be rotated/revoked before any use** (§7).

---

## 1. TL;DR

The single governing fact: **a PAT acts AS its owning user.** It is "a token tied to your account that functions like a session token" — every read, post, channel-create, or membership change made with it carries the owner's identity, permissions, and blast radius. There is no sandbox; the only scoping is the owner's own membership.

**The headline read side-effect answer — reading does NOT clear unread:** fetching a day's posts with plain `GET` requests does **not** advance the owner's read marker, does **not** clear unread/mention badges, and does **not** clear push. Unread is derived server-side from the `ChannelMember` record (`last_viewed_at` / `msg_count` / `mention_count`), and the **only** call that advances it is the explicit `POST /api/v4/channels/members/{user_id}/view` (ViewChannel) — which ActionPulse must **never** call. (Verified at the v4-reference and v11.3.0-source level; not empirically run on the corp build — see §3, §8.)

**The real hazards are not the GETs — they are three other things:**

1. **An always-on user websocket** (`/api/v4/websocket` authed with the PAT) flips the owner to **online** and keeps them there, which silently **suppresses the owner's own email notifications** for DMs/@mentions (email is gated on ~5-min-away). Push suppression is *not* default on v11.3 and depends on the owner's own setting — see §4. Plain REST GETs do **not** open a websocket and do **not** touch presence, so a **REST-only poller avoids this entirely.**
2. **Posting content that contains `@handles`** — the digest quotes evidence text, and `@username`/`@all`/`@here`/`@channel` are parsed out of the *message body* at post time and notify people. Quoted content like `"ping @ivan before EOD"` will ping a real Ivan (§5).
3. **Per-recipient delivery is not silent** — opening a DM or adding someone to a channel is visible to that person (§5).

**The discipline already exists in the codebase.** EWS ingest is strictly read-only — it queries with `folder.filter()` and reads `is_flagged`, with **zero** mutating calls (`ews.py:321` `folder.filter(...)`, `ews.py:446-448` `is_flagged`; grep for `.save(|mark_as_read|is_read|.delete(|.move(|.send(` over `ingest/` returns **NONE**). The only persistent write is the local sync-state watermark file (`_update_sync_state`). MM reading must match this "do not disturb the source" contract: **GET-only, never ViewChannel, never websocket-as-user.**

---

## 2. Identity & access model (the four owner questions)

### Q1 — Can it read the owner's own private DMs?

**Yes — its own DMs and group-DMs, but no one else's.** A PAT reads any channel the owner is a member of. DMs (`type "D"`) and group-DMs (`type "G"`) are ordinary channels; the gate is membership, not a separate "DM API". Read them via `GET /api/v4/channels/{channel_id}/posts` like any channel.

**The catch (consent, not access):** the owner's own DM *contains the counterparty's authored messages*. Ingesting a DM feeds a third party's personal data through the extractor — a data-minimization/consent concern distinct from access. **Arbitrary third-party DMs (owner not a participant) are NOT readable** — not even by a System Admin via the API. Reading non-participant DMs would require Enterprise Compliance Export / Legal Hold, which **TEAM edition does not have** (verified: both features are Enterprise-gated). Caveat from the verdict: a System Admin with **raw database access** can read any DM by querying Postgres directly — but that is out of ActionPulse's trust model (the PAT has no DB access). So the backstop is precise: **no API/PAT path and no admin-UI path** to non-participant DMs on TEAM; the only exposure is raw DB, out of scope.

**Default recommendation:** exclude `type "D"`/`"G"` from ingest, or gate them behind explicit per-channel opt-in, and treat any counterparty content as third-party PII under the project's privacy-first golden rules.

### Q2 — Can it surface the owner's mentions in channels?

**Yes, but there is no clean "all my mentions" feed, and the two work-arounds are each imperfect.** v4 post objects carry no server-computed `mentions` array on a REST read; mention highlighting is client-side. Two paths exist:

- **Search** — `POST /api/v4/teams/{team_id}/posts/search` with `{"terms":"@handle"}`. It **does** respect per-user channel ACLs (private channels the searcher is not in are excluded — good for privacy). **But it is NOT a reliable mention feed** (verdict: *refuted* as "reliably returns @mention matches"): on TEAM the only search backend is the DB full-text engine (Elasticsearch is Enterprise), which treats `@` as a delimiter and matches the bare username as a word. Documented failure modes: handles **with a dash** return nothing; **1–2-char** handles are dropped by the min-length/stop-word filter; handles colliding with **stop-words** (incl. `@all`/`@here`) return nothing; and it **false-positives** on any literal mention of the username as plain text. Use it only with known blind spots.
- **Parse** — pull `GET /channels/{id}/posts` and string-match the owner's `@handle` in `post.message` yourself (within-channel fallback).
- **Better structured source (confirm):** the websocket `posted` event carries a structured `mentions` field of user IDs (no DB tokenization). This is more reliable than search but requires a websocket — see the presence caveat in §4 before adopting it.

Either way, a PAT only ever sees mentions in channels the owner already belongs to.

### Q3 — Deliver from whom?

**A PAT posts AS the owner.** A **self-DM** (channel `[me, me]`) is clean — from you, to you. **DMing a different person from the personal PAT is functional impersonation** — the recipient sees a post authored by the owner. Clean, attributable delivery to *other* people requires a **dedicated bot account** (`action-pulse`) whose posts carry a bot badge.

### Q4 — Private per-person channel?

**Works, and it closes the D4 "audience-unprovable" gap.** `POST /api/v4/channels` with `type "P"` (private) + `POST /api/v4/channels/{id}/members` makes a provably-private channel with a derivable audience (unlike an opaque webhook URL). It is persistent and reaction-harvest-friendly for EP-15. **Caveat (verified):** the creator is auto-added not just as a member but as **channel admin** (`SchemeAdmin:true`). A PAT-created per-person channel therefore always contains **the owner** in every channel. Removing the creator afterward is historically buggy/irreversible for private channels. **Fix:** have the **bot** create the channel so the *bot* (not the owner) is the auto-member, then add the target — audience becomes a provable `{bot, target}`.

### PAT vs Bot comparison

| Dimension | PAT (personal) | Bot (`action-pulse`) |
|---|---|---|
| **Reads** | Owner's DMs + group-DMs, all member channels, owner's mentions (via search/parse) — **full owner context** | Only channels it is **added to** — **cannot** read owner DMs/mentions |
| **Posts-as** | The **owner** (impersonation if to others; clean only for self-DM) | The **bot** (correct attribution, bot badge) |
| **Sees owner's private DMs** | **Yes** (owner is a member) | **No** (membership-gated; not in owner's DMs) |
| **Blast radius** | Full owner account — anything the human can read/post/delete | Scoped to bot's memberships/permissions — contained |
| **Revocable** | Owner deletes in Account Settings → Security → PATs, or admin revokes | Admin disables the bot or revokes its token (kill whole identity) |
| **Expires** | **Never** by default — long-lived high-priv secret, plan manual rotation | Token also never expires by default; bot can be disabled wholesale |

### The bot-vs-PAT READ TENSION (load-bearing)

**A bot cannot read the owner's private context.** It only sees channels it is added to, so it can never read the owner's DMs, group-DMs, or surface the owner's channel mentions. Reading the owner's own private inbox **requires the owner PAT** — there is no bot path to it. Meanwhile attributed delivery to *others* **wants a bot**. These pull in opposite directions:

- PAT-only → impersonation on multi-recipient delivery + owner sits in every per-person channel.
- Bot-only → cannot read the owner's private inbox at all.

**Recommended two-credential model:** owner **PAT = READ** adapter for the owner's MM context (fits the existing `SourceAdapter.fetch(digest_date) -> List[NormalizedMessage]` seam, same as `EWSSourceAdapter` in `ingest/source_adapter.py`); **bot token = WRITE/delivery** + per-person private-channel creation. Store both ENV-only; never cross-use.

**Config dependency (uncertain — confirm on the live server):** PATs require `ServiceSettings.EnableUserAccessTokens = true`, which **defaults to `false`**. The 2026-06-17 study reports the owner already minted a working token, which *implies* it is enabled — but that is inference, not a config read. Bot creation likewise requires `ServiceSettings.EnableBotAccountCreation`.

---

## 3. Read side-effects (does reading remove "Unread"?)

**No.** This is the core good-news answer, verified at the v4-reference + v11.3.0-source level.

### The unread model

Unread is **not a per-message flag**. It is derived server-side from the `ChannelMember` record for `(user_id, channel_id)`:

| Field | Meaning |
|---|---|
| `last_viewed_at` | epoch-ms timestamp of last channel view |
| `msg_count` | messages the member is considered to have seen |
| `mention_count` | unread @-mentions (root-scoped variants: `msg_count_root`, `mention_count_root`, `urgent_mention_count`) |

The unread badge = `channel.total_msg_count − member.msg_count`; the mention bubble = `mention_count`. **Only a write that advances these fields clears unread.** Reads cannot.

### GET is non-mutating

`getPostsForChannel`, `getPost`, `getPostThread`, and `getReactions` are documented as pure reads gated by `read_channel`, with **no** side effect on `last_viewed_at`, `msg_count`, badges, or push. This was confirmed in `posts.yaml`/`reactions.yaml` **and** in the v11.3.0 server source (`api4/post.go` `getPostsForChannel` and `api4/reaction.go` `getReactions` make only read calls — no `ViewChannel`/`UpdateLastViewedAt`/`MarkChannelsAsViewed`). It is **edition-independent** (identical handlers on TEAM and ENTERPRISE) and **token-independent** (a PAT is just the user's identity).

### The ONE call that marks read — never call it

`POST /api/v4/channels/members/{user_id}/view` (ViewChannel; `user_id` is commonly `me`, `channel_id` is in the **body**, not the path). Its own description: *"Perform all the actions involved in viewing a channel. This includes marking channels as read, clearing push notifications, and updating the active channel."* This is the web/mobile client's channel-open call. **ActionPulse must never call it** — nor `POST /users/{user_id}/posts/{post_id}/set_unread`, nor any `/threads/{thread_id}/read` or mark-team-read shortcut.

**No server-side auto-fire risk:** the plugin Hooks interface exposes only write/lifecycle hooks (`MessageHasBeenPosted`, `ChannelHasBeenCreated`, etc.) — there is **no read-triggered hook** and nothing bound to ViewChannel, so no bot/plugin can transitively fire ViewChannel in reaction to a PAT read. The only path to an unintended mark-read is **client-side**: an SDK convenience wrapper or ActionPulse's own code calling ViewChannel. The official Go client keeps `GetPostsForChannel` and `ViewChannel` as separate single-HTTP-call methods, so the risk is purely "don't call it yourself."

### No read receipts in Team edition

Read receipts / "seen by" remain an **unshipped proposal** in Mattermost (Issue #9332; MM-57158) — present in **no** edition. Reading via PAT emits **no** "seen" signal to senders. `last_viewed_at` lives on the owner's own `ChannelMember` record and is not exposed to others. The user-facing "Mark as Unread" feature rewinds `last_viewed_at`; GET reads leave it untouched, so the owner's manual unread curation survives across digest runs. **Re-verify** only if the server is upgraded or a "Read receipts" toggle appears in System Console.

### Safe vs unsafe calls

| ✅ Safe (read-only, no read-state mutation) | ⛔ Unsafe (mutates state — never during ingest) |
|---|---|
| `GET /api/v4/channels/{id}/posts` | `POST /api/v4/channels/members/{user_id}/view` (ViewChannel — clears unread/badges/push) |
| `GET /api/v4/posts/{id}` | `POST /api/v4/users/{user_id}/posts/{post_id}/set_unread` |
| `GET /api/v4/posts/{id}/thread` | `POST /api/v4/posts` (creates a post) |
| `GET /api/v4/posts/{id}/reactions` | `POST` / `DELETE /api/v4/reactions` (adds/removes a reaction as the owner) |
| `POST /api/v4/teams/{team_id}/posts/search` (read-only POST) | `POST /api/v4/channels/{id}/members` (joins a channel) |
| `GET /api/v4/users/{id}`, `GET /api/v4/users/me`, `GET /users/me/teams` | `PUT /api/v4/users/{id}/status` (sets presence) |
| `POST /api/v4/users/ids` (batch read despite POST verb — **confirm**) | `POST /api/v4/channels/direct` (**creates** a DM channel — a write; see §6) |

**Guard:** add a lint/test asserting the ingest path issues only `GET` (plus the read-only search/`users/ids` POSTs) and never `POST .../members/{user_id}/view`. Grep the fetch path for `/view`, `ViewChannel`, `markChannelAsViewed`, `mark_unread`, `last_viewed`. This is the MM analogue of EWS's no-`.save()` discipline.

---

## 4. Presence & notification side-effects

### REST is presence-safe

Plain `Authorization: Bearer <PAT>` REST GETs (posts, reactions, members) do **not** set or reset presence and do **not** reset the away timer (verified). Presence is derived from active app sessions (websocket + foreground activity) and explicit status writes — **not** from REST polling. A REST GET is not even a websocket, so it never marks the user online in the first place. An idle owner polled by a REST-only integration still transitions to Away normally. **A REST-only poller is the recommended, presence-neutral design.**

### Websocket-as-user sets the owner ONLINE

Authenticating a websocket to `/api/v4/websocket` with the PAT makes the server treat it as an active client and **set the owner online** (`status_change → online`). This was verified against the v11.3.0 source (`web_conn.go` `NewWebConn` calls `SetStatusOnline` + `UpdateLastActivityAtIfNeeded` when the session has a user). An always-on daemon = the owner appears **perpetually online**.

- **Gating caveat (confirm):** the online-set is conditional on `ServiceSettings.EnableUserStatuses` (defaults `true`). On a default v11.3.0 TEAM server the flip happens; if an admin disabled user statuses, it does not.
- The 5-minute **auto-away** timer never fires while the websocket holds the session active.

### Notification-suppression risk — the production-biting part (corrected)

The original premise "online suppresses the owner's email **and** push" was **half-refuted** (verdict). Split it:

- **Email — CONFIRMED and firm.** Mattermost sends the owner's own @mention/DM **email** only after ~5 minutes away. While the websocket holds the owner online, those emails are **suppressed** (issue #7372: "the notification code drops all messages when the user has STATUS_ONLINE"). Not user-toggleable except by disabling email entirely; identical TEAM/ENTERPRISE. **So a perpetual-online daemon would swallow the owner's real DM/@mention emails.**
- **Push — NOT suppressed by default on v11.3.** Post-10.3 the default push trigger is "Online, Away, or Offline" (PR #29142), so push fires even while online. Push is only swallowed if **this owner** explicitly set push to "Away or Offline" / "Offline". **Confirm** by reading `GET /api/v4/users/me` → `notify_props.push_status` before asserting push suppression.

**Net design rule:** do **not** open a long-lived owner-authenticated websocket as a daemon. Use REST-only polling so presence stays user-driven and notification gating is never altered. If realtime is ever truly needed, use a **dedicated bot** identity (separate user) rather than the owner's PAT.

### Manual status & typing — separate explicit mutations to avoid

- `PUT /api/v4/users/{user_id}/status` explicitly sets presence (`online`/`away`/`offline`/`dnd`) and persists until reset. The PAT could set the owner's own status. **Never call it.** `GET /users/{id}/status` and `POST /users/status/ids` are read-only and safe.
- The websocket `user_typing` action broadcasts a "typing…" indicator **and** signals activity. **Never send it** — eliminated entirely by choosing REST-only.

### The daemon caveat

A scheduled, short-lived REST run (the digest's natural shape) never establishes presence. The presence/notification hazard only materializes if the integration is restructured into a **long-running websocket daemon** — which the design should forbid.

---

## 5. Delivery footprint & the accidental-@mention hazard

Moving from the current webhook delivery to PAT/bot posting (`POST /api/v4/posts`) adds recipient-visible footprint and a real mention hazard.

### The @mention-from-text hazard (real, currently unmitigated)

Mattermost parses `@username`/`@channel`/`@here`/`@all` out of the **message body** server-side at post time and notifies matched users — **identically** for an incoming webhook post and a PAT `POST /api/v4/posts` (verified in `notification.go` `getExplicitMentionsAndKeywords`, which extracts mentions from `post.Message` regardless of how the post was created). **There is no per-post opt-out flag** — `POST /api/v4/posts` accepts only `channel_id`, `message`, `root_id`, `file_ids`, `props`, `metadata`; no `disable_notifications`. The free-form `props` bag is not consulted for mention gating.

ActionPulse currently renders `item.title` (LLM-extracted free text from email/chat evidence) verbatim into the posted text and POSTs it raw as `{"text": part}` — see `deliver/mattermost.py` (webhook `client.post(webhook_url, json=payload)` at `:99`; `item.title` rendered into the item line in `_format_digest` at `:132`). There is **zero** `@`-escaping anywhere in `deliver/mattermost.py` or the assemble layer. So a digest item titled from quoted content like `"ping @ivan before EOD"` or a forwarded `"@all please review"` **will ping real people on delivery.**

**Important scope corrections (from verdicts):**

- **DM third-party leak does NOT occur** (verdict *refuted*). Posting into a 1:1 DM whose body names a **third** user's `@username` does **not** notify that third user — the notification list is filtered against the channel's member set (the 2 DM participants), and the out-of-channel fallback early-returns for DM/Group channels. The third user is silently dropped, not pinged and not warned.
- **The real DM hazard is narrower:** quoted text containing the **DM recipient's own** `@handle`, or `@all`/`@here`/`@channel` (which in a 1:1 DM resolve to the 2 participants), **over-notifies the single recipient**.
- **Third-party leak DOES remain on the PUBLIC/PRIVATE channel path:** there, a team-member-but-not-channel-member `@mention` triggers an ephemeral "add to channel" message (visible only to the poster, not a push to the third user, but it surfaces their name).
- **`@channel`/`@here`/`@all`** always fire by default but can be disabled per-channel for Guests/Members via the `use_channel_mentions` admin permission. This never affects `@username`.

**Neutralization (apply at the render boundary so every path — title now, quote later — is covered):**

- **Preferred: correctly-fenced inline code span** — `` `@ivan` ``. Mattermost does not parse mentions inside code spans (verified at the v11.3.0 parser level: a `CodeSpan` node is not a `Text` node and never reaches the mention collector). **Caveat:** the backtick fence must be balanced and longer than any backtick run inside the quoted content, or the `@ivan` falls back to plain text and **will** mention. Pick a fence length longer than any backtick run, or escape.
- **Alternative: zero-width space after `@`** — `@​ivan`. Verified to suppress (U+200B is a `FieldsFunc` token-split boundary, severing `@` from `ivan`). **But** the ZWSP is stored verbatim in the post, survives copy/paste, and is a fragile/obscure footprint — prefer code-span wrapping.
- **Simplest: strip/replace** a leading `@` in evidence-derived text (`@` → `(at)`, or `@` → `@​` globally).
- **Make the only surviving mention deliberate:** if you want a "ping me" lead line, hard-code `@owner` constructed by ActionPulse, and neutralize `@` in *all* evidence-derived text so the only live mention is the intentional one. Unit-test the escaper against adversarial quoted content.

### DM / channel creation is visible to the recipient

- **Opening a DM** (`POST /api/v4/channels/direct`) is **silent at creation** — no system message — but the moment a post lands the recipient sees a new DM thread + unread badge + notification per their settings. This is footprint the current single-channel webhook does **not** have.
- **Adding a user to a private channel** (`POST /api/v4/channels`, then `POST /api/v4/channels/{id}/members`) is **NOT silent**: the server writes a visible system post ("X added Y to the channel"), shown even when join/leave messages are disabled (verified in `channel.go` `PostAddToChannelMessage`); the channel appears in the recipient's sidebar. (Whether a separate push fires is notification-preference-dependent — the system post itself is unconditional.)

**Guidance:** use **one stable** DM/channel per recipient, created once with explicit owner intent — never a fresh channel per run. A single persistent DM is the quietest. Never auto-add third parties to a digest channel.

### Edit / delete tombstones (corrected)

- **Editing** (`PUT /api/v4/posts/{id}` / `PATCH .../patch`) shows a visible `(edited)` marker and broadcasts `post_edited`. **Correction (verdict *refuted*):** editing **does NOT fire new @mention notifications** — Mattermost dispatches mention notifications **only at first creation**. Adding an `@handle` via an edit re-renders/links it but will **not** push/email/notify that user (official docs; issue #24845). So the "update yesterday's digest re-pings people" fear is **lower** than naive intuition — the live accidental-mention risk is at **original POST time**.
- **Deleting** (`DELETE /api/v4/posts/{id}`) is a **soft delete** (sets `delete_at`, blanks the body, broadcasts `post_deleted`); recipients may see a "(message deleted)" tombstone until refresh. **It does NOT retract already-delivered notifications** (issue #13064: a notification email already sent is delivered regardless of later deletion). **Delete is best-effort at-rest cleanup, not a recall.** Treat first delivery as irreversible disclosure. Because a delivered @mention cannot be undone, **mention neutralization must happen BEFORE send** — it is the only real control.

**`post_id` capture:** the webhook path returns **no** `post_id`, so in-place updates are impossible today — capturing `post_id` from the `POST /api/v4/posts` response at first send is one concrete reason PAT/bot posting is needed.

---

## 6. Ingest side-effects & the EWS read-only parallel

### EWS is read-only (the principle to mirror), cited

| Fact | Location |
|---|---|
| Sole query is a read via `folder.filter()` (paged), **no `.only()` projection** | `ews.py:321` (`folder.filter(filter_query)[offset:offset+page_size]`) |
| Server-side date window filter (MM has no equivalent — see below) | `ews.py:312` (`Q(datetime_received__gte=..., datetime_received__lte=...)`) |
| Reads `is_flagged` into the normalized message | `ews.py:446-448` |
| **No** mutating calls anywhere in ingest | grep `.save(\|mark_as_read\|is_read\|.delete(\|.move(\|.send(` over `ingest/` → **NONE** |
| Only persistent write is the **local** sync-state watermark file | `ews.py` `_update_sync_state` (local file write only) |
| Read seam the MM adapter must implement | `ingest/source_adapter.py` — `SourceAdapter.fetch(digest_date) -> List[NormalizedMessage]` (cf. `EWSSourceAdapter`) |
| Delivery is webhook-only today (no Bearer/PAT, write-only) | `deliver/mattermost.py:99` (`client.post(webhook_url, json=payload)`, `raise_for_status` at `:100`; ping at `:47-48`); 295 lines |

`exchangelib` corroborates the discipline: the `filter()`/`GetItem` read path never emits a `<MarkAsRead>` element, so fetching+reading an item does **not** mark it read in Exchange (server default `MarkAsRead=false`). **Confirm** against the exact corp Exchange + pinned `exchangelib` version.

**The contract for MM:** reads must be invisible (GET-only, never ViewChannel, never websocket-as-user); writes (delivery) are inherently intrusive and must be deliberate, owner-confirmed, and scoped to a single stable private target.

> **D4 audience guard — present on `main`.** The `acknowledged_private` guard **does exist**: `config.py:270` (`MattermostDeliverConfig.acknowledged_private`, default `false`) and `deliver/mattermost.py:81-83`, which emits a payload-free `mattermost_target_privacy_unconfirmed` warning before delivery whenever the operator has not confirmed the webhook targets a private audience (shipped 2026-06-17). Because a webhook URL is opaque the guard can today only **warn**; **per-recipient DM / private-channel delivery makes the audience provable, so the guard can be auto-satisfied** — cleared only for a verified `POST /channels/direct` channel or a bot-created private channel, never for an arbitrary `channel_id` (see §2 Q4 and §5).

### Operational edges for the MM read adapter

| Edge | Detail | Handling |
|---|---|---|
| **Rate limits** | **Correction (verdict *refuted*):** the default rate limiter is **DISABLED** (`EnableRateLimiter=false`). When enabled, it keys by **IP** (`VaryByRemoteAddr=true`), **not** per-user (`VaryByUser=false`); `PerSec=10`, `MaxBurst=100`, GCRA token-bucket, `429` + `X-RateLimit-*` headers. | Do **not** assume a per-user bucket. A multi-worker fleet sharing one egress IP can collectively trip a per-IP limit. Back off on observed `429` + `X-RateLimit-Reset`, not on an assumed `PerSec`. The corp gateway's 15 RPM is a **separate** concern. |
| **Pagination + time window** | MM has **no** server-side date filter (unlike EWS `datetime_received__gte`). Page with `?page/?per_page` (max 200) or the `?since={ms_epoch}` cursor. `create_at`/`update_at`/`delete_at` are int64 **milliseconds** since epoch. | Build `[start_ms, end_ms)` from the existing aware-UTC window (`_get_time_window` output × 1000). **Beware:** `?since` returns **edited/deleted** posts too — a `create_at` window and a `?since` cursor are **not** interchangeable. |
| **Edited / deleted posts (new edge class)** | Edits bump `update_at`/`edit_at`; deletes are soft tombstones (`delete_at>0`, message blanked) — not removals. Email has no analogue (an email is immutable once received). | Filter out `delete_at>0` tombstones at ingest (privacy: don't leak deleted content); pick an edit policy (prefer latest `update_at`). A `?since` cursor must account for `update_at` to catch late edits. |
| **Deactivated / deleted authors** | `post.user_id` is always present; `GET /users/{id}` for a deactivated user returns it with `delete_at>0`; a hard-deleted user may 404. | Resolve via batched **`POST /api/v4/users/ids`** (a read despite the POST verb — **confirm** non-mutating). Fall back to `user_id`/username string, mirroring `ews.py`'s empty-string-tolerant normalization (don't crash). **Do NOT** confuse this with `POST /channels/direct`, which **creates** a DM channel — a real write. |
| **ms-epoch + tz** | `create_at` is unambiguous UTC epoch-ms — the MM adapter sidesteps naive-datetime risk by converting ms → aware-UTC directly. | Do **not** route MM timestamps through a naive path expecting `fail_on_naive` to catch errors — **`fail_on_naive` is dead config** (`config.py:31`, defined but never read; `ensure_aware` at `tz.py:50` always localizes a naive dt; `to_utc` at `tz.py:95` only raises after `ensure_aware` already made it aware). |
| **Large history (TEAM)** | **Correction (verdict *confirmed* for the claim):** TEAM/unlicensed has **no** post-history cap; the ~10k cap is **license-gated** (Enterprise binary). | Always read with the `create_at` window (daily digest is bounded). Replicate the EWS short-page break to avoid unbounded paging on a first-run/backfill. |
| **401 on revocation** | A revoked/expired PAT yields `401` on every read; the EWS tenacity retry catches only `ConnectionError`/`TimeoutError`, so auth failures are **not** retried (correct). | Map `401` (revoked PAT) / `403` (lost channel membership) to a **config-error that crashes with a clear "rotate token / re-add to channel" message** — do **not** silently degrade to an empty digest. Reuse tenacity only for `5xx`/`429`/network. |

---

## 7. Security & consent must-dos

1. **Rotate the exposed token first.** The prototype PAT was pasted in chat (2026-06-17 study). Revoke it in Account Settings → Security → Personal Access Tokens (or admin-revoke) and mint a fresh one **before any use**. A PAT carries the owner's full identity and **never expires by default** — treat it as a top-tier secret with a manual rotation plan.
2. **Prefer a dedicated low-priv bot for delivery-to-others.** Use the `action-pulse` bot for all posting to people other than the owner and for per-person private-channel creation, so footprint is attributable to a service identity and the blast radius is contained. The PAT stays read-only on the owner's context.
3. **ENV-only secrets.** Both the PAT and the bot token via environment, never in YAML config (golden rule). Never log the token, webhook URL, or any post body — structured logs only.
4. **Consent before reading others' DMs/reactions.** The owner's own DMs contain a third party's authored messages; ingesting them processes third-party PII. Default to excluding `type "D"`/`"G"`; gate behind explicit opt-in and document the consent basis. Reaction harvesting (EP-15) reads other people's reactions — same consent bar.
5. **MM content rides the same retention as the rest of the pipeline.** Ingested MM post bodies are evidence and inherit the project's 7-day retention / payload-free discipline (P2 traceability, never log payloads). Delivered digest content is irreversible once sent (a delete cannot recall notifications — §5).
6. **Never call the source-mutating endpoints** (ViewChannel, status, reactions-as-owner, channel-join) from the read path — enforce with the §3 guard/lint.

---

## 8. Recommended P0 and open questions to confirm on the live server

### P0 — minimal, high-value first slice

**Self-DM API delivery + capture `post_id` + read reactions, all gated on a live `GET /api/v4/users/me` → 200.**

1. **Live gate:** `GET /api/v4/users/me` with the (rotated) PAT must return `200`. A `401/403` means the token was revoked or `EnableUserAccessTokens` is off → keep `auth_mode` with **webhook as the default fallback** so a disabled/revoked PAT degrades gracefully.
2. **Self-DM delivery:** open the `[me, me]` DM (`POST /channels/direct`) once, POST the digest via `POST /api/v4/posts`. Clean attribution (from owner, to owner), no impersonation, no third-party footprint.
3. **Capture `post_id`** from the post response (unlocks in-place update + reaction read; the webhook path cannot do this).
4. **Read reactions:** `GET /api/v4/posts/{id}/reactions` for EP-15 calibration — a pure read, no mutation.
5. Apply **@-neutralization at the render boundary** before any POST (§5) — even self-DM, since quoted `@handles` ping the named users.
6. **REST-only** — no websocket — to stay presence/notification-neutral (§4).

### Open questions — confirm on the live corp server

| # | Question | Why it matters |
|---|---|---|
| 1 | **Is `EnableUserAccessTokens = true` on this instance?** (default is `false`) | Whole integration assumes a working PAT. Confirm via `GET /users/me` → 200. **Verdict: uncertain.** |
| 2 | **Edition/version is genuinely TEAM v11.3.0?** | Backstops the "no Compliance Export / no post-history cap / DB-only search backend" claims. |
| 3 | **Do `GET /channels/{id}/posts` + `GET /posts/{id}/reactions` truly leave unread/`last_viewed_at` untouched on this build?** | The do-not-disturb guarantee. Source-confirmed but not run on the corp build — a no-op-GET-then-recheck-unread test (without exercising the prototype token) would make it airtight. |
| 4 | **Are `create_direct_channel` / channel-create / edit admin-suppressed here?** | Bot-as-creator and self-DM delivery depend on these being available; corp policy could disable them. |
| 5 | **Notification-suppression behavior:** does a connected websocket flip the owner online (is `EnableUserStatuses` on?), and what is the owner's `notify_props.push_status`? | Determines whether a (hypothetical) websocket daemon would swallow email (yes by default) and/or push (only if owner set Away/Offline). Email half firm; push half owner-config-dependent. |
| 6 | **Is `POST /api/v4/users/ids` non-mutating, and does `POST /channels/direct` create a DM if absent?** | Author resolution must not accidentally create DM channels. The `/users/ids` batch is a read; `/channels/direct` is a **write**. |
| 7 | **`?since` cursor semantics:** does it return edited (`update_at`) and deleted (`delete_at>0`, blanked body) posts? | Avoids leaking deleted-post content into the digest or missing late edits — a correctness/privacy edge. |
| 8 | **Does `posts/search` of `@handle` miss dash/short/stop-word handles on this build?** | If mentions are a feature, search is an unreliable feed — prefer the structured websocket `mentions` field or client-side parse. |
