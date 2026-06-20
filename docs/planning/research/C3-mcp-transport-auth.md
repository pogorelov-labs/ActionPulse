# C3 · MCP Transport + Auth — Research Brief

> Spec item **C3** ([`RESEARCH_SPEC.md`](../RESEARCH_SPEC.md) §3 line 350). Question: does
> ActionPulse's MCP server need non-stdio transport + an auth model, given it ships today as
> **stdio + an env-var key only**, runs **in-perimeter**, and is **privacy-first**?
> Method: web-verified against the official spec (`modelcontextprotocol.io`) and recent
> (2025–2026) sources; normative text quoted where load-bearing. Researched 2026-06-20.

## 1. TL;DR

- **Two official transports, period.** As of the current spec the protocol defines exactly
  **stdio** (local) and **Streamable HTTP** (remote). The old **HTTP+SSE** two-endpoint
  transport is **deprecated** (since 2025-03-26) and being actively removed by hosts in 2026.
  **Do not build SSE.** If/when a remote surface lands, build **Streamable HTTP**.
- **Auth is OPTIONAL — and explicitly *not* for stdio.** The spec says stdio servers
  **SHOULD NOT** do the OAuth flow and instead **retrieve credentials from the environment**.
  **ActionPulse's current stdio + env-var-key design is already spec-aligned.** No change needed
  for the local AI-CLI surface.
- **Remote = OAuth 2.1 resource server, mandatory parts.** The moment the server is reachable
  over HTTP for a non-co-located client, the spec turns several knobs to **MUST**: OAuth 2.1,
  RFC 9728 Protected Resource Metadata, PKCE/S256, RFC 8707 `resource` indicators, and
  **audience-validated tokens** (token passthrough is **forbidden**).
- **The dominant threats are not transport bugs** — they are **prompt injection / tool
  poisoning**, **confused-deputy**, **token passthrough/over-broad scopes**, and (for local)
  **untrusted-server code execution**. These hit ActionPulse *regardless* of transport.
- **Recommendation:** keep stdio + env key as the default; treat a remote surface as a separate,
  gated track. For the likely 2nd surface (**B1: a Mattermost bot**), the secure pattern is **a
  corp-side service that calls `InboxAPI` directly — not a public MCP endpoint at all.** Only if a
  genuine *remote MCP client* is required should Streamable HTTP + OAuth 2.1 be added, behind
  PC-2, bound to corp network, with read-mostly scopes.

## 2. Transports

| Transport | Spec status (current) | Use case | Auth model |
|---|---|---|---|
| **stdio** | **Standard.** Clients **SHOULD** support it whenever possible. | Local: client launches server as subprocess; newline-delimited JSON-RPC over stdin/stdout. | **None in-protocol.** Spec: stdio **SHOULD NOT** do OAuth; **retrieve credentials from the environment** (env var / OS keychain). ← *ActionPulse today.* |
| **Streamable HTTP** | **Standard.** Introduced 2025-03-26, retained in the 2025-11-25 revision; the single official remote transport. | Remote / multi-client: one HTTP endpoint, POST + GET, optional **SSE upgrade** for streaming; optional `Mcp-Session-Id`. | OAuth 2.1 flow (see §3) when protected; or bearer/API-key/custom header for simpler setups. |
| **HTTP+SSE (legacy, 2024-11-05)** | **DEPRECATED.** Two endpoints (POST + a separate SSE stream). Kept only for backwards-compat. | — (migrate off). Hosts are removing it: e.g. **Atlassian Rovo 2026-06-30**, Keboola 2026-04-01. | Header-based; superseded by Streamable HTTP. |
| **Custom** | Allowed (`MAY`), transport-agnostic. | Niche (e.g. unix domain socket / IPC for a hardened local server). | Must follow that protocol's security best practices. |

