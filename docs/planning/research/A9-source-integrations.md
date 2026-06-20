# A9 — Source breadth: the next ingestion source

> **Deliverable for** [`RESEARCH_SPEC.md`](../RESEARCH_SPEC.md) item **A9** · draft v1 · 2026-06-20 · internal.
> **Question:** after EWS email + self-hosted Mattermost, which *next* read-only source
> adds the most action-item value while staying on-prem / no-egress feasible?
> **Companions:** [`A8-positioning.md`](A8-positioning.md) (the air-gap wedge this defends),
> [`MATTERMOST_INTEGRATION.md`](../MATTERMOST_INTEGRATION.md).
> **Verify-before-external-use:** API specifics below are pinned to official 2024–2026
> docs (URLs inline) but vendor on-prem timelines move — re-check the dated lifecycle
> claims (esp. Atlassian Data Center 2026/2028/2029, EWS-online 2026/2027) before any
> roadmap commitment. Confidence is flagged per claim.

---

## 1. TL;DR

**Add Exchange/EWS *calendar* next.** It is the only candidate that is both **high action-value**
and **near-zero-cost on-prem**: it rides the *exact* endpoint, auth, client, and throttling
budget the tool already uses for mail (`/EWS/Exchange.asmx`, Kerberos/NTLM) — it's "add a
Calendar-folder adapter," not a new integration — and it surfaces a signal class email + chat
structurally lack: **what you're committed to today + decisions you owe** (un-RSVP'd meeting
requests, conflicts). The on-prem EWS path carries **no deprecation risk** in the relevant
horizon (the Oct 2026 / Apr 2027 EWS shutdown is **Exchange Online only**; Microsoft says
verbatim *"there are no changes to EWS in Exchange Server"*).

**Then Jira Data Center**, the *richest explicit* action source (a ticket *is* an action — assignee,
status, due date, sprint, @mention, blocker are structured fields, not inferred prose), on-prem
via REST + Bearer PAT. The catch is strategic, not technical: Atlassian has put **Data Center
itself on a sunset path** (no new sales after **30 Mar 2026**, end-of-life **28 Mar 2029**), so
it's a strong *medium-term* bet for *existing* DC tenants, not a forever platform.

**Avoid Slack and Microsoft Teams** for the air-gap product: both are **cloud-only**. Reading
either *requires* egress to `slack.com` / `graph.microsoft.com` — a categorical dealbreaker for a
no-egress tool. (Mattermost, already supported, *is* the self-hostable Slack/Teams equivalent.)

**Shared docs (SharePoint SE / Confluence DC) are feasible on-prem but lowest value** — docs are
reference/knowledge, not time-bound asks; action density is thin. Rank them last despite OK APIs.

**Ranked (action-value × on-prem feasibility):**
**1. EWS calendar** · **2. Jira Data Center** · 3. Confluence Data Center · 4. SharePoint Server SE · *(disqualified: Teams, Slack — cloud-only)*.

---

## 2. Source comparison

