# A6 — Conversational / Saved / Scheduled "Ask Your Inbox"

**Spec item:** A6 (conversational / saved / scheduled ask)
**Date:** 2026-06-20 · **Status:** research brief · **Confidence:** medium-high on landscape, medium on prioritization

---

## 1. TL;DR

- **The market has split A6 into two products, and they map cleanly onto ActionPulse's two halves.** "Ask your inbox" (one-shot Q&A → answer) is now table stakes — Gemini in Gmail, M365 Copilot, Shortwave, Fyxer, Glean all ship it. **Multi-turn refinement** is common but is rarely the headline; **scheduled / standing queries** are the newer, stickier, more differentiated capability.
- **Scheduled ask is real and shipping at scale.** Microsoft 365 Copilot has first-class **Scheduled Prompts** (daily/weekly, up to 10 per user, delivered in chat + optional email), and Google shipped **Gemini Daily Brief** (a scheduled Gmail/Calendar/Tasks digest). The canonical examples Microsoft itself promotes — "morning briefing," "mid-week messages + tasks needing follow-up," "end-of-day action items + decisions" — *are exactly ActionPulse's digest sections rendered as standing queries.*
- **The killer standing-query patterns for a work inbox are commitment/obligation tracking**, not retrieval: "what am I waiting on (and who's overdue)," "what did I promise and by when," "what needs a reply," "what decisions/changes did I miss." Dedicated tools (Serif, alfred_, Fyxer) compete almost entirely on this, by scanning *sent* mail for outbound promises and matching incoming replies to resolve/age them.
- **The dominant failure modes are trust failures, not capability gaps:** silent omission (the answer looks complete but missed the one email that mattered), fabrication on under-grounded prompts, and recency/scope ambiguity. These are exactly the risks ActionPulse's evidence-tracing (P1/P2) is built to dominate — that is the wedge.
- **Recommendation:** ActionPulse should **(1) keep one-shot ask as the core, (2) add lightweight multi-turn (follow-ups in the same session, cheaply), and (3) invest meaningfully in saved + scheduled standing queries**, because scheduled standing queries are a natural extension of the existing digest engine (cron + a saved retrieval) and align with the product's strongest differentiator (cited, in-perimeter, no-egress). Multi-turn is a UX nicety; scheduled commitment-tracking is a product.

---

## 2. Landscape

| Product | Ask model | Grounded / cited? | Scheduled / standing? | Notes |
|---|---|---|---|---|
| **Gemini in Gmail (Q&A / "Help me")** | One-shot + light follow-up | Partial — "Sources" link on *some* answers; Google itself warns to verify | **Yes** — separate **Gemini Daily Brief** (scheduled Gmail+Calendar+Tasks digest) | Mass-market default; inbox-scoped; recency/scope not user-controllable |
| **Microsoft 365 Copilot (Outlook)** | One-shot + multi-turn chat | Cited but documented to **omit / mis-prioritize / fabricate** | **Yes — first-class Scheduled Prompts** (Teams/Outlook/Office chat; ≤10 active; daily/weekly/monthly; results in chat + email) | The closest competitor to ActionPulse's *scheduled* half; enterprise reach |
| **Shortwave** | **Multi-turn conversational**, multi-step reasoning, MCP cross-app actions | References threads via `about:[topic]` buttons; specific-ID citation not documented | **Saved Prompts** (one-click commands) + "AI Memories" for recurring briefing *content*; **no native scheduler** documented | Strongest pure conversational + agentic-action UX |
| **Fyxer** | **Fyxer Chat** — ask inbox/meetings, text+voice | Connects to live Gmail/Outlook; citation depth unclear | Triage + follow-up reminders; standing follow-up rules | Browser-extension overlay, no migration |
| **Serif AI** | Q&A + drafting | Tied to emails/meetings | **Commitment tracking is the headline** — tracks "every commitment across emails and meetings" | Closest to the commitment standing-query thesis |
| **alfred_ / InboxPilot / Carly / Lindy** | Q&A + agentic | Varies | Detects "gone quiet" threads, auto-surfaces follow-ups | Agent-pattern triage; standing rules on sent mail |
| **Glean (enterprise)** | **Multi-turn**, agentic (Fast/Thinking modes) | **Strong — permissions-aware, deep-linked citations** | Single-pass cross-source triage (Gmail+Slack+Asana) | Best-in-class grounding/citation model to emulate; not email-only |
| **ActionPulse (today)** | One-shot RAG ask | **Strong — every item cites evidence_id + source_ref (P1/P2)** | Daily digest exists; ask is not yet scheduled/standing | In-perimeter, no cloud egress = the differentiator |