**Migration story.** Streamable HTTP **replaced** HTTP+SSE: collapse the two endpoints into one
URL that handles POST (and optionally upgrades to SSE) and GET. Most SDKs (FastMCP, the official
TS/Python SDKs, Spring AI) support it natively; the common pattern is *stdio for local dev,
Streamable HTTP for prod*, gated by an env var, sharing tool logic and differing only in
transport init.

**Where it's heading (recency flag).** The official blog *"Exploring the Future of MCP
Transports"* (2025-12-19) confirms **no new transport type** is planned — instead Streamable HTTP
gains **statelessness** (carry shared context per-request, drop sticky sessions), **session
elevation** to the app/data layer, **routing-friendly headers/paths**, and a
`/.well-known/mcp.json` **Server Card** for pre-init capability discovery. SEPs target Q1 2026,
landing in a ~mid-2026 spec. Net: betting on Streamable HTTP is safe; the *stateful session*
mechanics may change.

**Streamable HTTP security warnings (verbatim, spec §Transports).** Even before auth, a Streamable
HTTP server **MUST** validate the `Origin` header (anti DNS-rebinding); **SHOULD** bind to
`127.0.0.1` when local, not `0.0.0.0`; and **SHOULD** implement authentication for all connections.

## 3. Auth & security

### Current spec (Authorization, 2025-11-25)

- **OPTIONAL, transport-scoped.** *"Authorization is OPTIONAL… Implementations using an HTTP-based
  transport SHOULD conform… Implementations using an STDIO transport SHOULD NOT follow this
  specification, and instead retrieve credentials from the environment."* → the local CLI surface
  is correctly out of scope.
- **When HTTP + protected, these are MUSTs:**
  - Server **acts as an OAuth 2.1 resource server**; AS **MUST** implement OAuth 2.1.
  - Server **MUST** implement **RFC 9728 Protected Resource Metadata**; client **MUST** use it to
    discover the AS. Discovery via `WWW-Authenticate` on `401` *or* a `.well-known` URI.
  - Client **MUST** implement **PKCE with `S256`** and **refuse to proceed** if the AS doesn't
    advertise PKCE support.
  - Client **MUST** send **RFC 8707 `resource`** (canonical MCP server URI) on authorize + token
    requests, *even if the AS ignores it*.
  - **Token audience:** server **MUST** validate tokens were *"issued specifically for them as the
    intended audience"*; **MUST NOT** accept tokens not issued for it; **MUST NOT** accept or
    transit other tokens; tokens **MUST NOT** be in the URI; `Authorization: Bearer` on **every**
    request. Public clients **MUST** rotate refresh tokens; AS **SHOULD** issue short-lived tokens.
  - Registration: **Client ID Metadata Documents** (URL-as-client_id) is now the preferred path;
    Dynamic Client Registration is demoted to backwards-compat; pre-registration also supported.

### Top pitfalls and mitigations

| Pitfall | What it is | Mitigation |
|---|---|---|
| **Prompt injection via tool results** | Untrusted content returned by a tool carries instructions the model obeys. The #1 systemic MCP risk (Simon Willison; OWASP). | Treat **all tool output as untrusted data, not instructions**; strip instruction-like tags; tell the model returns are data; human-in-the-loop for sensitive acts. |
| **Tool poisoning / "rug pull"** | Malicious instructions hidden in tool **descriptions/schemas**, or definitions silently changed after approval. (1 empirical study: ~5.5% of 1,899 public servers showed it.) | **Pin tool definitions** (SHA-256), re-verify before use; inspect the *whole* schema; strict immutable JSON schema (`additionalProperties:false`). |
| **Confused deputy** | A proxy MCP server with a *static* client_id to a 3rd-party AS + DCR + a consent cookie lets an attacker skip consent and steal the auth code. | Proxy servers **MUST** get **per-client consent before** forwarding; exact `redirect_uri` match; secure single-use `state`; `__Host-` consent cookies. |
| **Token passthrough** | Server accepts a client token not issued *to it* and forwards it downstream. **Explicitly forbidden.** | Server **MUST NOT** accept tokens not issued for it; mint a **separate** upstream token; never forward the inbound token. |
| **Over-broad scopes** | Publishing `files:*`/`admin:*` and granting all up front → huge blast radius on token theft. | Least-privilege: minimal baseline scope, **incremental step-up** via `WWW-Authenticate scope=`; accept down-scoped tokens. |
| **Session hijacking** | Guessable/reused `Mcp-Session-Id` → impersonation or cross-server event injection. | Servers **MUST NOT** use sessions for auth and **MUST** verify every request; non-deterministic IDs; **bind `<user_id>:<session_id>`**. |
| **SSRF in discovery** | Malicious server points metadata URLs at `169.254.169.254`/internal IPs. | Block private/link-local ranges, enforce HTTPS, egress proxy, pin DNS. (Acute for an *in-perimeter* deployment — internal targets are reachable.) |
| **Local server compromise** | A malicious local server = arbitrary code execution with client privileges. | Use **stdio** to limit access to the launching client; sandbox; client **MUST** show the exact launch command + consent. |