| Source | On-prem read API? | Auth (on-prem) | Rate limits | Action-value | Feasibility for air-gap |
|---|---|---|---|---|---|
| **Exchange/EWS calendar** | ✅ Yes — same EWS the tool uses (`FindItem`+`CalendarView`, `GetItem`) | Kerberos / NTLM (same as mail). *No* on-prem OAuth — HMA needs the cloud | EWS throttling policy (`EWSMaxConcurrency`, token-bucket); daily read is trivially within it | **High** — today's meetings, **un-RSVP'd requests**, conflicts, declines: a signal mail/chat lack | **Highest — "free"**: same endpoint/auth/client/budget; just another folder |
| **Jira Data Center** | ✅ Yes — REST `/rest/api/2/search` (JQL), `/issue`, `/comment` | **PAT (Bearer)** since 8.14 (2020); Basic; OAuth 1.0a | Admin-configurable per-user; → 429; often unthrottled by default | **Highest (explicit)** — assignee, status, due date, sprint, @mention, blocker are *structured fields* | **High now, sunsetting**: on-prem + revocable PAT, but no new DC sales after 30 Mar 2026, EOL 28 Mar 2029 |
| **Confluence Data Center** | ✅ Yes — `/rest/api/content`, `/child/comment`, `/search?cql=` | **PAT (Bearer)** 7.9+; Basic | Per-user (DC); GET-only read | **Low** — inline tasks (`mention=currentUser()`), @mentions; mostly reference | **OK**: on-prem + PAT, but same Atlassian EOL (28 Mar 2029) |
| **SharePoint Server SE** | ✅ Yes — `_api/web/lists/...`, `_api/search/query`, CSOM | **NTLM / Kerberos** (Windows auth). S2S high-trust possible; ACS & Graph are cloud — avoid | OData/REST; daily read trivial | **Lowest** — no native mention→action feed; recently-changed docs only | **OK**: on-prem; longest runway (SE "In Support", ~2035 guarantee) but weakest signal |
| **Microsoft Teams** | ❌ **No on-prem Teams** — Graph at `graph.microsoft.com` only | Entra (Azure AD) OAuth 2.0 + admin consent | Graph throttling, 429 + `Retry-After` | (High *if* reachable: @mentions, chats) | ❌ **Dealbreaker — cloud-only egress.** (Metering removed 25 Aug 2025; egress, not cost, is the blocker) |
| **Slack** | ❌ **No on-prem Slack** — `slack.com` Web API only | OAuth 2.0 (bot/user tokens) | **2025 clamp**: non-Marketplace `conversations.history` → 1 req/min, 15 objects | (High *if* reachable: @mentions, DMs, asks) | ❌ **Dealbreaker — cloud-only egress.** EKM/residency ≠ self-hosting |

---

## 3. Findings

### 3.1 Calendar — Exchange/EWS (winner) vs Google Calendar (cloud-only)

**EWS calendar reads are native, read-only, and essentially free given the tool already speaks EWS.**
Microsoft documents retrieving appointments via `CalendarFolder.FindAppointments` / the `FindItem`
operation with a **`CalendarView`** (start/end window), then `GetItem` for detail — all read ops on
the well-known `calendar` distinguished folder.
[learn.microsoft.com — Get appointments and meetings by using EWS](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/how-to-get-appointments-and-meetings-by-using-ews-in-exchange)
The load-bearing nuance: recurring occurrences live as attachments on a master, so a plain
`FindItem` won't expand them — you **must** use `CalendarView` (Microsoft's explicit
recommendation). *(Confidence: HIGH.)* Architecturally this is the **same** `/EWS/Exchange.asmx`
endpoint, **same** auth, **same** `ExchangeService` client, **same** throttling budget the tool
already lives within — `DistinguishedFolderId="calendar"` instead of `"inbox"`. No new credential,
no new network path, no new egress. *(Confidence: HIGH — strongest single finding.)*

**Auth on-prem = Kerberos (preferred) or NTLM.** Microsoft's EWS auth doc lists *"OAuth 2.0
(Exchange Online only) · NTLM (Exchange on-premises only) · Basic (no longer recommended)"* and
states *"OAuth authentication for EWS is only available in Exchange Online."* On-prem OAuth exists
only via Hybrid Modern Auth, which reaches into Entra ID and is therefore **incompatible with a true
air-gap**. Whatever auth the tool uses for the inbox is exactly what calendar uses.
[learn.microsoft.com — Authentication and EWS](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/authentication-and-ews-in-exchange) *(Confidence: HIGH.)*

**Throttling:** governed by a per-user policy (`EWSMaxConcurrency` default 10/27; `EWSFindCountLimit`
default 1000; token-bucket on 2013+). Microsoft deliberately omits canonical defaults because
on-prem admins can change them — but a once-daily, low-concurrency, paged read is comfortably within
any sane policy; respect paging + `BackOffMilliseconds` on `ErrorServerBusy`.
[learn.microsoft.com — EWS throttling](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/ews-throttling-in-exchange) *(Confidence: HIGH mechanism / MEDIUM exact numbers — admin-tunable.)*

