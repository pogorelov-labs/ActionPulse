# PC-2 — Per-endpoint data-handling ADR

> **Status:** ☐ **DRAFT** (offline scaffold) — the per-endpoint statements below are **placeholders**
> the owner fills with written answers from the corp LLM-platform team at the corp session.
> **Until every endpoint a feature uses is `CONFIRMED` here, that feature's live-flag stays off.**
>
> **Type:** ADR (the "master gate" referenced by `ROADMAP.md`, `RUNBOOK.md`, `REDESIGN_PLAN.md`).
> **Companion:** ARCHITECTURE ADR-006 (masking boundary), ADR-014 (store), ADR-015 (MCP exposure).
> **Owner action:** this is the document to bring to the LLM-platform / security team.

## 1. Context — the actual privacy boundary

ActionPulse is privacy-first, but it is **not** zero-egress: the digest is produced by sending
**corporate message content** (subjects, bodies, sender names/emails — see ADR-006: PII is masked
in *logs*, never in the evidence sent to the model) to the **corp LLM gateway**. The boundary that
matters is therefore *external*:

> The gateway promises **non-logging**, not **redaction**. There is no gateway-side scrubbing —
> `x-redaction-policy: strict` was fictional and is **not** sent (ADR-006, verified). The corp
> gateway's own logging/retention policy is the **only inference-time control** on the content we
> send. (Our side never logs payloads/secrets, redacts bearer/PAT in logs, and bounds retention —
> §3 — but none of that constrains what the *endpoint* does with a request once received.)

So before turning on any endpoint that receives content **in prod**, we need a **written**
logging / retention / caching / residency / training-use statement **per endpoint**, recorded here.
This is not derivable from code — it must be confirmed with the platform team.

## 2. Scope — endpoints and what each receives

The extractor `/v1/chat` is **already live** (the daily digest). The rest are **built but off**
behind flags; this ADR gates flipping them on.

| Endpoint | Used by | Data SENT to it | Live today? | Flag gated on this ADR |
|----------|---------|-----------------|:-----------:|------------------------|
| EWS (Exchange) | ingest | NTLM auth only; **reads** mail (no content egress) | ✅ | — (inbound) |
| `/v1/chat` (extractor) | daily digest | evidence text = subjects + bodies + sender names/emails; the prompt | ✅ | — (already accepted in prod) |
| `/v1/chat` (judge model) | `judge.enabled` | the evidence body + the extracted item, to score support | ☐ off | `judge.enabled` |
| `/v1/chat` (ask / explain) | `ask`, `explain` | retrieved passages (ask) / run telemetry only, never bodies (explain) | ☐ off* | — (rides the extractor accept) |
| `/v1/embeddings` | store `reembed`, threading | message body text to vectorize — **DM bodies excluded** (redacted at rest → no chunks, §3) | ☐ off | `enable_relevance`, store reembed-live, `threading.embedding_merge` |
| `/rerank` (`/v1/score`) | `reranker.enabled` | the query + candidate message snippets (low-confidence items only) | ☐ off | `reranker.enabled` |
| `/v1/tokenize` | budget pre-count (if used) | text to count tokens | ☐ off | — |
| Mattermost (webhook / api) | delivery, reactions | **sends** the digest (extracted items + verbatim quotes); api-mode also reads reactions | ✅ webhook | api-mode → its own owner-channel + PAT |

\* `ask`/`explain` are shipped but need the gateway live; `explain` sends only whitelisted run
telemetry (never email bodies — see `explain.py`).

## 3. Controls already in place (our side)

These are real and verified; they bound *our* handling, not the endpoint's:

- **DM bodies redacted at rest** (guardrail #9, structural — DM messages get **no rows in the
  chunks table**), so `/v1/embeddings`, `/rerank`, and store search **cannot** receive DM text.