## 4. Implications for ActionPulse

**Posture.** In-perimeter (EWS+gateway corp-only; MM reachable everywhere), privacy-first,
P2-traceable. The MCP server (`actionpulse-mcp`) re-exposes ~24 `InboxAPI` verbs over **stdio +
env-var key**, opt-in `[mcp]` extra ([`STATUS.md`](../STATUS.md) §4 surface table). **New egress
is gated on PC-2** ([`RESEARCH_SPEC.md`](../RESEARCH_SPEC.md) §8 line 469).

**Verdict on C3's question:** **No, the MCP server does not need HTTP/SSE today**, and it should
**never** add the deprecated SSE. The current stdio + env-key design is the spec's *recommended*
shape for a local server — keep it as the default.

**Decision is driven by B1 (the 2nd surface), and the choice is architectural, not just transport:**

1. **If the 2nd surface is a Mattermost bot (B1, the likely pick):** do **not** expose a public
   MCP endpoint. Run a **corp-side service that calls `InboxAPI` directly** (the same facade the
   MCP server wraps). MM auth (bot token / slash-command signing) handles identity; data never
   leaves the perimeter; no new MCP transport, no OAuth server to operate. This sidesteps the
   entire confused-deputy/token-passthrough/SSRF surface. **Recommended.**
2. **Only if a genuine *remote MCP client* must connect** (e.g. a hosted agent that speaks MCP and
   cannot run the stdio binary), add **Streamable HTTP + OAuth 2.1 as a resource server** — and
   treat it as a **new egress path requiring a PC-2 amendment** and an ADR (the metric C3 already
   names: a working HTTP prototype with an auth gate + a PC-2-consistent egress note).

**Security guardrails if a remote transport is ever built:**
- **Streamable HTTP only.** Validate `Origin`; **bind to the corp interface, never `0.0.0.0`**;
  TLS terminated corp-side; prefer keeping it **inside the perimeter / behind the VPN** rather than
  internet-exposed.
- **OAuth 2.1 resource server** with the corp IdP as the AS: RFC 9728 PRM, **audience validation
  (`resource`/RFC 8707)**, **no token passthrough**, short-lived tokens, PKCE/S256 on the client.
- **Least-privilege scopes mapped to `InboxAPI` verbs:** default to **read/search** (`retrieve`,
  `search`, `ask`, `summarize`); gate any state-changing or DM-body-touching verb behind a
  step-up scope + explicit consent. Honor the existing redaction guardrails (DM bodies redacted;
  bearer/PAT log redaction already shipped — [`STATUS.md`](../STATUS.md) §6).
- **No sessions for auth**; verify every request; `<user_id>:<session_id>` binding if sessions are
  used at all.
- **Tool-output hygiene** (applies to *both* surfaces, today): treat ingested message content
  surfaced through tools as **untrusted data**, and consider pinning the MCP tool-definition set so
  a future change is visible — this is the prompt-injection/tool-poisoning defense, and it's
  relevant now because the corpus *is* attacker-influenceable email/chat.