**Deprecation — on-prem is safe.** The EWS retirement is **Exchange Online only**: *"October 2026:
EWS starts to be disabled… April 2027: EWS is fully disabled"* — title literally "Deprecation of
EWS in **Exchange Online**".
[learn.microsoft.com — Deprecation of EWS in Exchange Online](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-ews-exchange-online)
Microsoft's announcement (MC676299) says verbatim *"there are no changes to EWS in Exchange Server"*.
[mc.merill.net/message/MC676299](https://mc.merill.net/message/MC676299) — corroborated independently
([office365itpros](https://office365itpros.com/2026/02/06/ews-retirement-may-2027/),
[bleepingcomputer](https://www.bleepingcomputer.com/news/microsoft/microsoft-to-shut-down-exchange-web-services-in-cloud-in-2027/)).
*(Confidence: HIGH — three independent source families + Microsoft's verbatim sentence.)* Caveat:
on-prem EWS is *frozen-but-supported* (Graph is the cloud future; there is no on-prem Graph) — fine
for reading calendars, don't bet *new* features on it.

**Action-value (concrete):** un-RSVP'd meeting requests (`CalendarItem.MyResponseType ==
NoResponseReceived`, or `MeetingRequest` items in the inbox) → textbook `my_actions`/`urgent`;
today's/upcoming meetings (`CalendarView` over `[now, now+horizon]`); conflicts (overlap, computed
client-side) → `urgent`; declines/cancellations (`IsCancelled`, attendee `ResponseType`). Maps
cleanly onto the tool's existing `my_actions`/`urgent`/`fyi` sections, and every item carries a
stable `ItemId` + folder for P2 evidence-tracing. Privacy: prefer subject/time/location/RSVP-state
over full bodies/attendee lists (same redaction discipline as the DM-body guardrail).

**Google Calendar is the cloud-only foil.** Clean read-only API (`calendar.events.readonly`,
service account + domain-wide delegation, 600 req/min/user
— [scopes](https://developers.google.com/workspace/calendar/api/auth),
[quota](https://developers.google.com/workspace/calendar/api/guides/quota)) **but Google Workspace
is cloud-only SaaS** — data-region controls choose a Google *cloud* region, not self-hosting
([Data Regions](https://workspace.google.com/products/admin/data-regions/)). Any read = egress to
`googleapis.com`. Relevant only to a future cloud/Workspace variant — out of scope for the
air-gap build. *(Confidence: HIGH.)*

### 3.2 Jira — Data Center (on-prem, richest explicit actions) vs Cloud (dealbreaker)

**Jira Data Center has a stable read REST API and is fully air-gap-compatible today.** Read issues,
comments, JQL search via `GET /rest/api/2/issue/{key}`, `/comment`, `GET|POST /rest/api/2/search`;
`expand=changelog` for status history; offset pagination.
[developer.atlassian.com — Jira REST API examples](https://developer.atlassian.com/server/jira/platform/jira-rest-api-examples/),
[DC REST v10002](https://developer.atlassian.com/server/jira/platform/rest/v10002/api-group-search/).
Note: the `/rest/api/2|3/search` removal (deadline 1 Aug 2025, → `/search/jql`) is **Jira Cloud
only** — DC keeps the classic endpoint.
[adaptavist](https://docs.adaptavist.com/sr4jc/latest/release-notes/breaking-changes/atlassian-rest-api-search-endpoints-deprecation) *(Confidence: HIGH.)*

**Auth:** **Personal Access Tokens (Bearer)** — introduced Jira 8.14 (26 Nov 2020), `Authorization:
Bearer <token>`, inherit the creator's permissions (scope to a read-only service account), admin-
revocable/expirable. Same PAT pattern as the existing Mattermost integration.
[confluence.atlassian.com — Using PATs](https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html),
[developer.atlassian.com — PAT](https://developer.atlassian.com/server/jira/platform/personal-access-token/) *(Confidence: HIGH.)*

**Rate limiting** is a DC admin feature (System → Rate limiting; per-user, per-node; → 429;
allow-listable). Not necessarily on by default, and a once-daily JQL page-through is negligible.
[confluence.atlassian.com — Rate limiting](https://confluence.atlassian.com/adminjiraserver/improving-instance-stability-with-rate-limiting-983794911.html) *(Confidence: HIGH mechanism / MEDIUM default-on + intro version.)*

**Lifecycle — the strategic catch.** Jira *Server* hit EOL **15 Feb 2024**
([Farewell to Server](https://www.atlassian.com/blog/announcements/farewell-to-server)); **Data
Center** continued (current DC ~11.3) — **but** Atlassian's Sept-2025 "Ascend" announcement put DC
on a sunset path: **new sales end 30 Mar 2026**, existing-customer purchases end 30 Mar 2028,
**end-of-life 28 Mar 2029** (licenses become read-only).
[atlassian.com — Ascend](https://www.atlassian.com/blog/announcements/atlassian-ascend)
*(Confidence: HIGH on the three dates — official + 3 independent write-ups.)* Net: sound for an
*existing* on-prem DC tenant for the medium term; a *brand-new* org can't adopt DC at all after Mar
2026 (only Cloud).

**Action-value:** the **highest for explicit actions** — `assignee = currentUser()`, status
transitions (changelog), `duedate`, sprint fields, blockers (issue links/`flagged`), and @mentions
as parseable `mention` nodes. Cleaner/higher-precision than inferring "this is a task" from email
prose; pairs naturally with P2 (cite `issueKey` + comment id).

**Jira Cloud is the dealbreaker foil:** REST v3 at `*.atlassian.net` / `api.atlassian.com`, OAuth
2.0 (3LO), cost/points rate limits — **cloud-hosted only**, mandatory egress to Atlassian's cloud.
[developer.atlassian.com — Cloud REST v3](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/),
[rate limiting](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/). Exclude from the
air-gap product. *(Confidence: HIGH.)*

### 3.3 Teams & Slack — both cloud-only (disqualified)

**Microsoft Teams has no on-prem/self-hosted server** — *"Microsoft Teams itself does not have an
on-premise version. It is a cloud-based service that is part of Microsoft 365."*
[learn.microsoft.com Q&A](https://learn.microsoft.com/en-us/answers/questions/2151762/does-teams-server-exist)
(corroborated by the Skype-for-Business retirement narrative: Teams = cloud path; the on-prem
successor is a *different* product, SfB Server SE). All reads go to Microsoft Graph
(`/teams/{id}/channels/{id}/messages`, `/chats/{id}/messages`) at `graph.microsoft.com` — i.e.
cloud egress, an unconditional dealbreaker.
[channel messages](https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0),
[chat messages](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0).
**Recency correction:** the historical metered "protected API" model (model=A/B, $/message) was
**removed 25 Aug 2025** — so *cost* is no longer the blocker (don't cite it as one); **egress is**.
[learn.microsoft.com — teams-licenses](https://learn.microsoft.com/en-us/graph/teams-licenses)
*(Confidence: HIGH on cloud-only + metering removal; MEDIUM on exact Graph RPS — unverified this pass.)*

**Slack has no on-prem/self-hosted product** — it runs exclusively on Slack's (Salesforce/AWS)
cloud; Enterprise Grid, EKM, and data-residency are still cloud (residency picks a Slack region;
EKM keys live in AWS KMS — neither is self-hosting).
[slack.com — Data residency](https://slack.com/help/articles/360035633934-Data-residency-for-Slack),
[Slack EKM](https://slack.com/enterprise-key-management),
[secumeet review](https://secumeet.com/reviews/slack-self-hosted). All reads hit `slack.com`
(`conversations.history`/`.list`, `search.messages`) — cloud egress, dealbreaker. *(Confidence:
HIGH.)* Independently, Slack tightened non-Marketplace `conversations.history`/`.replies` to **1
req/min, 15 objects** (announced 29 May 2025; internal customer-built apps exempt at 50+/min ×
1000), which would also throttle bulk ingestion.
[docs.slack.dev — rate-limit change](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps/) *(Confidence: HIGH on the numbers / see §5 on the existing-install cutover date.)*

Both map to the same action surface as Mattermost (@mentions, DMs, channel asks) — which is exactly
why **Mattermost (already supported, open-source, air-gap-deployable) is the right home for the
"Slack/Teams-shaped" signal** in this product.
[kinsta — self-hosted Slack alternatives](https://kinsta.com/blog/slack-alternatives/).

### 3.4 Shared docs — feasible on-prem, but lowest action density

**Both SharePoint Server SE and Confluence DC have real on-prem read APIs** (so feasibility is not
the blocker — *value* is). SharePoint: REST `_api/web/lists/...` + `_api/search/query` (+ CSOM),
auth via **NTLM/Kerberos** (Windows auth; ACS/Graph are cloud — avoid); SE is on the Modern
Lifecycle, "In Support" (longest on-prem runway).
[learn.microsoft.com — SharePoint REST](https://learn.microsoft.com/en-us/sharepoint/dev/sp-add-ins/get-to-know-the-sharepoint-rest-service),
[app auth in SharePoint Server](https://learn.microsoft.com/en-us/sharepoint/security-for-sharepoint-server/plan-for-app-authentication-in-sharepoint-server),
[SE lifecycle](https://learn.microsoft.com/en-us/lifecycle/products/sharepoint-server-subscription-edition).
Confluence DC: `/rest/api/content`, `/child/comment`, `/search?cql=`, **PAT (Bearer)** auth — same
Atlassian EOL (28 Mar 2029).
[developer.atlassian.com — Confluence REST examples](https://developer.atlassian.com/server/confluence/confluence-rest-api-examples/).
*(Confidence: HIGH on APIs/auth; MEDIUM on SE "to 2035" — third-party.)*

**But docs are reference, not asks.** The only real action signals are thin and second-class:
Confluence inline tasks (`mention = currentUser()` + Task Report macro) and @mentions; SharePoint
has *no* native cross-site mention→action feed — only "recently changed docs." Yield is a handful of
items/user/day at best vs the dense, inherently-actionable mail/chat/ticket/calendar streams. If one
is ever added, **Confluence DC first** (its inline-task/@mention model gives a cleaner queryable
signal than SharePoint), but rank the whole category **last**.

---

## 4. Recommendation

**Next source: Exchange/EWS calendar.** Best value × feasibility by a wide margin. It is effectively
free (rides existing endpoint/auth/throttling/evidence plumbing — a folder adapter, not an
integration), it adds the one action class the current sources can't see (commitments-today +
decisions-owed: RSVPs, conflicts), it slots into the existing canonical sections with native
evidence IDs, and the on-prem path has **no** deprecation exposure in the planning horizon. The only
engineering nuance is `CalendarView` recurrence expansion (handled by the API) and reusing the
existing paging/backoff. Lowest-risk, highest-leverage move.

**Then: Jira Data Center** — the richest *explicit* action source, on-prem via REST + PAT (same
posture as EWS/Mattermost). Gate it on the org actually running DC, and treat it as a strong
*medium-term* bet given Atlassian's 2026/2029 DC sunset (a *new* org can't buy DC after Mar 2026).
For a bank that already runs on-prem Atlassian, this is high-value and worth doing.

**Avoid (cloud-only — structural dealbreaker for the no-egress wedge): Microsoft Teams and Slack.**
Reading either requires egress to `graph.microsoft.com` / `slack.com`; no configuration (Grid, EKM,
residency, hybrid) changes that. They're out of scope *by design*, not "blocked on licensing/cost."
The honest framing in any positioning doc: the self-hostable chat surface is **Mattermost**, which
the tool already covers.

**Deprioritize: shared docs (SharePoint SE / Confluence DC)** — feasible on-prem but lowest action
density (reference, not asks). Revisit only after the high-density sources are exhausted.

---

## 5. Open questions / low-confidence

- **Slack existing-install cutover date.** A search snippet claimed the 1-req/min limit hits
  *existing* non-Marketplace installs on **3 Mar 2026**, but a direct fetch of the official changelog
  did **not** confirm it (it states existing installs are *not yet* subject to the new limits, with
  no firm public date). Treated as **unconfirmed** — and moot anyway, since Slack is disqualified on
  cloud-only grounds. Verify the live FAQ if ever load-bearing. *(LOW confidence on the date.)*
- **EWS throttling exact defaults** are version-specific and **admin-tunable on-prem** — confirm the
  target Exchange's actual `Get-ThrottlingPolicy` before sizing; the cited defaults are Microsoft's
  documented values, not guarantees for a given farm. *(MEDIUM.)*
- **Atlassian DC dates may shift.** The 2026/2028/2029 timeline is official (Sept 2025) but Atlassian
  has moved on-prem deadlines before; re-check the live EOL page before committing roadmap. *(HIGH on
  current dates, but "watch.")*
- **Graph/Teams exact RPS** not verified this pass (medium); irrelevant given the cloud-only verdict.
- **SharePoint SE "guaranteed to 2035"** is repeated by resellers; the official page only says "In
  Support" under Modern Lifecycle — treat 2035 as a stated minimum, not an EOL. *(MEDIUM.)*
- **EWS-on-prem is frozen, not growing.** Microsoft's strategic direction is Graph, and there is no
  on-prem Graph. Reading calendars is stable today, but the on-prem API surface won't gain features —
  a longevity caveat for the whole EWS-based wedge, not a near-term blocker. *(HIGH.)*

---

## 6. Sources

**Calendar / EWS**
- https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/how-to-get-appointments-and-meetings-by-using-ews-in-exchange
- https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/authentication-and-ews-in-exchange
- https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/ews-throttling-in-exchange
- https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-ews-exchange-online
- https://mc.merill.net/message/MC676299 · https://office365itpros.com/2026/02/06/ews-retirement-may-2027/ · https://www.bleepingcomputer.com/news/microsoft/microsoft-to-shut-down-exchange-web-services-in-cloud-in-2027/
- Google Calendar: https://developers.google.com/workspace/calendar/api/auth · https://developers.google.com/workspace/calendar/api/guides/quota · https://workspace.google.com/products/admin/data-regions/

**Jira**
- https://developer.atlassian.com/server/jira/platform/jira-rest-api-examples/ · https://developer.atlassian.com/server/jira/platform/rest/v10002/api-group-search/
- https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html · https://developer.atlassian.com/server/jira/platform/personal-access-token/
- https://confluence.atlassian.com/adminjiraserver/improving-instance-stability-with-rate-limiting-983794911.html
- https://www.atlassian.com/blog/announcements/farewell-to-server · https://www.atlassian.com/blog/announcements/atlassian-ascend
- https://docs.adaptavist.com/sr4jc/latest/release-notes/breaking-changes/atlassian-rest-api-search-endpoints-deprecation
- Cloud: https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/ · https://developer.atlassian.com/cloud/jira/platform/rate-limiting/

**Teams**
- https://learn.microsoft.com/en-us/answers/questions/2151762/does-teams-server-exist
- https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0 · https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/teams-licenses (metering removed 25 Aug 2025)

**Slack**
- https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps/ · https://docs.slack.dev/changelog/2025/06/03/rate-limits-clarity/
- https://slack.com/help/articles/360035633934-Data-residency-for-Slack · https://slack.com/enterprise-key-management · https://secumeet.com/reviews/slack-self-hosted
- https://kinsta.com/blog/slack-alternatives/

**Shared docs**
- SharePoint: https://learn.microsoft.com/en-us/sharepoint/dev/sp-add-ins/get-to-know-the-sharepoint-rest-service · https://learn.microsoft.com/en-us/sharepoint/security-for-sharepoint-server/plan-for-app-authentication-in-sharepoint-server · https://learn.microsoft.com/en-us/sharepoint/dev/general-development/using-the-sharepoint-search-query-apis · https://learn.microsoft.com/en-us/lifecycle/products/sharepoint-server-subscription-edition
- Confluence: https://developer.atlassian.com/server/confluence/confluence-rest-api-examples/ · https://developer.atlassian.com/server/confluence/advanced-searching-using-cql/ · https://www.atlassian.com/licensing/data-center-end-of-life