- **No payload/secret logging**; structured logs mask PII + bearer/PAT tokens (#177).
- **Retention:** ≤7 d plaintext (`var/out`), ≤7 d delivered-posts ledger, ≤30 d encrypted store
  (opt-in, SQLCipher; ADR-014). `--dump-ingest` snapshots redact DMs fail-closed.
- **Spotlight fencing:** untrusted evidence is fenced between per-call random markers (EP-4).
- **Per-session TLS** (no process-global verify bypass); secrets ENV-only, never YAML.

## 4. Decision (proposed framework)

1. **Default-deny.** Every content-receiving endpoint's live-flag (`reranker.enabled`,
   `enable_relevance`, `judge.enabled`, store `reembed` against the real gateway,
   `threading.embedding_merge`) stays **off** until its row in §2 is marked `CONFIRMED` with a
   written statement recorded in §5.
2. **Per-endpoint, not blanket.** A non-logging guarantee for `/v1/chat` does **not** automatically
   extend to `/v1/embeddings` / `/rerank` / the judge model — confirm each.
3. **DM-to-gateway is double-gated.** Counterparty DM content reaching the gateway requires **both**
   the user's DM-ingest consent **and** a CONFIRMED statement for the receiving endpoint. (Today DMs
   never reach the gateway — they're redacted at rest; this governs any future change.)
4. **Record, then flip.** When a statement is obtained, fill §5, flip §2's status to `CONFIRMED`,
   then (and only then) flip the corresponding flag. Set this ADR's Status to `ACCEPTED` once the
   endpoints needed for the activation cycle are confirmed.

## 5. Per-endpoint statements — TO BE FILLED at the corp session

For each endpoint to be enabled, obtain written answers and record them here. **`<TBD>` =
unanswered (blocks the flag).**

### `/v1/chat` (extractor + judge + ask)
- Logging of request/response content: `<TBD>`
- Retention (duration, where): `<TBD>`
- Caching (are prompts/results cached, keyed how, for how long): `<TBD>`
- Data residency (region/jurisdiction): `<TBD>`
- Used for model training / fine-tuning / eval: `<TBD>`
- Statement source (person / doc / date): `<TBD>` → **Status: ☐ CONFIRMED**

### `/v1/embeddings`
- Logging / retention / caching / residency / training: `<TBD>`
- Confirm: does the non-logging guarantee match `/v1/chat`? `<TBD>` → **Status: ☐ CONFIRMED**

### `/rerank` (`/v1/score`)
- Logging / retention / caching / residency / training: `<TBD>` → **Status: ☐ CONFIRMED**

### `/v1/tokenize` (only if used)
- Logging / retention: `<TBD>` → **Status: ☐ CONFIRMED**

### Mattermost (api-mode delivery + reactions)
- Owner-only target channel id: `<TBD>` (corp-interactive — list channels via the live API)
- Confirm digests post only to that private channel: `<TBD>` → **Status: ☐ CONFIRMED**

## 6. Consequences

- **Until ACCEPTED:** the deployment runs the Conservative variant (extractor `/v1/chat` only;
  fleet off; store keyword-only; webhook delivery). This is the current state — RUNBOOK §"What this
  deployment runs today".
- **Once ACCEPTED (per-endpoint):** flip the confirmed flags → reranker / fused relevance / judge /
  semantic search + `ask` over the live gateway / api-mode delivery come online. This is the unlock
  in ROADMAP Phase B → C.
- **PC-1 (separate, already ✅ Personal):** service-account vs Personal selects the extractor model
  (`qwen35-397b-a17b` @15 RPM); not part of this ADR.

## 7. Open questions for the platform team

- Is the gateway's non-logging policy **uniform** across `/v1/chat`, `/v1/embeddings`, `/rerank`,
  `/v1/tokenize`, or per-endpoint?
- Any endpoint that **caches** by content hash (a cache is a form of retention)?
- Residency: do any endpoints route outside the approved region?
- Is there a contractual "no training on inputs" guarantee, or only operational non-logging?