---

## 3. Findings

**(a) RAG-over-email / conversational inbox assistants.**

- **What they let you ask** converges on the same set: catch-me-up/summarize a topic or thread, find a specific fact ("PO number," "how much did we spend on the event"), show unread / from-sender, and extract action items. Gmail's own examples are "Catch me up on the emails about quarterly planning" and "What was the PO number for my agency?" ([Google Workspace blog](https://workspaceupdates.googleblog.com/2024/08/gmail-q-new-way-of-searching-your-inbox.html)). Shortwave's go further into action ("Find all emails related to Project Falcon and create a Notion status update") ([Shortwave docs](https://www.shortwave.com/docs/guides/ai-assistant/)).
- **One-shot vs multi-turn.** Multi-turn is *available* nearly everywhere but is rarely the selling point. Shortwave explicitly supports follow-up clarifications/refinement ([Shortwave docs](https://www.shortwave.com/docs/guides/ai-assistant/)); Glean is conversational with iterate/refine and Fast/Thinking modes ([Glean Assistant](https://www.glean.com/product/assistant)). Gmail Q&A and Copilot are primarily one-shot per question with chat context. The independent read: refinement matters most when the *first retrieval is wrong*, which points back to retrieval quality rather than turn-count as the real lever.
- **Grounding / citation** is the maturity axis. Glean is the gold standard: "permissions-aware, fully referenceable… deep-linked citations" ([Glean Assistant](https://www.glean.com/product/assistant)). Gmail shows a "Sources" link on *some* answers and Google itself tells users to verify ([eesel guide](https://www.eesel.ai/blog/gmail-ask-gemini-for-details)). Shortwave links threads but specific-quote/ID citation isn't documented. **ActionPulse already meets the Glean bar by contract (P2 traceability) — this is rare in the email-assistant tier.**
- **Known failure modes (cross-checked, multiple sources):**
  - *Silent omission / mis-prioritization* — summaries "omit key aspects or mix important concepts with secondary elements" ([Bonfy](https://blog.bonfy.ai/ai-hallucinations-compliance-risk-and-the-need-for-microsoft-copilot-oversight)). This is the most dangerous failure: the answer *looks* complete.
  - *Fabrication on under-grounded prompts* — Computerworld's hands-on test had Copilot invent data-quality problems, fake examples ("M or F" vs "Male or Female"), and a non-existent attachment ([Computerworld](https://www.computerworld.com/article/3475988/is-copilot-for-microsoft-365-a-lying-liar.html)). Hallucination rises "when disconnected from real-time enterprise data or with weak prompts" ([Bonfy](https://blog.bonfy.ai/ai-hallucinations-compliance-risk-and-the-need-for-microsoft-copilot-oversight)).
  - *Trust erosion* — "many corporate users complain that ChatGPT in their pocket is better than the Copilot their employer provides" ([Bonfy](https://blog.bonfy.ai/ai-hallucinations-compliance-risk-and-the-need-for-microsoft-copilot-oversight)); Gmail users are told to double-check via the Sources link ([eesel](https://www.eesel.ai/blog/gmail-ask-gemini-for-details)).
  - *Recency/scope ambiguity* — none of the consumer tools let the user pin a date window or guarantee "all" emails were considered; Gmail explicitly notes scope is expanding over time ([Google Workspace blog](https://workspaceupdates.googleblog.com/2024/08/gmail-q-new-way-of-searching-your-inbox.html)).

**(b) Scheduled / standing-query products.**

- **Microsoft 365 Copilot Scheduled Prompts** is the most direct prior art: automate a prompt "at specified times and frequencies across Teams, Office.com chat, and Outlook," up to **10 active** per user, results in the Copilot chat *plus optional email notification* ([MS Learn](https://learn.microsoft.com/en-us/copilot/microsoft-365/scheduled-prompts), [MS Support](https://support.microsoft.com/en-us/topic/schedule-copilot-prompts-29dfd5fb-211a-4515-88a6-730b8074e489)). Microsoft's three canonical examples are a **morning briefing** (meetings/agenda/notes), a **mid-week check-in** (messages + tasks needing follow-up), and an **end-of-day** summary (action items + decisions) — i.e. ActionPulse's sections as standing queries.
- **Google Gemini Daily Brief** is a scheduled, learned morning digest over Gmail/Calendar/Tasks that "prioritizes your inbox" and improves personalization over time ([MindStudio](https://www.mindstudio.ai/blog/google-gemini-daily-brief-ai-morning-digest)). This is essentially ActionPulse's product, from Google, scheduled by default — validating the category and raising the bar.
- **Dedicated commitment-tracking** is where standing queries get specific and valuable: tools "scan sent emails to identify outbound commitments… recognizing 'I will send the revised contract by Wednesday' as a commitment with a deadline," then "monitor incoming replies, automatically resolving action items… and surfacing items as overdue when deadlines pass" ([alfred_](https://get-alfred.ai/blog/best-ai-assistant-for-email-follow-ups)). Standing rules like "remind me to follow up if no response within 3 business days" are explicitly supported. Serif's headline is tracking "every commitment across emails and meetings"; alfred_ detects "gone quiet" threads.
- **Why it matters:** 42% of knowledge workers say their inbox is "out of control" and 36% admit ignoring messages from overwhelm ([MindStudio](https://www.mindstudio.ai/blog/google-gemini-daily-brief-ai-morning-digest)) — the demand for *push* (scheduled) over *pull* (ask) is structural.

---

## 4. Implications for ActionPulse

**One-shot vs multi-turn vs saved/scheduled — recommended investment:**

1. **One-shot ask (keep as core).** Already shipped and already at the *grounding bar that the market struggles with*. No major new investment; this is the baseline.
2. **Multi-turn (light, cheap).** Add follow-up turns within a single ask session (carry retrieved evidence + prior answer as context; let the user say "narrow to last week," "only from Anna," "just the overdue ones"). Value is real but bounded — it mostly rescues a wrong first retrieval. **Constraint: the 2-LLM-call budget (ADR-008) means true open-ended chat is off-budget.** Scope multi-turn as *refinement of a fixed retrieval*, not arbitrary new agentic calls per turn. **Investment: small.**
3. **Saved + scheduled standing queries (invest here).** This is the highest-leverage A6 work because it is *almost free architecturally*: ActionPulse already has (a) a daily digest pipeline (the scheduler/cron + delivery to Mattermost/email) and (b) a cited retrieval ("ask"). A **saved query = ask + a name**; a **scheduled query = saved query + cron + reuse the existing delivery path**. It also leans on the strongest differentiator (in-perimeter, no egress, every line cited) against Copilot/Gemini's trust problems. **Investment: medium, high ROI.** Mirror Copilot's sane defaults: a small cap (~10) of saved/scheduled queries, daily/weekly cadence, deliver to the same channel as the digest.

**Top standing-query patterns for a work inbox (ranked by value):**

1. **"What am I waiting on / who's overdue to me?"** — outbound asks with no reply past N days. Highest-value, lowest-noise; this is what dedicated tools monetize.
2. **"What did I promise, and by when?"** — outbound commitments extracted from *sent* mail, aged against deadlines.
3. **"What still needs a reply from me?"** — inbound questions/asks directed at the user, unresolved.
4. **"What changed / what did I miss?"** — decisions, reschedules, scope/priority changes since the last digest/login.
5. **"Weekly rollup of open threads by project/person"** — recurring status across a topic.
6. **"End-of-day: action items + decisions from today."** — Copilot's own canonical pattern.

Patterns 1–3 require reasoning over **sent** mail and **reply-matching** (resolve/age an item when a reply arrives) — a capability ActionPulse's evidence model can support but does not centrally do yet. These are the differentiated, defensible patterns; pure retrieval/summary (4–6) are commoditized by Gemini/Copilot and win only on *trust* (citations + no egress), which ActionPulse already has.

**Strategic note:** Google's Gemini Daily Brief and Copilot Scheduled Prompts mean ActionPulse is no longer the only "scheduled inbox digest." The defensible ground is **evidence-traced, in-perimeter, commitment-aware standing queries** — not generic morning summaries.

---

## 5. Open questions / low-confidence

- **Citation depth of competitors is genuinely unclear** from public docs for Shortwave/Fyxer/Serif (whether they cite *specific* emails/quotes or just link threads). Low confidence; would need hands-on testing. Glean's deep-linking and Gmail's "Sources" link are confirmed; the rest is inferred.
- **Reply-matching accuracy** (resolving a commitment when a reply arrives) is asserted by vendor blogs (alfred_, Serif) but I found **no independent benchmark**. Treat "real-time auto-resolution" as marketing until verified.
- **Demand split for ActionPulse's user**: I'm inferring (from market structure + the 42%/36% overwhelm stats) that scheduled-push beats conversational-pull for a *work* inbox. This is a reasonable but untested hypothesis for ActionPulse's specific corp users — worth a quick user check.
- **Multi-turn ROI under the 2-call budget** is uncertain; whether "refinement of a fixed retrieval" feels good enough vs. open chat needs a prototype.
- **Recency cutoff:** some sources are vendor/SEO blogs (alfred_, MindStudio, Bonfy). Microsoft/Google primary docs and Computerworld are the load-bearing citations; vendor blogs are corroborating, not authoritative.

---

## 6. Sources

- Microsoft 365 Copilot — Scheduled Prompts (admin): https://learn.microsoft.com/en-us/copilot/microsoft-365/scheduled-prompts
- Microsoft 365 Copilot — Schedule prompts (end-user): https://support.microsoft.com/en-us/topic/schedule-copilot-prompts-29dfd5fb-211a-4515-88a6-730b8074e489
- Gmail Q&A with Gemini (Google Workspace blog): https://workspaceupdates.googleblog.com/2024/08/gmail-q-new-way-of-searching-your-inbox.html
- Gmail "Ask Gemini for details" — practical guide + verify caveat (eesel): https://www.eesel.ai/blog/gmail-ask-gemini-for-details
- Gemini Daily Brief — scheduled inbox digest (MindStudio): https://www.mindstudio.ai/blog/google-gemini-daily-brief-ai-morning-digest
- Shortwave AI Assistant docs (multi-turn, saved prompts, memories): https://www.shortwave.com/docs/guides/ai-assistant/
- Glean AI Assistant (grounded, deep-linked citations, agentic modes): https://www.glean.com/product/assistant
- Best AI for email follow-ups — commitment tracking / standing rules (alfred_): https://get-alfred.ai/blog/best-ai-assistant-for-email-follow-ups
- "Is Copilot for Microsoft 365 a lying liar?" — hands-on failure modes (Computerworld): https://www.computerworld.com/article/3475988/is-copilot-for-microsoft-365-a-lying-liar.html
- AI hallucinations & Copilot oversight — omission/fabrication risks (Bonfy): https://blog.bonfy.ai/ai-hallucinations-compliance-risk-and-the-need-for-microsoft-copilot-oversight
- AI email assistant comparisons 2025/26 (Fyxer, Serif, Superhuman, Zapier): https://blog.superhuman.com/fyxer-vs-superhuman/ · https://www.serif.ai/blog/serif-ai-vs-fyxer-ai-which-email-assistant-is-actually-better-in-2026 · https://zapier.com/blog/best-ai-email-assistant/