**One thing to do regardless of C3's outcome:** because ActionPulse's data is literally untrusted
inbound comms, the **prompt-injection / tool-output-as-data** discipline is the highest-leverage
hardening and is independent of whether a 2nd transport ever ships.

## 5. Open questions / low-confidence

- **Spec churn (medium confidence on "current").** `2025-11-25` is the latest published revision,
  but one source labels it a *release candidate*, and a **`2026-07-28` RC** is already referenced
  on the MCP blog. The **stateless-session** redesign (§2) may alter session mechanics by mid-2026.
  Transport *choice* (Streamable HTTP) is safe; **defer session/auth *implementation* detail** until
  the build moment and re-verify against the then-current spec.
- **Does any intended client even need MCP-over-HTTP?** If B1 is an MM bot and AI-CLIs run locally,
  the answer may be "never" — confirm before investing in OAuth infra.
- **Corp IdP as OAuth 2.1 AS — feasible?** Whether the bank IdP can act as an MCP-compatible AS
  (RFC 9728 PRM, PKCE discovery, RFC 8707) is unverified; gateway/network constraints (CIB limits)
  may dominate. Needs a corp-side spike.
- **Client conformance.** Ecosystem clients (Claude, ChatGPT, Cloudflare `mcp-remote`, AgentCore)
  accept Streamable HTTP, but *bearer/static-key* vs *full OAuth* support varies — verify the
  specific client before assuming the simpler path works.
- **In-perimeter SSRF.** Standard SSRF mitigations assume internal IPs are the *forbidden* targets;
  inside the corp net those are exactly what's reachable — the threat model needs a corp-specific
  pass (ties to C10 DPIA/threat model).

## 6. Sources

*Official spec (lead):*
- MCP — Transports (2025-06-18): https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- MCP — Authorization (2025-11-25): https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- MCP — Security Best Practices (2025-11-25): https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices
- MCP Blog — *Exploring the Future of MCP Transports* (2025-12-19): https://blog.modelcontextprotocol.io/posts/2025-12-19-mcp-transport-future/
- MCP — 2025-11-25 changelog: https://modelcontextprotocol.io/specification/2025-11-25/changelog
- MCP Blog — 2026-07-28 Release Candidate (recency flag): https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/

*RFCs:* RFC 9728 (Protected Resource Metadata) https://datatracker.ietf.org/doc/html/rfc9728 · RFC 8707 (Resource Indicators) https://www.rfc-editor.org/rfc/rfc8707.html · OAuth 2.1 draft https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13

*Security / ecosystem:*
- OWASP MCP Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html
- Microsoft — MCP Security Best Practices 2025: https://github.com/microsoft/mcp-for-beginners/blob/main/02-Security/mcp-security-best-practices-2025.md
- Simon Willison — MCP prompt injection: https://simonwillison.net/2025/Apr/9/mcp-prompt-injection/
- Auth0 — why MCP moved off SSE: https://auth0.com/blog/mcp-streamable-http/
- Descope — MCP auth spec deep-dive: https://www.descope.com/blog/post/mcp-auth-spec
- WorkOS — Everything about MCP in 2026: https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026
- Atlassian — HTTP+SSE deprecation notice (2026-06-30 removal): https://community.atlassian.com/forums/Atlassian-Remote-MCP-Server/HTTP-SSE-Deprecation-Notice/ba-p/3205484
- Truefoundry — stdio vs Streamable HTTP trade-offs: https://www.truefoundry.com/blog/mcp-stdio-vs-streamable-http-enterprise

*Project inputs:* [`RESEARCH_SPEC.md`](../RESEARCH_SPEC.md) (C3 §3, B1 §3, §8 non-goals) · [`STATUS.md`](../STATUS.md) (streams 3/6, surface gating table) · ADR-015 (MCP), PC-2 (`digest-core/docs/PC2_DATA_HANDLING.md`, unwritten).
