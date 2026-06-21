# ActionPulse Architecture & Technical Specification

> **Version:** 1.3.0 | **Status:** Living Document | **Last Updated:** 2026-06-21 (calendar source E1–E3 + Meetings section + history surface-parity; ADR-014 store, ADR-015 MCP/InboxAPI)
>
> Этот документ — единственный источник правды для архитектуры, контрактов и роадмапа.
> Любые решения, противоречащие этому документу, требуют его обновления.

---

## 1. Product Vision

**Одно предложение:** Ежедневный автоматический дайджест корпоративных коммуникаций
(почта, чаты), который показывает: что от тебя ждут, что срочно, что решили — с
трассируемой ссылкой на первоисточник.

**Не-цели (что мы НЕ делаем):**
- Не платформа для многих пользователей (single-tenant CLI tool)
- Не real-time система (batch, daily cron)
- Не замена почтового клиента (дополнение, read-only)
- Не AI-agent с действиями (только extraction + presentation)

---

## 2. Architecture Principles

| # | Принцип | Следствие |
|---|---------|-----------|
| P1 | **Extract-over-Generate** | LLM извлекает факты из evidence, а не генерирует "от себя". Каждый пункт привязан к evidence_id |
| P2 | **Traceability** | Любой пункт дайджеста → evidence_id → source_ref → оригинальное письмо/сообщение |
| P3 | **Privacy-first** | PII маскируется на уровне LLM Gateway. Локально — минимальное хранение: артефакты в `var/out` авто-удаляются в конце реального запуска по `retention.keep_days` (default **7 дней**, configurable; `enabled`/`keep_days` + env `DIGEST_RETENTION_*`) |
| P4 | **Idempotency** | `(user_id, date)` → один и тот же результат. Watermark + T-48h rebuild window |
| P5 | **Graceful Degradation** _(частично)_ | **Сделано (Phase 0):** сбой LLM после ретраев → валидный partial digest с секцией «Статус»; сбой MM delivery → warning, exit 0 (ADR-011). **Ещё нет:** частичный отчёт при падении EWS ingest и др. стадий до LLM — по-прежнему exception |
| P6 | **Simplicity-first** | Не добавлять abstractions до появления второго use case |
| P7 | **Prompt-is-the-product** | Качество дайджеста на 80% определяется промптом, а не инфраструктурой |

---

## 3. System Context

```
                          ┌──────────────────────────────────────────────────┐
  Exchange (EWS/NTLM) ───>│              digest-core (Python 3.11)            │
   (mail + calendar)      │                                                  │
  Mattermost (v4 API) ───>│  ingest → normalize → threads → evidence →       │
   (opt-in sources:       │    select → LLM extraction → assemble → deliver  │
    @-mentions, channels,  │                                                  │
    consent-gated DMs)     │  Outputs: digest-YYYY-MM-DD.{json,md}            │
                          │   → Mattermost (incoming webhook OR authenticated │
  Corp LLM Gateway   <──> │      api delivery) · Prometheus :9108 · logs      │
  (qwen35-397b-a17b,      └───────────────────────┬──────────────────────────┘
   15 RPM; corp-only)                             │ (opt-in, encrypted at rest)
                          ┌───────────────────────▼──────────────────────────┐
                          │  message store (SQLCipher + FTS5 + vectors)       │
                          │   → search · ask · open-loops / pending · InboxAPI │
                          │   → MCP server (actionpulse-mcp, stdio)           │
                          └──────────────────────────────────────────────────┘

  Network: EWS + LLM gateway are corp-only; Mattermost delivery works anywhere,
  ingest is corp-only. Store + MCP are opt-in (default off). Gateway-backed features
  (semantic/hybrid search, ask, reranker, judge) are gated on PC-2 (§16 / ROADMAP).
  See ADR-012 (network), ADR-014 (store), ADR-015 (InboxAPI + MCP).
```

---

## 4. Pipeline Stages

### 4.1 Stage Overview

```
EWS Inbox · MM · Calendar          (opt-in sources; each NormalizedMessage is source-tagged)
    │
    ▼
┌──────────┐   NormalizedMessage[]  (raw HTML body — naming debt, см. TD-009)
│ 1.INGEST │──────────────────────────┐
└──────────┘                          │
    │                                 ▼
┌──────────┐   NormalizedMessage[]  (cleaned text body)
│2.NORMALIZE│─────────────────────────┐
└──────────┘                          │
    │                                 ▼
┌──────────┐   ConversationThread[]
│3.THREADS │──────────────────────────┐
└──────────┘                          │
    │                                 ▼
┌──────────┐   EvidenceChunk[]  (sorted by priority, truncated ≤ max_total_tokens — default 7000)
│4.EVIDENCE│──────────────────────────┐
└──────────┘                          │
    │                                 ▼
┌──────────┐   EvidenceChunk[]  (bucket-balanced subset, same token budget + optional shrink)
│ 5.SELECT │──────────────────────────┐
└──────────┘                          │
    │                                 ▼
┌──────────┐   Digest (validated JSON) — extractor ≤2 LLM calls (1 primary + 1 quality retry, 15 RPM; ADR-008 v2 per-stage budgets)
│  6.LLM   │──────────────────────────┐
└──────────┘                          │
    │                                 ▼
┌──────────┐   digest-{date}.json + .md
│7.ASSEMBLE│──────────────────────────┐
└──────────┘                          │
    │                                 ▼
┌──────────┐   file saved + MM DM sent (or webhook)
│8.DELIVER │
└──────────┘
```

### 4.2 Stage Contracts

#### Stage 1: INGEST

**Input:** EWS + Mattermost + Calendar configs + digest_date + time_config
**Output:** `List[NormalizedMessage]` — source-tagged (`source="email"` default | `"mm"` | `"calendar"`)

> **Naming debt (TD-009):** Тип называется `NormalizedMessage`, но на выходе Stage 1
> тело письма ещё **не нормализовано** (может содержать HTML). Реальная нормализация —
> Stage 2. Корректное имя: `RawMessage` для Stage 1, `NormalizedMessage` для Stage 2.
> Переименование отложено, чтобы не ломать тесты. Учитывать при чтении кода.

```python
class NormalizedMessage(NamedTuple):  # TODO: rename to RawMessage for Stage 1 output
    msg_id: str              # InternetMessageId (lowercase, no angle brackets)
    conversation_id: str     # EWS conversation_id (for threading)
    datetime_received: datetime  # UTC
    sender_email: str        # lowercase
    subject: str
    text_body: str           # Raw text/HTML body (NOT yet normalized)
    to_recipients: List[str] # lowercase emails
    cc_recipients: List[str] # lowercase emails
    # kw-only, NOT part of the content hash (multi-source / calendar):
    source: str = "email"    # "email" (default) | "mm" | "calendar"
    event_end: datetime = None  # calendar events only — end time (E3 collisions)
```

**Invariants:**
- NTLM auth, corporate CA support
- Retry: 8 attempts, exponential backoff (0.5s → 60s)
- Pagination: configurable page_size (default 100)
- Watermark: timestamp-based incremental sync in `.state/ews.syncstate`
- Dedup: by `msg_id` (InternetMessageId)

**Failure mode:** Connection failure after retries → raise, caller handles _(P5 target: partial report)_

**Multi-source:** the same `NormalizedMessage` also carries Mattermost messages (`source="mm"`,
opt-in) and EWS **calendar events** (`source="calendar"`, opt-in via `--sources calendar`).
Calendar events are dated by meeting start and carry `event_end`; they flow through every
downstream stage (in-body agenda actions are extracted) and additionally feed the deterministic
**Meetings** section (§9.3, no LLM). Live calendar fetch is corp-only (ADR-012).

---

#### Stage 2: NORMALIZE

**Input:** `List[NormalizedMessage]` (raw)
**Output:** `List[NormalizedMessage]` (cleaned text_body)

**Operations:**
1. HTML → text (BeautifulSoup): strip scripts, styles, tracking pixels, cid: images
2. HTML entity decode
3. Truncate to 200KB with `[TRUNCATED]` marker
4. Quote removal (RU/EN patterns, recursive up to 5 levels)
5. Signature removal (5+ language patterns)
6. Disclaimer removal
7. Whitespace normalization

**Invariants:**
- Output message count == input message count (no filtering here)
- Empty body after cleaning → body = "" (not filtered out)
- Subject NOT modified at this stage

---

#### Stage 3: THREADS

**Input:** `List[NormalizedMessage]`
**Output:** `List[ConversationThread]`

```python
class ConversationThread(NamedTuple):
    conversation_id: str
    messages: List[NormalizedMessage]  # sorted by datetime_received ASC
    latest_message_time: datetime
    participant_count: int
    message_count: int
```

**Implementation:** `ThreadBuilder` in `threads/build.py` (not a bare group-by).

**Logic (high level):**
1. **Dedup** messages by body checksum (duplicates tracked for diagnostics).
2. **Thread assignment** (per message, first match wins):
   - **Strategy 1:** EWS `conversation_id` → stable key `conv_<id>`.
   - **Strategy 2:** `In-Reply-To` / `References` vs an index of seen `msg_id` → inherit parent thread.
   - **Strategy 3:** **Normalized subject** → key `subj_<hash(normalized)>`; merge with existing thread if any message already shares the same normalized subject. Нормализация темы и семантика слияния — **§4.4** (`SubjectNormalizer`); контракт стадии — **этот подпункт** (#### Stage 3 внутри §4.2).
   - **Fallback:** empty subject → `single_<msg_id>`.
3. **Semantic merge pass:** threads that share a normalized subject may be merged when **body text similarity** exceeds `semantic_similarity_threshold` (default 0.7; `calculate_text_similarity` в `subject_normalizer.py`, см. §4.4).
4. **Caps:** max 50 messages per thread (oldest dropped).
5. **Sort** threads by `latest_message_time` DESC.

**Invariant:** `sum(thread.message_count for all threads) == len(unique messages)` (after dedup).

**Types:** `ConversationThread` in code may carry `merged_by_semantic` and `duplicate_sources`; the NamedTuple above is the stable core fields used across stages.

---

#### Stage 4: EVIDENCE SPLIT

**Input:** `List[ConversationThread]`
**Output:** `List[EvidenceChunk]` — все порождённые чанки сортируются по **`priority_score` DESC**, затем **`EvidenceSplitter._limit_total_tokens`** оставляет префикс списка, укладывающийся в **`context_budget.max_total_tokens`**. На выходе Stage 4 **нет** «полного» набора чанков до бюджета — только усечённый.

**Type (actual code: `@dataclass` in `evidence/split.py`, not a narrow NamedTuple):**

```python
@dataclass
class EvidenceChunk:
    evidence_id: str
    conversation_id: str = ""
    content: str = ""              # primary body; synced with `text` in __post_init__
    text: str = ""
    source_ref: Dict[str, Any] = field(default_factory=dict)
    msg_id: str = ""
    token_count: int = 0           # heuristic: words * 1.3
    priority_score: float = 0.0
    message_metadata: Dict[str, Any] = field(default_factory=dict)
    addressed_to_me: bool = False
    user_aliases_matched: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)
    chunk_idx: int = 0
    total_chunks: int = 1
    timestamp: str = ""
    sender: str = ""
    thread_id: str = ""            # legacy / back-compat with older tests
```

**Token / chunking (see `ContextBudgetConfig`, `ChunkingConfig` in `config.py`; YAML `context_budget`, `chunking`; ENV e.g. `DIGEST_CTX_BUDGET_MAX_TOTAL_TOKENS`, `DIGEST_CHUNKING_*` — §5):**
- `max_tokens_per_chunk`: 512 (default)
- `min_tokens_per_chunk`: 64 (default)
- **`max_total_tokens`:** default **7000** (`ContextBudgetConfig`), not a hard-coded 3000
- Per-message chunk cap: **`ChunkingConfig`** — `max_chunks_default` (12) with reduced caps for long messages (`max_chunks_if_long`, default 3) and optional adaptive scaling under high load

**Splitting strategy:**
1. Split by paragraphs (`\n\n`)
2. If paragraph > 512 tokens → split by sentences (`[.!?]+`)
3. If sentence > 512 tokens → hard truncate

**Implementation note:** В `evidence/split.py` есть **`_detect_structural_breaks()`** (заголовки, списки, маркеры цитирования) — задел под более структурный сплит; **текущий путь сплита его не вызывает**, фактическое поведение соответствует шагам 1–3 выше.

**Priority scoring (additive):**
- Action words (please, need, urgent, approve, deadline...): +1.0 each
- Date/time references: +0.5
- Question marks: +0.5
- Exclamation marks: +0.3
- Recency: <1h +2.0, <6h +1.0, <24h +0.5

---

#### Stage 5: CONTEXT SELECTION

**Input:** `List[EvidenceChunk]` (already truncated by Stage 4 to ≤ `context_budget.max_total_tokens`)
**Output:** `List[EvidenceChunk]` — подмножество после перескоринга, **balanced bucket selection** с тем же лимитом токенов и (если включено) **auto-shrink**

> **Token budget responsibility:**
> Один лимит **`context_budget.max_total_tokens`** (default **7000**, см. `ContextBudgetConfig`) применяется **последовательно на двух стадиях**:
> 1. **Stage 4** — глобальная усечённость корпуса: после сортировки по приоритету список обрезается, пока сумма `token_count` не превышает бюджет (`EvidenceSplitter._limit_total_tokens`).
> 2. **Stage 5** — отбор подмножества для LLM: `ContextSelector.select_context` передаёт тот же бюджет в **`_select_with_buckets(..., token_budget)`**; чанки не «размножаются», суммарный размер выбранного набора укладывается в остаток бюджета по мере заполнения квот. При **`shrink.enable_auto_shrink`** вызывается **`_ensure_token_budget`** для дожима до лимита.
>
> | Стадия | Роль | Что делает |
> |--------|------|------------|
> | Stage 4 (Evidence) | **Coarse cap** | Сортировка по `priority_score`, затем префикс списка по `max_total_tokens` |
> | Stage 5 (Select) | **Selection under cap** | Фильтрация шума, перескоринг, бакеты (`threads_top`, `addressed_to_me`, `dates_deadlines`, `critical_senders`, `remainder` — квоты в `SelectionBucketsConfig` / YAML `selection_buckets`), учёт `token_count` при выборе |
> | Stage 6 (LLM) | **Consumer** | Формирует запрос из выбранных чанков; бюджет evidence не расширяет |

**Logic (implementation: `select/context.py`):**
1. Отфильтровать служебные письма (noreply, undeliverable, OOO, auto-reply и т.д.).
2. Пересчитать score для каждого чанка; поле **`priority_score` перезаписывается** новым агрегированным значением (базовый приоритет Stage 4 входит с малым весом — см. `_calculate_enhanced_scores`).
3. Заполнить бакеты с учётом **`token_budget`** и квот из конфига.
4. Опционально: auto-shrink, если включён `ShrinkConfig.enable_auto_shrink`.

---

#### Stage 6: LLM EXTRACTION

**Input:** `List[EvidenceChunk]` + prompt template + trace_id
**Output:** `Dict` (validated against Digest schema)

**Target model:** `qwen35-397b-a17b` (corp LLM Gateway)

**Rate limit constraint: 15 RPM (requests per minute)**

Это ключевое ограничение, определяющее архитектуру LLM-вызовов:
- **Max 2 LLM-вызова на run** (1 primary extraction + опциональный quality retry, см. ADR-008). Типичный run — 1 вызов.
- При 15 RPM этого хватает с запасом для single-tenant MVP.
- Batch of N users: при 15 RPM max ~15 пользователей/мин или ~900/час.
  Для single-tenant MVP — не блокер. Для multi-tenant (Phase 4+) — потребуется
  очередь с rate limiter.
- **Запрещено:** multi-step pipeline (extract → summarize → format) расходует
  3 RPM на 1 run и быстро упирается в лимит. ADR-002 (single call) подтверждён.

**LLM Request (shape; see `LLMGateway._make_request_once` in `llm/gateway.py`):**
```json
{
  "model": "qwen35-397b-a17b",
  "messages": [
    {"role": "system", "content": "<prompt_template>"},
    {"role": "user", "content": "<numbered evidence blocks with headers>"}
  ],
  "temperature": 0.0,
  "max_tokens": 6000,
  "response_format": {"type": "json_object"}
}
```

`temperature` and `max_tokens` come from config (`llm.temperature`, default 0.0;
`llm.max_output_tokens`, default 6000 — a real production day measured 5,226 output
tokens, so the former hardcoded 2000 truncated; see
`CORP_VALIDATION_FINDINGS_2026-06.md` F-01). `max_output_tokens` is clamped to the
gateway output ceiling (16384; oversize `max_tokens` returns HTTP 429, not 413).
`finish_reason=length` with unparseable JSON fails straight to degrade (no retry —
deterministic truncation would just repeat); with parseable JSON it logs a warning
and is recorded in the request meta.

**Retry policy (two levels):**

_Internal retries (within `_make_request_with_retry`, `stop_after_attempt(2)`)_:
- HTTP 429 (rate limit): wait `Retry-After` header or 60s, then 1 retry
- HTTP 5xx: 1 retry after 5s
- JSON parse error: 1 retry after 4s, adds "Return strict JSON" hint

_Quality retry (in `extract_actions`, on top of internal retries)_:
- If empty sections but evidence has positive signals (priority_score ≥ 1.5)
  → 1 additional call with quality hint, 4s rate-limit wait

**Max LLM HTTP calls per pipeline run:** 2 logical calls (1 primary + 1 quality retry),
each with up to 1 internal retry for transient errors = max 4 HTTP requests worst case.
Typical run: 1 HTTP call.

**Response validation:**
- Each item must have: title, evidence_id, confidence, source_ref
- `evidence_id` must exist in input evidence list
- `confidence` must be float in [0, 1]
- `source_ref` must have `type` field
- Invalid items silently dropped (partial result)

**Token capture:** from response headers (`x-llm-tokens-in/out`) or body `usage` field

**qwen35-397b-a17b specific notes:**
- Prompt language: RU (default) or EN. qwen3.5 handles both well.
  Default: `extract_actions.v1.txt` (RU). For models containing `qwen` in the name,
  the loader auto-selects `extract_actions.en.v1.txt` (see `run.py:_load_extract_prompt`).
- JSON mode: qwen3.5 reliably outputs structured JSON with clear schema
  instructions. Few-shot examples still recommended for edge cases.
- Context window: must fit configured evidence budget (`context_budget.max_total_tokens`, default 7000) + system prompt + completion headroom.
  No second-round chunking of the **HTTP** request beyond Stage 4/5 (single messages payload).

**Post-LLM steps (still in `run.py`, before Stage 7):**

- **`--validate-citations`:** rebuild `Item.citations` from **selected** `EvidenceChunk`s via `CitationBuilder`, validate with `CitationValidator` against normalized bodies (`msg_id` → `text_body` map). On any failure for a non-`system` item, `citation_validation_ok` is false; CLI exits **2** after assemble/deliver. Partial digests (`evidence_id: system`) and empty digests skip the requirement.
- **`ranker.enabled`:** optional `DigestRanker.rank_items` per section (default **off**; see §4.3).

---

#### Stage 7: ASSEMBLE

**Input:** `Digest` (Pydantic model)
**Output:** `digest-{date}.json` + `digest-{date}.md`

**JSON Schema:**
```python
# `llm/schemas.py` — default `cli run` / Digest schema 1.0. Pydantic: BaseModel, Field.
# Masked PII fields (1.1.0 removal) не используются.

class Citation(BaseModel):
    msg_id: str
    start: int                   # offset in normalized body
    end: int
    preview: str                 # text[start:end], capped
    checksum: Optional[str] = None

class Digest(BaseModel):
    schema_version: str = "1.0"
    prompt_version: str
    digest_date: str
    trace_id: str
    sections: List[Section]
    total_emails_processed: int = 0
    emails_with_actions: int = 0

class Section(BaseModel):
    title: str
    items: List[Item]

class Item(BaseModel):
    title: str
    due: Optional[str] = None
    evidence_id: str
    confidence: float            # 0.0 - 1.0
    source_ref: Dict[str, Any]
    email_subject: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)
```

**Markdown format:**
- Russian localization ("Дайджест действий")
- Max 10 items per section
- Max 400 words total
- Confidence → Russian text (очень высокая ≥0.9, высокая ≥0.7, средняя ≥0.5, низкая ≥0.3, очень низкая <0.3)
- Evidence references section with IDs
- Empty digest: "За период релевантных действий не найдено"

---

#### Stage 8: DELIVER

**Input:** File paths (`.json`, `.md`) + delivery config
**Output:** Delivery confirmation (log entry + optional delivery receipt)

**Delivery targets (ordered by priority):**

| Target | Phase | Mechanism | Config |
|--------|-------|-----------|--------|
| **File (disk/S3)** | MVP (done) | `Path.write_text()` | `out` CLI flag |
| **Mattermost** (incoming webhook → канал webhook; Bot API — опционально) | Phase 0 | Incoming webhook POST or Bot API | `deliver.mattermost.*` |
| Email (SMTP) | Phase 1+ | Optional | `deliver.email.*` |

**Mattermost delivery (Phase 0):**

> **SHIPPED (#200): PAT-first is the wizard default.** Setup leads with a Personal
> Access Token → api-mode (Вариант B): delivery to the owner's own private channel /
> self-DM, found-or-created on the first run (provably owner-only; post-ids captured for
> the reactions flywheel). The incoming webhook (Вариант A) is the **fallback** when no
> PAT is given. The "start with webhook" framing below is the original Phase-0 decision —
> the connect-out api path is now the default.

Два варианта подключения (от простого к гибкому):

**Вариант A: Incoming Webhook (рекомендуется для старта)**
```python
# Одна HTTP-команда, нет bot token management
httpx.post(
    webhook_url,
    json={"text": markdown_content}
)
```
- Плюсы: 0 зависимостей, 1 config field, 5 минут setup
- Минусы: только отправка, нет реакций/команд, привязан к каналу

**Вариант B: Bot API (для Phase 1+ интерактивности)**
```python
httpx.post(
    f"{mm_url}/api/v4/posts",
    headers={"Authorization": f"Bearer {bot_token}"},
    json={"channel_id": dm_channel_id, "message": markdown_content}
)
```
- Плюсы: DM любому юзеру, реакции, slash commands
- Минусы: нужен bot account + token

**Decision (ADR-010):** Начинали с Incoming Webhook (вариант A). **Update (#200):**
мастер теперь по умолчанию использует **PAT/api-mode** (вариант B) — доставку в
owner-only канал/self-DM; webhook остаётся fallback. Bot slash-commands — по-прежнему Phase 1.

**MM Markdown limitations:**
- Нет `###` heading (только `#` и `##` в некоторых клиентах)
- Нет collapsible sections
- Таблицы поддерживаются, но плохо читаются на mobile
- **Max message size:** 16383 characters. Если дайджест длиннее → split на части.
- Рекомендация: компактный формат, без Evidence section (ссылки → JSON-файл).

**MM-specific markdown format:**
```markdown
## Дайджест действий — 2026-03-29

**Мои действия**
1. Согласовать бюджет Q2 → @ivan.petrov, срок: 2026-04-01 (уверенность: высокая)
2. Ответить на запрос юристов по NDA → срок: сегодня (уверенность: средняя)

**Срочное**
1. Сервер staging упал — нужна диагностика (уверенность: очень высокая)

**К сведению**
- Перенос stand-up на 11:00 с понедельника
- Новый шаблон отчётности в Confluence

---
_trace: abc123 | items: 5 | [полный отчёт](link-to-file)_
```

**Failure mode:** MM delivery failure → log warning, do NOT fail pipeline.
File artifacts already written by Stage 7 — delivery is best-effort.

**Feedback collection (Phase 1):**
- Пользователь ставит emoji-реакцию на сообщение бота (👍/👎/🤔)
- Bot API может подписаться на `reaction_added` websocket event
- Логировать: `{trace_id, reaction, timestamp}` → feedback dataset для prompt tuning

---

### 4.3 `select/ranker.py` — `DigestRanker` (item scoring)

**Purpose:** Rule-based **re-ranking of digest items** after extraction (actionability score). Pure Python — no extra ML dependencies. Implements `DigestRanker` with `RankingFeatures` (To/CC match, action/mention flags, due date, sender importance, thread length, recency, attachments, JIRA-style tags) and **normalized weights** that sum to 1.0.

**Config:** `RankerConfig` is mounted on the root `Config` model as `config.ranker` (YAML key `ranker`). ENV overrides follow the generic nested rule `DIGEST_<PREFIX>_<FIELD>` with `PREFIX=RANKER` (e.g. `DIGEST_RANKER_ENABLED`, `DIGEST_RANKER_WEIGHT_USER_IN_TO`) — see `_merge_model` in `config.py` and §5.

**Pipeline status:** When **`config.ranker.enabled`** is `true`, `digest_core/run.py` calls **`DigestRanker`** after Stage 6 (LLM), **per section**, to reorder v1 **`Digest`** items by `rank_score` before Stage 7 (assemble). Default in **`RankerConfig`** is **`enabled: false`** — production output order is unchanged unless the flag is turned on. Stage 5 evidence selection remains **`select/context.py`** (`ContextSelector`); the ranker only reorders **extracted items**, not chunks. Unit tests: `tests/test_ranker.py`.

**API sketch:**
- `DigestRanker.rank_items(items, evidence_chunks)` → sorted list (highest `rank_score` first).
- Project-tag detection uses regex union over `[JIRA-123]`, `[#456]`, etc.

---

### 4.4 `threads/subject_normalizer.py` — `SubjectNormalizer`

**Purpose:** Produce a **canonical subject key** for threading when `conversation_id` and RFC822 reply headers are missing or insufficient. Pure regex/Unicode — no ML.

**Primary API:**
- `normalize(subject: str) -> (normalized_subject, original_subject)` — iteratively strips nested **Re:/Fwd:/Ответ:/Пересл:** (RU+EN), **(External)** markers, bracket/parenthesis **tags** (limited length), **emoji**, normalizes smart quotes and dashes, collapses whitespace, **lowercases**, applies **Unicode NFC**. Original string is returned untouched for display/logging; only the normalized form is used as a merge key inside `ThreadBuilder`.
- `is_similar(a, b)` — equality of normalized forms.
- `calculate_text_similarity` — lightweight character-level similarity used when merging threads that already share a normalized subject (semantic pass in **#### Stage 3: THREADS** above, same §4.2).

**Call sites:** `ThreadBuilder` (`threads/build.py`) — subject-hash grouping, subject-based thread matching, and semantic-merge grouping (**#### Stage 3: THREADS** в §4.2 выше).

**Pipeline status:** **In production path** — invoked during every `run` that builds threads (Stage 3). В отличие от **опционального** `DigestRanker` (§4.3), этот модуль **всегда** задействован, когда выполняется `ThreadBuilder`.

**Tests:** `tests/test_threading.py` (and related threading tests).

---

### 4.5 `llm/models.py` — `LLMResponse` / `parse_llm_json`

**Purpose:** Строгие Pydantic-модели (`LLMResponse`, `EvidenceItem`, `SummaryItem`) и **`parse_llm_json()`** для валидации JSON от LLM в альтернативном формате (`evidence` / `summary` списки).

**Pipeline status:** **Не** используется **`LLMGateway.extract_actions`** и **не** используется типичным **`run.py`**: основной путь парсит JSON в gateway и валидирует контракт **`Digest`** / секций. Файл полезен для тестов, утилит и экспериментов; не считать его частью default daily pipeline без явной проводки.

---

### 4.6 P2 citation gate + LLM fleet (PR8/PR11 + EP-12, decisions D1/D4/D5)

Post-LLM enrichment order in `run.py`: **gate annotate → repair → support-recall →
quarantine**. Every stage is degrade-not-drop (R3): fleet failures fall back to
today's behavior, items are never dropped.

| Step | Module | Contract |
|---|---|---|
| Gate (shadow, always on) | `evidence/citation_gate.py` | Annotates every evidence-backed item: `citation_fidelity_ok` (offset+SHA in the normalized body), `support_score` (optional), `weak_evidence`. Zero network with the reranker off. |
| Reranker support scoring | `llm/fleet.py` `RerankerClient` | Behind `reranker.enabled` (default **off**; D4/PC-2 approved, live flip waits for EP-14). Endpoint `/rerank` (probe-verified; `reranker.endpoint_path` flips it without code). Spent on **low-confidence items only**, ≤ `budget_per_run` (10); model `bge-reranker-v2-m3` on its own 10 RPM bucket + stage budget via `RateBroker`. Any failure (429/timeout/budget) → gate turns fidelity-only for the rest of the run. |
| Repair (quarantine rescue) | `evidence/repair.py` | Behind `judge.enabled` (default **off**). Re-selects a **verbatim** span (`reselect_span`, non-generative) and accepts it only if the **cross-model** judge (`judge.model` ≠ `llm.model`, R4) approves above `reranker.tau_repair`. Judge rides `LLMGateway.judge()` with a model override (R1), stage budget `judge=8`. Failures keep the weak badge; budget exhaustion stops further attempts. |
| Quarantine | `run.py _quarantine_weak_items` | D1: items still `weak_evidence` after repair move to the trailing **«Не подтверждено»** section — withheld from main sections, still delivered. Repaired items escape it. |
| Eval judge (offline only) | `eval/judge.py` | `eval.judge_mode` (default `pointwise` = advisory dashboard; **never a gate** — research-refuted pattern). `reference` = reference-anchored binary judging vs gold rows (`eval-judge-run`: calibration κ/α + regression report). Pairwise = library only, consumed by EP-10 selection. **No-gate rule:** nothing gates CI until reactions-based calibration clears κ ≥ 0.41 with the bootstrap CI floor (D2/EP-15). |
| Best-of-N selection (EP-10) | `llm/best_of_n.py` | Behind `extract.best_of_n` (default **1** = single-shot). Candidate 1 deterministic; 2..N sampled at `extract.sample_temperature` on the extractor's bucket/budget (ADR-008 v2: raise `llm.stage_call_budgets.extractor` alongside — the default budget degrades sampling back to N=1). The gate selects by offset-verifiable support recall, fidelity-only (zero fleet spend); ties prefer the deterministic candidate; the pairwise judge breaks only EXACT ties when wired (corp). Sampling failures keep the gathered candidates; disabled under `--replay-llm`. Offline proof: `eval-best-of-n` (the selector never loses to N=1; archived under `docs/audits/baselines/`). Live N/temp tuning `requires corp validation` (EP-14). |

Record/replay: fleet calls use a **`<recording>.fleet.json` sidecar** next to the
LLM recording (namespaced per endpoint); under `--replay-llm` without a sidecar
the reranker is disabled for the run, and the repair judge is always disabled
under replay (no judge channel in LLM recordings yet — EP-14 design item).
`run_meta` reports `fleet_reranker_calls` / `fleet_judge_calls` (D6 visibility).

---

## 5. Configuration

### 5.1 Config Schema

```yaml
time:
  user_timezone: "Europe/Moscow"          # IANA timezone
  window: "calendar_day"                  # calendar_day | rolling_24h

ews:
  endpoint: "https://ews.corp.com/EWS/Exchange.asmx"
  user_upn: "user@corp.com"
  password_env: "EWS_PASSWORD"            # ENV var name for password
  verify_ca: "/etc/ssl/corp-ca.pem"       # Corporate CA cert path (optional)
  autodiscover: false
  folders: ["Inbox"]
  lookback_hours: 24
  page_size: 100
  sync_state_path: ".state/ews.syncstate"
  calendar_lookahead_days: 1               # calendar source: forward window (1 = today only)
  calendar_max_events: 100                 # calendar source: per-run safety cap

llm:
  endpoint: "https://llm-gw.corp.com/api/v1/chat"
  model: "qwen35-397b-a17b"                   # Target production model
  timeout_s: 120                           # 397B model may be slower; was 45
  headers: {}                              # Extra headers for LLM Gateway
  max_tokens_per_run: 30000                # Safety limit
  cost_limit_per_run: 5.0                  # USD safety limit (NOT enforced yet)
  rate_limit_rpm: 15                       # Gateway rate limit (requests/min)

deliver:
  mattermost:
    enabled: true                            # Enable MM delivery
    webhook_url_env: "MM_WEBHOOK_URL"        # ENV var name for webhook URL
    # --- Bot API (Phase 1, alternative to webhook) ---
    # bot_token_env: "MM_BOT_TOKEN"
    # api_url: "https://mm.corp.com/api/v4"
    # channel_id: ""                         # DM channel ID (auto-resolve later)
    max_message_length: 16383                # MM limit
    include_trace_footer: true               # DEPRECATED no-op (C5/C8): MM message is recipient-facing, trace/budget are operator-only

observability:
  prometheus_port: 9108
  log_level: "INFO"

reranker:                                  # P2-gate support scoring (EP-12, D4)
  enabled: false                           # OFF until corp validation (EP-14)
  tau: 0.0
  budget_per_run: 10
  low_confidence_threshold: 0.7
  quarantine_weak: true                    # D1: weak -> «Не подтверждено»

judge:                                     # cross-model repair judge (EP-12, D1)
  enabled: false                           # OFF until corp validation (EP-14)
  model: "qwen35-35b-a3b"                  # must differ from llm.model (R4)

eval:
  judge_mode: "pointwise"                  # D5: pointwise (advisory) | reference

extract:                                   # best-of-N extraction (EP-10, ADR-008 v2)
  best_of_n: 1                             # 1 = today's single-shot (default)
  sample_temperature: 0.7                  # candidates 2..N only
```

### 5.2 Config Precedence

**Целевой порядок (от низшего к высшему):**

1. Значения по умолчанию в Pydantic-моделях
2. `configs/config.example.yaml`
3. `configs/config.yaml`
4. YAML по пути из `DIGEST_CONFIG_PATH` (если задан)
5. Переменные окружения и `.env` через `pydantic-settings` при создании `Config`

**Реализация в коде (`config.py`):** сначала выполняется `BaseSettings.__init__` (defaults + `.env` + env), затем по очереди накладываются YAML-файлы через `_apply_yaml_config()` → `_merge_model()`. Для каждого поля, перед применением YAML-значения, проверяется наличие переопределения через ENV, и если ENV задан — YAML значение **пропускается**.

Используются два механизма ENV-override (см. `_merge_model()` в `config.py`):
- **Explicit `env_field_map`** для обратной совместимости: EWS (`EWS_ENDPOINT`, `EWS_USER_UPN`, `EWS_USER_LOGIN`, `EWS_USER_DOMAIN`) и LLM (`LLM_ENDPOINT`).
- **Generic `env_prefix`** на каждой секции (`DIGEST_<PREFIX>_<FIELD>`): TIME, EWS, LLM, MM (deliver.mattermost), OBS (observability), SEL_BUCKETS, SEL_WEIGHTS, **CTX_BUDGET** (YAML-ключ `context_budget`), CHUNKING, SHRINK, HIERARCHICAL, EMAIL_CLEANER, NLP, RANKER, DEGRADE. Например: `DIGEST_LLM_TIMEOUT_S=300` переопределит `llm.timeout_s` в YAML; `DIGEST_CTX_BUDGET_MAX_TOTAL_TOKENS` — поле `max_total_tokens`.

Пароль EWS (`EWS_PASSWORD`) и токен LLM (`LLM_TOKEN`) читаются напрямую из ENV в `get_password()`/`get_token()` и в YAML не мержатся вообще.

**Status:** TD-003 закрыт в Phase 0 (см. §13.1) — каждое поле в nested-конфиге имеет хотя бы один корректный env-override путь через generic prefix.

### 5.3 Secrets (ENV only, never in YAML)

| Variable | Required | Description |
|----------|----------|-------------|
| `EWS_PASSWORD` | Yes | EWS/NTLM password |
| `LLM_TOKEN` | Yes | LLM Gateway Bearer token |
| `MM_WEBHOOK_URL` | No* | Mattermost incoming webhook URL (*required if deliver.mattermost.enabled) |
| `MM_BOT_TOKEN` | No | Mattermost bot token (Phase 1, alternative to webhook) |
| `DIGEST_CONFIG_PATH` | No | Path to custom config YAML |

Каталог вывода и state по умолчанию живут в **data home** (U5, 2026-06-12): `<data home>/var/out` и `<data home>/var/state`, где data home = `$ACTIONPULSE_HOME` → корень checkout-а → `~/.local/share/actionpulse` (см. `digest_core/paths.py` и `actionpulse paths`). Флаги **`--out`** и **`--state`** (см. Appendix B) переопределяют дефолты; отдельные `DIGEST_*_DIR` переменные **не читаются** пайплайном (в шаблонах env могут встречаться закомментированные примеры). Уровень логирования: **`--log-level`**, не `DIGEST_LOG_LEVEL`.

---

## 6. Observability

### 6.1 Prometheus Metrics (port 9108)

**Canonical source:** `digest_core/observability/metrics.py` — `MetricsCollector._init_metrics`. Все объявленные там серии попадают в registry на порту **9108** (по умолчанию). Имена и labelsets ниже краткие; точное определение — только в коде.

**На пути `digest_core.cli run`:** обновляются LLM-, pipeline-, email- и evidence-счётчики, стадийные гистограммы, `runs_total`, при partial после сбоя LLM — `degradations_total` и др. **Не каждая** серия инкрементится на каждом запуске (серии под иерархический режим, часть ranking-метрик и т.д. могут оставаться нулевыми, пока соответствующий код не вызывает `record_*`).

**Что вызывает `run.py` напрямую:** `record_emails_total`, `record_pipeline_stage_duration` (по стадиям), `record_llm_latency`, `record_llm_tokens`, `record_run_total`, `record_digest_build_time`, при сбое LLM — `record_degradation`. **`LLMGateway`** при невалидном JSON в теле ответа может вызвать **`record_llm_json_error`**. При переданном в нормализацию **`MetricsCollector`** считаются **`html_*`** счётчики в `normalize/html.py`. Методы вроде **`record_evidence_chunks`**, **`record_threads`**, **`record_hierarchical_run`**, ranking-`record_*` объявлены на **`MetricsCollector`**, но **из `run.py` не вызываются** (ищите `record_` по `src/digest_core/`).

#### Основные: pipeline и LLM

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `llm_latency_ms` | Histogram | — | Latency LLM-запроса (мс) |
| `llm_tokens_in_total` | Counter | — | Входные токены (из метаданных gateway) |
| `llm_tokens_out_total` | Counter | — | Выходные токены |
| `llm_request_context_total` | Counter | `model`, `operation` | Низкокардинальный контекст вызова (см. `record_llm_latency`) |
| `llm_json_error_total` | Counter | — | Ошибки разбора JSON в теле ответа LLM |
| `llm_repair_fail_total` | Counter | — | Неудачи пути «починки» JSON |
| `digest_build_seconds` | Summary | — | Длительность полного прогона |
| `runs_total` | Counter | `status` | Исходы run (`ok`, `retry`, `failed`, …) |
| `pipeline_stage_duration_seconds` | Histogram | `stage` | Длительность стадий |
| `emails_total` | Counter | `status` | Воронка ingest/normalize |
| `evidence_chunks_total` | Counter | `stage` | Чанки по стадиям (`created`, `selected`, …) |
| `threads_total` | Counter | `status` | Статистика тредов |
| `errors_total` | Counter | `error_type`, `stage` | Ошибки по типу и стадии |
| `degradations_total` | Counter | `reason` | Например `llm_failed` при partial digest |

#### Доставка (Mattermost / файлы)

**Отдельных счётчиков `delivery_total` / `delivery_errors_total` в `metrics.py` нет.** Успех/сбой доставки отражаются **structured logs** и при необходимости **`delivery_receipt`** в метаданных run (`run.py`, `deliver/mattermost.py`). См. ADR-011.

#### Расширенная инструментализация (выборочно)

| Область | Метрики (labels — в коде) |
|---------|---------------------------|
| Email cleaner | `email_cleaner_removed_chars_total`, `email_cleaner_removed_blocks_total`, `cleaner_errors_total` |
| Citations / actions | `citations_per_item_histogram`, `citation_validation_failures_total`, `actions_found_total`, `mentions_found_total`, `actions_confidence_histogram`, `actions_sender_missing_total` |
| Threading | `threads_merged_total`, `subject_normalized_total`, `duplicates_found_total`, `redundancy_index` (Gauge) |
| Ranking (`DigestRanker`) | `rank_score_histogram`, `top10_actions_share`, `ranking_enabled` — **§4.3:** `run.py` вызывает ранкер только при **`ranker.enabled`**; отдельные ranking-метрики в Prometheus по-прежнему могут не инкрементиться из daily `run` (проверять `metrics.py` / вызовы `record_*`) |
| Hierarchical | `hierarchical_runs_total`, `avg_subsummary_chunks`, `saved_tokens_total`, `must_include_chunks_total` — не дефолтный daily `run` |
| HTML normalize | `html_parse_errors_total`, `html_hidden_removed_total` |
| Прочее | `validation_error_total`, `tz_naive_total`, `system_uptime_seconds`, `memory_usage_bytes` |

### 6.2 Structured Logging

- **Library:** structlog (JSON renderer)
- **Output:** console + file (`~/.digest-logs/run-{timestamp}.log`)
- **Context:** every log entry includes `trace_id`, `stage`
- **PII redaction:** passwords, tokens, SSN, credit cards redacted in logs
- **Email addresses:** NOT redacted in local logs (policy decision)

### 6.3 Health Endpoints (port 9109)

| Endpoint | Success | Failure |
|----------|---------|---------|
| `GET /healthz` | 200 `{"status": "healthy"}` | — |
| `GET /readyz` | 200 `{"status": "ready", "components": {...}}` | 503 `{"status": "not_ready"}` |

Readiness checks: LLM Gateway connectivity (if configured).

---

## 7. Idempotency Model

```
run_digest("2026-03-29", ...)
    │
    ├── digest-2026-03-29.json exists?
    │       │
    │       ├── YES + age < 48h → SKIP (return existing)
    │       ├── YES + age ≥ 48h → REBUILD
    │       └── NO → BUILD
    │
    ▼
  fetch emails (may use watermark for incremental window)
    │
    ▼
  process pipeline → write artifacts
    │
    ▼
  update watermark (.state/ews.syncstate = end_date ISO)
```

**Override:** флаг CLI `--force` обходит проверку идемпотентности (пересборка даже при «свежих» артефактах).

**Return value:** успешный вызов `run_digest()` возвращает **`RunDigestResult`** (не `bool`): для проверки успеха используйте truthiness объекта; для гейта цитат при `--validate-citations` смотрите **`citation_validation_ok`**.

**Known limitation:** Race condition при параллельных запусках. Два процесса
(`cron` overlap, manual + cron) могут оба пройти проверку `json_path.exists()` и
оба записать файл. Для single-user CLI маловероятно. Если станет проблемой —
добавить file lock (`fcntl.flock` или `filelock` package).

---

## 8. Error Taxonomy

Каждая стадия может упасть. Таблица определяет текущее поведение и целевое (P5).

| Стадия | Error Type | Текущее поведение | Целевое (Phase 0, P5) |
|--------|-----------|-------------------|-----------------------|
| **1. Ingest** | EWS auth failure (401/403) | 8 retries → exception → crash | Partial report: "EWS: authentication failed" banner, exit 1 |
| **1. Ingest** | EWS timeout / network | 8 retries → exception → crash | Same as above |
| **1. Ingest** | 0 emails fetched | Continues (empty pipeline) | Valid empty digest: "Новых писем нет" |
| **2. Normalize** | Malformed HTML | BS4 handles gracefully | OK (no change needed) |
| **3. Threads** | Empty input | Returns `[]` | OK (no change needed) |
| **4. Evidence** | No chunks created | Returns `[]` | OK, flows to empty digest |
| **5. Select** | All chunks filtered / empty input | Returns `[]` | OK, flows to empty digest |
| **6. LLM** | HTTP 429 (rate limit) | `RetryableLLMError` → до 2 попыток с ожиданием (`Retry-After` или дефолт), затем partial digest | OK (см. `gateway.py`, бюджет вызовов в рамках лимита run) |
| **6. LLM** | HTTP 5xx (server error) | Повтор с backoff (через `RetryableLLMError`), затем partial digest | OK |
| **6. LLM** | HTTP timeout | После исчерпания ретраев → partial digest с текстом про таймаут | OK (`_build_partial_digest` в `run.py`) |
| **6. LLM** | Invalid JSON (unparseable body) | `json.loads` failure → `RetryableLLMError` → до 2 HTTP-попыток внутри `_make_request_with_retry`, с подсказкой strict JSON в system prompt. **`extractive_fallback` из `degrade.py` на этом пути не вызывается.** После исчерпания ретраев — исключение → **`_build_partial_digest`** в `run.py`. См. примечание ниже про `process_digest`. | OK |
| **6. LLM** | Empty sections (no actions found) | Quality retry если есть позитивные сигналы | OK (реализовано) |
| **7. Assemble** | Disk write failure | Exception → crash | Log error, attempt alternate path or fail with clear message |
| **7. Assemble** | Word count > 400 | Truncate with Russian marker `*[Содержимое обрезано для соблюдения лимита слов]*` (`assemble/markdown.py`) | OK (implemented) |
| **8. Deliver** | MM webhook unreachable | `logger.warning()`, exit 0. Файлы уже сохранены | OK (ADR-011, `mattermost.py`) |
| **8. Deliver** | MM message too long (>16383) | Дробление на несколько сообщений | OK (`MattermostDeliverer`) |
| **8. Deliver** | MM webhook returns 4xx | Warning / лог; без ретрая (конфиг) | OK |
| **CLI** | `--validate-citations` | После LLM: цитаты пересобираются из **выбранных** evidence-чанков, проверяются `CitationValidator`; результат в JSON пунктов; `run_meta.citation_validation_ok`; при провале CLI exit **2** (артефакты уже записаны). См. `docs/development/CITATIONS.md`, `digest-core/CLAUDE.md`. | OK (Phase 0) |

**Partial report format (при сбое LLM):**

Реализовано в `_build_partial_digest()` (`run.py`).

**`extract_actions` (default daily run) vs `process_digest`:** типичный `run` вызывает только **`LLMGateway.extract_actions`**. При любом необработанном исключении там пайплайн переходит к partial digest **без** `degrade.extractive_fallback`. Модуль **`degrade.py`** и **`extractive_fallback`** используются в **`LLMGateway.process_digest`** (legacy / enhanced digest path), в т.ч. при `enable_degrade=True` и отсутствии `custom_input` — это **отдельный** контракт от дневного `extract_actions`.

**`process_digest` + `custom_input` (hierarchical final aggregation):** если передан **`custom_input`**, ветка с **`extractive_fallback` отключена** — при ошибке LLM исключение пробрасывается наверх (см. `gateway.py`). Не смешивать с поведением «обычного» `process_digest` без `custom_input`.

Пример формы partial JSON:
```json
{
  "schema_version": "1.0",
  "prompt_version": "none",
  "digest_date": "2026-03-29",
  "trace_id": "...",
  "sections": [
    {
      "title": "Статус",
      "items": [{
        "title": "LLM Gateway недоступен. Дайджест неполный.",
        "evidence_id": "system",
        "confidence": 0.0,
        "source_ref": {"type": "system", "error": "HTTP 503"}
      }]
    }
  ]
}
```

---

## 9. Prompt Strategy & Section Taxonomy

### 9.1 Prompt File Inventory

| File | Format | Stage | Used in `run.py`? | Status |
|------|--------|-------|-------------------|--------|
| `prompts/extract_actions.v1.txt` | Plain text | 6 (LLM) | **Yes** — default RU prompt | Active |
| `prompts/extract_actions.en.v1.txt` | Plain text | 6 (LLM) | **Yes** — EN variant for qwen models | Active |
| `prompts/extract_actions.v1.changelog` | Text | — | No (documentation only) | Reference |
| `prompts/thread_summarize/v1/default.j2` | Jinja2 | 6 (LLM) | **No** — `hierarchical/processor.py` only | Active (experimental) |

**Dead entries in `prompt_registry.py`** (files do not exist on disk):

| Registry key | Mapped path | Status |
|-------------|-------------|--------|
| `summarize.mvp.5` / `summarize.mvp5` | `summarize/mvp/v5/default.j2` | Dead — file removed |
| `summarize.v2` / `summarize.v2_hierarchical` | `summarize/v2/default.j2` | Dead — file removed |
| `summarize.v1` | `summarize/v1/default.j2` | Dead — file removed |
| `summarize.en.v1` | `summarize/v1/en.j2` | Dead — file removed |

**Loading mechanism:** `run.py` calls `get_prompt_template_path()` from `prompt_registry.py`,
then reads the file via `Path.read_text()`. Jinja2 rendering is NOT used for extraction prompts (ADR-009);
only `thread_summarize` in the hierarchical processor uses Jinja2.

> **Not prompt-driven:** the **Meetings** section (calendar source) is assembled deterministically
> in `run._enrich_digest_with_meetings()` — no LLM, no prompt (§9.3).

### 9.2 Prompt Design Decisions

**Decision: Two-step pipeline is NOT needed for MVP.**

- `extract_actions` → structured JSON (LLM does extraction)
- Markdown assembled **programmatically** from JSON (deterministic, no LLM)

This is the correct approach:
- Deterministic formatting (always valid MD)
- Lower LLM cost (one call, not two)
- Easier to test (MD assembly is pure function)

**Status:** Старые `summarize*.j2` файлы удалены в Phase 0 (см. §13.1 TD-007 и §9.1
"Dead entries in `prompt_registry.py`"). Исторические записи в registry оставлены
как маркеры для отладки регрессий.

### 9.3 Section Taxonomy

Промпт должен инструктировать LLM использовать фиксированный набор секций.
Секции, не входящие в контракт фазы, должны быть проигнорированы assembler-ом.

**MVP (Phase 0-1) — обязательные секции:**

| Section title (RU) | Назначение | Когда создаётся |
|--------------------|-----------|-----------------|
| **Мои действия** | Конкретные задачи/просьбы, адресованные получателю | Есть actionable items |
| **Срочное** | Дедлайны ≤ 2 рабочих дней, urgent-маркеры | Есть urgent items |
| **К сведению** | Информация без required action, но важная | Есть FYI items |

**Phase 2 — добавляется:**

| Section title (RU) | Назначение |
|--------------------|-----------|
| **Упоминания** | Места, где пользователь упомянут по имени/алиасу |

**Phase 3 — добавляется:**

| Section title (RU) | Назначение |
|--------------------|-----------|
| **Темы из каналов** | Кластеры сообщений из MM public channels |

**Детерминированные / store-derived секции (НЕ от LLM, собираются в `run.py`):**

| Секция (RU / key) | Источник | Когда |
|-------------------|----------|-------|
| **Встречи** (`meetings`) | календарь (`--sources calendar`), сортировка по началу, ⚠ при наложении | есть события на сегодня (E2/E3) |
| **Ждут вашего ответа** (`pending`) | store carryover | `store.pending` + история |
| **Открытые вопросы** (`open_loops`) | store carryover | `store.carryover` + история |

**Правила:**
- Пустые секции не включаются в output
- Если все секции пустые → "За период релевантных действий не найдено"
- Assembler должен принимать **любые** section titles от LLM, но сортировать
  по каноническому весу (`assemble/labels.py` `SECTION_ORDER_BY_KEY`): Срочное →
  Мои действия → Встречи → Ждут вашего ответа → Открытые вопросы → К сведению → остальные

---

### 9.4 Prompt quality (ongoing)

Промпт `extract_actions.v1.txt` расширен (≈180+ строк): таксономия секций, жёсткий JSON-контракт, few-shot, калибровка confidence, edge cases (пустой evidence, несколько действий в chunk). Дальнейшая полировка — через dogfooding и замеры качества, а не через смену формата без причины.

Исторический чеклист из Phase 0 (см. `PHASE0_PROMPT.md`) описывал состояние **до** мержа hardening; не использовать его как proof того, что код всё ещё отсутствует.

---

## 10. File & Directory Structure

```
digest-core/
├── configs/
│   ├── config.example.yaml        # Reference config (committed)
│   └── config.yaml                # User config (gitignored)
├── deploy/
│   ├── actionpulse-digest.service # systemd user service unit
│   ├── actionpulse-digest.timer   # systemd timer (daily 08:00)
│   ├── crontab.example            # cron alternative
│   ├── env.example                # Environment variables template
│   └── install-systemd.sh         # One-command systemd install
├── docker/
│   └── Dockerfile                 # Multi-stage, non-root (UID 1001)
├── docs/
│   ├── ARCHITECTURE.md            # THIS FILE
│   ├── DEPLOYMENT.md              # Deployment guide (CI, cron, systemd, Docker)
│   └── PHASE0_PROMPT.md           # Historical Phase 0 backlog prompt (snapshot)
├── prompts/
│   ├── extract_actions.v1.txt     # RU extraction prompt (plain text)
│   ├── extract_actions.en.v1.txt  # EN extraction prompt
│   └── thread_summarize/v1/default.j2  # Used by hierarchical path via registry
├── scripts/
│   ├── run-local.sh               # Local execution helper
│   ├── test.sh, lint.sh           # Dev scripts
│   ├── build.sh, deploy.sh        # Build/deploy
│   ├── smoke.sh                   # Smoke tests
│   ├── collect_diagnostics.sh     # Log collection
│   ├── print_env.sh               # Environment diagnostics
│   └── rotate_state.sh            # State management
├── src/digest_core/
│   ├── __init__.py
│   ├── cli.py                     # Typer CLI entry point
│   ├── run.py                     # Pipeline orchestration
│   ├── config.py                  # Pydantic config
│   ├── ingest/
│   │   └── ews.py                 # Exchange EWS adapter
│   ├── normalize/
│   │   ├── html.py                # HTML → text
│   │   └── quotes.py              # Quote/signature removal
│   ├── threads/
│   │   └── build.py               # Thread grouping
│   ├── evidence/
│   │   ├── __init__.py
│   │   ├── split.py               # Evidence chunking (`EvidenceChunk`, `EvidenceSplitter`)
│   │   ├── signals.py             # Heuristic signals on text
│   │   ├── actions.py             # Action / mention extraction helpers
│   │   ├── citations.py           # CitationBuilder / CitationValidator (wired when `--validate-citations`)
│   │   ├── citation_gate.py       # P2 shadow gate: offset fidelity + support_score (§4.6)
│   │   ├── repair.py              # Non-generative weak-item repair, cross-model judge (§4.6)
│   │   └── lemmatizer.py          # RU lemmatization for NLP-style helpers
│   ├── select/
│   │   └── context.py             # Context selection/scoring
│   ├── llm/
│   │   ├── gateway.py             # LLM HTTP client (+ judge() verdict call, §4.6)
│   │   ├── fleet.py               # Fleet clients: embeddings / reranker / tokenizer (§4.6)
│   │   ├── rate_broker.py         # Per-model RPM buckets + per-stage call budgets (ADR-008 v2)
│   │   ├── best_of_n.py           # Gate-as-selector over N extraction candidates (EP-10, §4.6)
│   │   └── schemas.py             # Pydantic output schemas
│   ├── eval/                      # Offline eval harness (no live-run coupling)
│   │   ├── replay_harness.py      # eval-replay: frozen corpus vs committed baseline
│   │   ├── best_of_n_harness.py   # eval-best-of-n: EP-10 selector proof on the corpus
│   │   ├── judge.py               # Hybrid judge: pointwise / reference-anchored / pairwise (D5)
│   │   ├── agreement.py           # Cohen κ / Krippendorff α + may_gate floor (EP-5)
│   │   ├── gold_set.py            # MM-reactions gold labels
│   │   └── calibrate.py           # Per-stratum tau calibration
│   ├── assemble/
│   │   ├── jsonout.py             # JSON output writer
│   │   └── markdown.py            # Markdown output writer
│   ├── deliver/                   # Phase 0: delivery targets
│   │   ├── __init__.py
│   │   └── mattermost.py         # MM incoming webhook / Bot API
│   └── observability/
│       ├── logs.py                # Structured logging
│       ├── metrics.py             # Prometheus metrics
│       └── healthz.py             # Health check server
├── tests/
│   ├── fixtures/
│   │   ├── emails/                # Email test fixtures (10 files)
│   │   ├── emails.json            # Fixture data
│   │   ├── config_calendar_day.yaml
│   │   ├── config_rolling_24h.yaml
│   │   └── generate_fixtures.py   # Fixture generator
│   ├── mock_llm_gateway.py
│   ├── test_cli.py
│   ├── test_empty_day.py
│   ├── test_evidence_split.py
│   ├── test_ews_ingest.py
│   ├── test_idempotency.py
│   ├── test_llm_contract.py
│   ├── test_llm_gateway.py
│   ├── test_llm_integration.py
│   ├── test_markdown_json_assemble.py
│   ├── test_masking.py
│   ├── test_normalize.py
│   ├── test_observability.py
│   ├── test_pii_policy.py
│   ├── test_selector.py
│   └── test_smoke_cli.py
├── pyproject.toml                 # Dependencies & build config
├── Makefile                       # Dev workflow targets
└── README.md
```

**Документы снаружи `digest-core/docs/`:** в корне монорепозитория см. `docs/planning/BUSINESS_REQUIREMENTS.md`, `docs/development/TECHNICAL.md`, каталог `docs/testing/`. Файлы с историческими именами `Bus_Req_v5.md` и `Tech_details_v1.md` в дереве не версионируются (ссылки на них в старых инструкциях были устаревшими).

---

## 11. Dependencies (locked)

| Package | Version | Purpose |
|---------|---------|---------|
| typer | ≥0.12 | CLI framework |
| pydantic | ≥2.7 | Data validation |
| pydantic-settings | ≥2.4 | Configuration management |
| structlog | ≥24.1 | Structured logging |
| httpx | ≥0.27 | HTTP client (LLM Gateway) |
| exchangelib | ≥5.3.6 | EWS client (NTLM) |
| tenacity | ≥9.0 | Retry logic |
| prometheus-client | ≥0.20 | Metrics |
| beautifulsoup4 | ≥4.12 | HTML parsing |
| pytz | ≥2023.3 | Timezone handling |
| pyyaml | ≥6.0 | YAML config parsing |
| jinja2 | ≥3.1 | Template engine for **`process_digest`** / hierarchical prompts (`.j2` under `prompts/`); **extraction** prompts `.txt` остаются plain text (ADR-009) |
**Not adding (and why):**
- `tiktoken` — approximate `words * 1.3` is sufficient at configured `max_total_tokens` scale (default 7000); ±10% error is acceptable for chunk budgeting
- `faiss` / `sentence-transformers` — rule-based context selection handles ≤100 emails
- `celery` / `rq` — single-user batch tool, no task queue needed
- `sqlalchemy` — no database, file-based state is sufficient
- `fastapi` — no API server needed (CLI + cron)

---

## 12. Architecture Decisions Record (ADR)

### ADR-001: Programmatic MD assembly (not LLM)
- **Decision:** Markdown generated from JSON by code, not by LLM
- **Rationale:** Deterministic, testable, cheaper, no hallucination risk
- **Consequence:** `summarize.v1.j2` is dead code → remove

### ADR-002: Single-step LLM extraction (no multi-step pipeline)
- **Decision:** One extraction call, not multi-step (extract → summarize → format).
  Quality retry (max +1 call) is allowed within the same step — see ADR-008.
- **Rationale:** Latency, cost, complexity. One 2000-token response is sufficient
- **Consequence:** Prompt must be high-quality to compensate for single-step

### ADR-003: Rule-based context selection (not embeddings)
- **Decision:** Keyword scoring + filtering, no vector embeddings
- **Rationale:** ≤100 emails/day, rule-based is sufficient and fast
- **Revisit when:** >500 messages/day or cross-platform dedup (LVL3)

### ADR-004: Timestamp watermark (not EWS SyncFolderItems)
- **Decision:** Watermark = ISO timestamp of last processed batch
- **Rationale:** Simpler, portable, works across restarts
- **Limitation:** May miss messages arriving with past timestamps (rare for email)
- **Revisit when:** Missed messages become a measurable problem

### ADR-005: No pipeline abstraction (yet)
- **Decision:** Linear function calls in `run.py`, no stage registry/protocol
- **Rationale:** One source, one pipeline path. Abstraction cost > benefit
- **Expires:** Phase 2 start. Phase 2 roadmap включает "Pipeline refactoring:
  composable stages" (8h). После этого ADR-005 заменяется новым ADR.

### ADR-006: Email addresses NOT masked locally
- **Decision:** Email addresses remain visible in local artifacts and logs
- **Rationale:** They are non-sensitive in corporate context; masking adds noise
- **Masking boundary — CORRECTED 2026-06 (was fictional):** there is **no** gateway-side
  redaction. `x-redaction-policy: strict` is **NOT sent** — the header was never wired
  (verified). Evidence text (incl. emails/names) reaches the LLM gateway as-is; the corp
  gateway's own non-logging policy is the only inference-time control. The PC-2
  data-handling ADR (`PC2_DATA_HANDLING.md`, DRAFT) formalizes this per endpoint.
- **Other PII:** phones, SSN, credit cards, names, IPs — masked **in structured logs only**,
  never in the evidence sent to the model.

### ADR-007: Russian as primary output language
- **Decision:** Digest output in Russian, prompt switches to EN for qwen models
- **Rationale:** Corporate environment is RU-first
- **Consequence:** All section titles, confidence labels, empty-day messages in Russian

### ADR-008: Per-stage LLM call budgets at the real gateway ceilings (rev. 2026-06-11, D6)
- **Decision (v2):** Лимиты вызовов задаются **per-stage бюджетами RateBroker**
  (`llm.stage_call_budgets`: extractor=2, reranker=10, embeddings=30, judge=8, tokenize=20),
  а не прозой «max 2 calls per run». Бюджеты можно поднимать **до реальных потолков
  гейтвея** (key-budget 15 RPM на флагмане; **3 параллельных запроса**; ~30 s латентность
  на вызов; token-budget `max_tokens_per_run`), когда задача того требует (например
  best-of-N extraction, EP-10) — повышение фиксируется в конфиге, не в коде.
- **Visibility (часть решения, сужено owner C5/C8):** каждый run обязан показывать
  **оператору** фактический расход — call count и token usage против бюджета
  (`run_meta.llm_budget` + структурный лог). Невидимый бюджет — не бюджет.
  Доставляемое в Mattermost сообщение — recipient-facing: бюджет/trace там больше
  не печатаются (раньше был trace-footer; теперь это operator-only поверхности).
- **Rationale:** 15 RPM — бюджет *ключа*, а не свойство пайплайна: дневной batch с
  N=3 последовательными extraction-вызовами (~30 s каждый) тривиально укладывается.
  Реальные ограничители — параллелизм (3) и латентность; их моделирует RateBroker,
  и он же это **enforce'ит** — ADR теперь описывает то, что код делает.
  (Источник лимитов: консолидированный справочник эндпоинта, 2026-06-11.)
- **Defaults unchanged:** extractor budget остаётся 2 (1 primary + 1 quality retry);
  поведение по умолчанию не меняется. Подъём extractor-бюджета — только вместе с
  `extract.best_of_n` (EP-10), после offline-доказательства выигрыша на citation-recall.
- **History (v1, 2026-03):** «Max 2 LLM calls per run; no multi-step prompting» —
  написано, когда дормантный `hierarchical/` грозил исчерпать RPM; пакет удалён в
  redesign-cleanup. Формулировка v1 сохранена в git history.
- **Revisit when:** меняются key-бюджеты гейтвея или появляется второй endpoint.

### ADR-009: Extraction prompts are plain text (Jinja2 elsewhere only)
- **Decision:** Extraction prompts (`extract_actions.v1.txt`, `extract_actions.en.v1.txt`)
  загружаются через `Path.read_text()`, **без** Jinja2. Переменные шаблона (`{{ }}`)
  в extraction prompt не используются.
- **Rationale:** Текст extraction — статический, проще ревью и воспроизводимость.
  Отдельно: **Jinja2** подключён в зависимостях для **`LLMGateway.process_digest`**
  и **hierarchical** путей (`.j2` под `prompts/`) — см. §11. Это не противоречит
  отказу от шаблонизатора на hot-path `extract_actions`.
- **Status:** Phase 0 (TD-010): extraction — `.txt`. Dynamic extraction через Jinja2
  не планируется без отдельного ADR.
- **Note:** `prompts/thread_summarize/v1/default.j2` — hierarchical processor
  (не вызывается из `run.py` в MVP). Не путать с extraction prompts.

### ADR-010: Mattermost as primary delivery channel (not Web UI)
- **Decision:** Дайджест доставляется через **Mattermost incoming webhook** (канал,
  заданный при создании webhook) или, в Phase 1+, Bot API. Продуктовый UX — push
  в клиент MM (часто личный/«как DM»), а не отдельный Web UI.
- **Rationale:**
  - Дайджест — push-продукт ("приходит к тебе"), а не pull ("ты идёшь к нему").
    Если надо помнить "зайти на страницу" — через неделю перестанешь.
  - Mattermost — push в клиент, который и так открыт весь день (desktop + mobile).
  - Web UI для одного пользователя = FastAPI + templates + auth + TLS + процесс.
    MM webhook = один `httpx.post()`.
  - Feedback loop: реакции (👍/👎) на сообщение бесплатны. В web UI надо строить UI.
- **Phase 0:** Incoming Webhook (простейший вариант, 4-6h).
- **Phase 1:** Миграция на Bot API для slash commands (`/digest today`).
- **Revisit when:** Появится потребность в навигации по истории дайджестов,
  drill-down в evidence, или поиск по 30+ дайджестам. Тогда — lightweight web UI.
- **Consequence:** No `fastapi` dependency. Delivery failure = warning, not crash.

### ADR-013: Terminal surfaces follow the design system
- **Decision:** All terminal output (installer, wizard, CLI, progress displays)
  follows `docs/development/TERMINAL_DESIGN.md`: semantic tokens come from
  `digest_core/ui` (never inline styles), live progress uses the split-region
  ProgressSink architecture (`run.py` emits events, sinks render; structlog
  stays a parallel channel), reports are English by default with
  `report.language: ru` as the user setting, no mouse reporting on
  line-oriented surfaces, and every renderer degrades append-only on
  non-TTY/CI (`--progress=auto|live|plain|none`).
- **Rationale:** The rules are evidence-tiered (deep-research over 24 primary
  sources + source reading of cargo/uv/BuildKit/Claude Code/rich, 2026-06-12);
  split-region is the verified cross-tool substrate. Structure beats policy:
  the `ui` module and `tests/test_terminal_conformance.py` enforce the rules
  in CI, so they cannot silently rot.
- **Consequence:** New surfaces import `ui` tokens and subscribe to
  `ProgressSink`; the conformance test gates `make test`/CI; the reviewer
  checklist lives in `CONTRIBUTING.md`; fleet lane rendering (REDESIGN PR2+)
  builds against design §4.3 from day one.

### ADR-012: "Code outside, run inside, debug outside" workflow
- **Decision:** Development and debugging happens on general network dev workstation.
  Real pipeline runs (EWS + LLM) happen only in corp network. Diagnostic bundles
  transferred via MM DM for analysis outside.
- **Rationale:** EWS and LLM Gateway accessible only from corp network. Developer
  productivity requires ability to iterate without being physically on corp network.
- **Consequence:**
  - Diagnostic export CLI (`export-diagnostics`) is P0 for Phase 0
  - EWS replay mode (`--dump-ingest` / `--replay-ingest`) is P0 for Phase 0
  - All CI tests use mocks only — no real EWS/LLM in CI
  - MM delivery testable from anywhere (MM accessible from general network)
  - LLM replay mode (`--record-llm` / `--replay-llm`) is P1 for Phase 1

### ADR-011: Delivery is best-effort (not transactional)
- **Decision:** Сбой доставки в MM не блокирует pipeline. File artifacts уже
  записаны Stage 7 — данные не теряются.
- **Rationale:** MM webhook может быть недоступен (maintenance, network).
  Артефакты на диске — source of truth. MM — convenience channel.
- **Consequence:** Delivery errors → `logger.warning()` + запись в `run_meta` / `delivery_receipt`.
  Отдельного Prometheus-счётчика доставки в MVP **нет** (см. §6.1). Pipeline exit code = 0 (success) даже при failed delivery.

### ADR-014: Persistent encrypted message store (opt-in)

- **Decision:** An optional `digest_core/store/` package persists fetched messages
  for ALL sources in a single **SQLCipher-encrypted** SQLite file, with FTS5
  keyword + brute-force-cosine (NumPy) semantic + RRF hybrid search. Default OFF
  (install-time via the `store` extra AND runtime via `store.enabled`). It is wired
  as a **non-fatal side-channel after Stage 2 NORMALIZE** — never a numbered pipeline
  stage, never able to fail the digest. See ADR-004 (the per-source timestamp
  watermark it shares for incremental load is now `ingest/watermark.py`).
- **Rationale:** Prepare fetched data for future lookups / chunking / vectorization
  / search without re-fetching. Encryption-at-rest lets the store keep a longer
  window than the plaintext artifacts. `sqlite-vec` is deliberately NOT used — it
  does not load into the SQLCipher fork; at the target scale (≤~100k rows)
  brute-force cosine is ~5–20 ms, so an ANN index is unnecessary.
- **Retention domains (three intentional numbers):** plaintext `var/out` = 7d
  (`retention.keep_days`, P3), hash-only dedup ledger = 7d (`memory.dedup_ttl_days`),
  **encrypted store = 30d** (`store.ttl_days`). The longer store window is justified
  *because* it is encrypted at rest; the others hold plaintext PD and stay short.
- **Privacy:** Guardrail #9 — a DM (mm channel type `D`/`G`) body is never persisted
  at rest. The store keeps the DM row + metadata but redacts the body
  (`[DM content redacted at rest]`, matching the `--dump-ingest` redaction) and does
  not chunk/embed it. Embeddings reuse the gateway `EmbeddingsClient` (bge-m3); the
  key is ENV-only (`DIGEST_STORE_KEY`); a lost key makes the DB unrecoverable.
- **Consequence:** New deps in the `store` extra only (`sqlcipher3`/-binary +
  `numpy`); the default install and `make test` stay driver-free (store tests are
  `skipif(not HAS_SQLCIPHER)`). CLI: `search` + the `store` sub-app.

---

### ADR-015: Local API surface (InboxAPI) + MCP server + AI-CLI installer (opt-in)

- **Decision:** A single local API facade `digest_core/api/InboxAPI` wraps the store's
  retrieval, search, insight, and reasoning verbs; `digest_core/mcp/` exposes it to AI
  coding CLIs over an **stdio MCP server** (`actionpulse-mcp`, the official `mcp` SDK /
  FastMCP, opt-in `mcp` extra); and `actionpulse mcp install` registers that server into
  Claude Code / opencode / qwen-code configs on macOS, with consent. The digest's
  cross-day enrichment (`run._enrich_digest_from_store`) reads its insights through the
  same facade — one surface, not ad-hoc `store.conn` access.
- **Exposure = FULL CONTENT by default.** Tools/resources return message bodies and RAG
  answers; `ACTIONPULSE_MCP_REDACT_BODIES=1` switches to metadata-only. This is a
  conscious trade: an MCP client may route to a **cloud** model, so connecting the server
  can egress corp message content to whatever model the CLI uses — a per-deployment
  decision, NOT a default the rest of the system makes (extraction / `ask` reach only the
  *corp* gateway). Store-mutating maintenance tools (sweep_ttl/embed/reembed/vacuum) are
  OFF unless `ACTIONPULSE_MCP_ENABLE_MAINTENANCE=1`.
- **Invariants preserved:** DM bodies stay redacted at rest (guardrail #9 / ADR-014), so
  `get_message`/`get_thread`/resources never surface DM text. The store key is read from
  the env (the 0600 `~/.config/actionpulse/env`) — NEVER written into a client config nor
  accepted as a tool argument (a test asserts no tool takes a key). Gateway verbs degrade
  honestly off-corp (search → keyword; `ask`/`summarize` → a clear `GatewayUnavailable`),
  never hang.
- **Installer safety:** idempotent (keyed on the fixed `actionpulse` server name),
  byte-exact timestamped `.bak` before any write, atomic temp+rename, never clobbers a
  sibling server or an unparseable config; macOS-gated; `--dry-run` previews the exact
  JSON; the registered command carries no secret (the server self-loads the key).
- **Consequence:** New deps in the `mcp` extra only (`mcp>=1.2`); the default install and
  `make test` stay SDK-free (`_build_app` imports the SDK lazily; MCP-registration tests
  skip without it, covered by a dedicated `test-mcp` CI lane).

---

## 13. Known Technical Debt

> ⚠ **STALE (~2026-03).** This table and §14 below predate the redesign / EP-program /
> U-track / Mattermost-source / message-store work and overstate what is "open". For the
> current forward plan and an accurate open-backlog inventory, see
> [`docs/planning/ROADMAP.md`](../../docs/planning/ROADMAP.md). Verify any row here against
> code before treating it as open.

Сводка ниже отражает **текущий** `main` (~2026-03). Исторические строки Phase 0 в старых версиях этого файла описывали бэклог до мержа hardening — не путать с открытыми задачами.

### 13.1 Снято в коде (Phase 0)

| ID / тема | Примечание |
|-----------|------------|
| TD-001 | Общий `_run_pipeline()`, тонкие `run_digest` / `run_digest_dry_run` |
| TD-002 | `PACKAGE_ROOT / "prompts"` в `run.py` |
| TD-004 | Partial digest, секция «Статус», `run_meta.partial` |
| TD-005 | `extract_actions.v1.txt` / `.en` — развёрнутый промпт (см. §9) |
| TD-007 | Мёртвые `summarize*.j2` удалены |
| TD-010 | Plain-text промпты `.txt` (ADR-009) |
| TD-011 | HTTP 429/5xx → `RetryableLLMError`, tenacity, мин. интервал вызовов |
| TD-012 | `rate_limit_rpm` в `LLMConfig` |
| TD-013 | `timeout_s` default **120** |
| TD-003 | `_merge_model()` пропускает YAML, если задан `env_field_map[key]` или `DIGEST_{prefix}_{key}`; каждая секция имеет `env_prefix`, поэтому каждое nested-поле имеет валидный env-override путь (см. §5.2) |
| Stage 8 | `deliver/mattermost.py`, webhook, best-effort (ADR-011) |
| Offline | `--dump-ingest`, `--replay-ingest`, `export-diagnostics` |
| QA | `tests/test_e2e_pipeline.py`, `--force` для идемпотентности |

### 13.2 Открытый долг

| ID | Component | Issue | Severity | Phase |
|----|-----------|-------|----------|-------|
| TD-006 | `llm.cost_limit_per_run` | Нет enforcement | Low | Phase 1 |
| TD-008 | `run.py` | Нет `if __name__ == "__main__"` (вход через `cli`) | Low | Phase 1 |
| TD-009 | `ingest/ews.py` | `NormalizedMessage` на выходе Stage 1 — вводящее имя | Low | Phase 1 |
| P5 gap | ingest | Падение EWS до LLM без partial report | Medium | По приоритету |

---

## 14. Roadmap

> ⚠ **HISTORICAL.** The phases below describe the email-only MVP horizon and are largely
> delivered (MM ingest, api delivery, the message store, the terminal/UX program all
> shipped since). The live forward roadmap is [`docs/planning/ROADMAP.md`](../../docs/planning/ROADMAP.md).

### Phase 0 — MVP Hardening + MM Delivery

**Статус:** основная часть работ **выполнена в `main`** (см. §13.1, `PHASE0_PROMPT.md` — только исторический чеклист).

**Цель (как было):** daily cron → полезный дайджест → доставка в Mattermost.

Ниже — **исходный план-оценка** (архив); не трактовать как список незакрытых задач.

| Task (архив) | Hours | Priority | Description |
|------|-------|----------|-------------|
| TD-005 fix | 4h | P0 | Промпт: taxonomy, few-shot, RU/EN |
| TD-002 fix | 1h | P0 | Путь к `prompts` от корня пакета |
| TD-004 fix | 2h | P0 | Partial при сбое LLM |
| TD-011 fix | 2h | P0 | 429/5xx retry + rate spacing |
| TD-013 fix | 0.5h | P0 | `timeout_s` 120 |
| MM delivery | 5h | P0 | Stage 8 webhook (ADR-010) |
| TD-001 fix | 2h | P1 | Единый `_run_pipeline` |
| TD-003 fix | 1.5h | P1 | Precedence ENV vs YAML |
| TD-010 fix | 0.5h | P2 | `.txt` + удаление мёртвых промптов |
| `--force` | 0.5h | P2 | Обход идемпотентности |
| E2E smoke | 3h | P1 | Mock LLM + MM |

**Критерии выхода (проверка на `main`):** `make test`; `run --dry-run` с корня репозитория; partial при ошибке LLM; MM delivery best-effort; replay/diagnostics CLI. Тег релиза — по отдельному решению.

---

### Phase 1 — Dog-fooding & Iteration (1-2 weeks)

**Goal:** Daily use. Iterate prompt quality. Automate deployment.

| Task | Hours | Priority |
|------|-------|----------|
| Daily prompt iteration (run → read MM DM → fix prompt → repeat) | ongoing | P0 |
| Migrate MM delivery to Bot API (prep for slash commands) | 4h | P1 |
| ~~CI pipeline: GitHub Actions (lint + test + docker build)~~ | 4h | P1 | **Done** — `.github/workflows/ci.yml` |
| ~~Cron/systemd unit for daily schedule~~ | 3h | P1 | **Done** — `deploy/` (systemd + cron) |
| Docker Compose for production deployment | 2h | P2 |
| Cost budget enforcement (fail if tokens > limit) | 2h | P2 |
| Feedback: log emoji reactions (👍/👎) via MM websocket | 4h | P2 |

**Exit criteria:**
- 5 consecutive days of useful digests **received in MM DM**
- ≥80% action items are correct (subjective self-assessment)
- CI green on every push
- Docker image runs unattended via cron

**Deliverable:** Tag `v0.2.0`

---

### Phase 2 — Mention Detection + Slash Commands (2-3 weeks)

**Goal:** Personalized "what's expected of me" section. Interactive commands.

| Task | Hours |
|------|-------|
| Alias config: email, login, display name, initials, RU declensions | 6h |
| Mention detector: regex + LLM classification (imperative/approval/deadline) | 8h |
| New section in JSON/MD: "Mentions & My Actions" with confidence | 4h |
| Prompt v2 with mention-aware instructions | 4h |
| Pipeline refactoring: composable stages (prep for Phase 3) | 8h |
| Slash commands: `/digest today`, `/digest details <item>` | 6h |
| Tests for mention detection + slash handler | 4h |

**Exit criteria:**
- "My Actions" section appears with ≥80% precision (self-assessed)
- `/digest today` triggers on-demand generation and returns result in DM
- Pipeline supports injecting new stages without modifying `run.py` core logic

**Deliverable:** Tag `v0.3.0`

---

### Phase 3 — Mattermost Ingest (3-4 weeks)

**Goal:** Unified digest from email + MM public channels.

> Note: MM *delivery* уже работает с Phase 0. Phase 3 — это MM *ingest* (чтение каналов).

| Task | Hours |
|------|-------|
| MM ingest adapter (API v4) | 10h |
| Unified `Message` protocol (email + MM share common interface) | 6h |
| Cross-source dedup (SHA1 + canonical URL) | 6h |
| Topic clustering (TF-IDF, NOT embeddings) | 8h |
| Source attribution in MD: `[email: ...]` / `[mm: #channel]` | 4h |
| Integration tests with MM mock | 6h |

**Exit criteria:**
- Digest includes items from both email and MM public channels
- Each item has correct source attribution
- No DM content leaks into digest (privacy boundary)

**Deliverable:** Tag `v0.4.0`

---

### Phase 4+ — Future (not planned in detail)

- **LVL4:** DM ingest with consent management
- **LVL5:** Full interactive MM bot (`/digest since:2025-10-10 only:actions`)
- **Web UI:** Lightweight history browser (when 30+ digests accumulated)
- **Multi-user:** Config per user, schedule per user
- **Quality metrics:** Labeled gold-set, P/R/F1 evaluation
- **Embedding-based selection:** When message volume exceeds 500/day

---

## 15. Anti-Patterns (What NOT to Do)

| Anti-Pattern | Why It's Bad | Do Instead |
|-------------|-------------|------------|
| Add embeddings/FAISS for context selection | Over-engineering for ≤100 emails. Adds GPU dependency | Rule-based scoring works fine |
| Build multi-user SaaS platform | No demand signal, massive complexity | Single-user CLI tool |
| Add database (Postgres, SQLite) | File-based state is sufficient. DB adds ops burden | JSON files + watermark |
| Add message queue (Celery, Redis) | Batch daily job, no async needed | Direct function calls |
| Create microservices | One process, one pipeline. No service boundaries needed | Monolith |
| Add real-time processing | Daily cron is the product. Real-time changes everything | Keep batch |
| Add consent management before DM support | Consent only matters for DM (LVL4). Email is employer-owned | Defer to Phase 4 |
| Build Web UI before MM delivery works | Push > Pull. Web UI = "remember to visit". MM DM = auto-delivered | MM webhook first, Web UI later for history/search (ADR-010) |
| Add multiple LLM providers/fallback | One corporate gateway. Provider switching is gateway's job | Single endpoint |
| Multi-step LLM prompting (extract → summarize → format) | 3 RPM per run at 15 RPM limit = max 5 concurrent users. Single call = 1-2 RPM/run | Keep single LLM call (ADR-002, ADR-008) |
| Use tiktoken for exact token counting | Approximate `words * 1.3` is sufficient at typical `max_total_tokens` scale (default 7000). Off by ±10% doesn't matter | Keep approximation |

---

## 16. Security Boundaries

```
┌─────────────────────────────────────────────────────┐
│                LOCAL TRUST ZONE                      │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ EWS Data │  │ Artifacts│  │   Logs   │          │
│  │ (raw)    │  │ (JSON/MD)│  │(redacted)│          │
│  └──────────┘  └──────────┘  └──────────┘          │
│                                                      │
│  Email addresses: VISIBLE (policy decision)          │
│  Phones/SSN/CC: REDACTED in logs                     │
│  Passwords/Tokens: REDACTED in logs                  │
│                                                      │
│  Retention: 7 days default, auto-pruned at run end   │
│  Access: local filesystem permissions                │
│                                                      │
└──────────────────────────┬──────────────────────────┘
                           │
               NETWORK BOUNDARY (no local redaction)
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│              LLM GATEWAY (EXTERNAL, corp)            │
│                                                      │
│  x-trace-id: {trace_id}                              │
│                                                      │
│  ⚠ CORRECTED 2026-06: evidence text is sent AS-IS    │
│  (incl. emails/names). There is NO x-redaction-policy│
│  header and NO local pre-inference masking — the     │
│  [[REDACT:..]] scheme was never wired. The corp      │
│  gateway's own non-logging policy is the only        │
│  inference-time control — formalized per endpoint    │
│  in PC2_DATA_HANDLING.md (PC-2 ADR, DRAFT).          │
└─────────────────────────────────────────────────────┘
```

---

## 17. Network Topology & Development Workflow

### 17.1 Network zones

```
┌──────────────────────────────────────────────────────────┐
│                  CORP NETWORK (закрытая)                   │
│                                                            │
│  ┌───────────┐   ┌───────────┐   ┌─────────────────────┐ │
│  │ Exchange   │   │ Corp LLM  │   │ digest-core (prod)  │ │
│  │ EWS/NTLM  │   │ Gateway   │   │ cron + Docker       │ │
│  └───────────┘   └───────────┘   └──────────┬──────────┘ │
│        ▲               ▲                     │            │
│        │               │              diagnostic export   │
│        │               │              (logs, traces,      │
│   ONLY from corp  ONLY from corp      artifacts)          │
│                                              │            │
└──────────────────────────────────────────────┼────────────┘
                                               │
                    ════════════════════════════╪═══════════
                         NETWORK BOUNDARY       │
                    ════════════════════════════╪═══════════
                                               │
┌──────────────────────────────────────────────┼────────────┐
│                  GENERAL NETWORK                          │
│                                              ▼            │
│  ┌───────────┐   ┌───────────┐   ┌─────────────────────┐ │
│  │Mattermost │   │  GitHub   │   │ Dev workstation     │ │
│  │ (delivery │   │  (repo,   │   │ (code, debug,       │ │
│  │  + bot)   │   │   CI)     │   │  analyze traces)    │ │
│  └───────────┘   └───────────┘   └─────────────────────┘ │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 17.2 Что это значит для разработки

| Сервис | Где доступен | Следствие |
|--------|-------------|-----------|
| **Exchange (EWS)** | Только corp network | Реальный ingest тестируется только изнутри. CI = только mock |
| **LLM Gateway** | Только corp network | LLM extraction тестируется только изнутри. CI = mock |
| **Mattermost** | General + corp | Delivery можно тестировать откуда угодно |
| **GitHub** | General + corp | CI/CD, code review — без ограничений |
| **Dev workstation** | General network | Код пишем и дебажим снаружи, запускаем реально — изнутри |

### 17.3 Diagnostic Export (corp → dev workstation)

**Проблема:** Pipeline работает в corp сети. Дебаг и анализ — снаружи.
Нужен механизм передачи диагностики из corp наружу.

**Diagnostic bundle** — единый архив для передачи из corp сети:

```
diagnostic-{trace_id}-{date}.tar.gz
├── run.log                    # Structured JSON log (PII-redacted)
├── digest-{date}.json         # Output artifact (machine-readable)
├── digest-{date}.md           # Output artifact (human-readable)
├── pipeline-metrics.json      # Per-stage timing, token counts, error counts
├── evidence-summary.json      # Evidence chunk stats (NO content — privacy)
│   ├── chunk_count, total_tokens, top_scores
│   ├── filtered_service_count, selected_count
│   └── per_thread: {conversation_id, message_count, chunk_count}
├── config-sanitized.yaml      # Config with secrets stripped
├── ews-fetch-stats.json       # Fetch timing, message count, errors (NO content)
├── llm-request-trace.json     # Request metadata (NO prompt/response body)
│   ├── model, tokens_in, tokens_out, latency_ms
│   ├── http_status, retry_count
│   └── validation_errors (dropped items count)
└── env-info.txt               # Python version, package versions, OS
```

**Что НЕ включается (privacy):**
- Тела писем (raw или нормализованные)
- Evidence chunk content
- LLM prompt или response body
- Email addresses из логов (если не redacted)
- EWS password, LLM token

**CLI команда:**
```bash
# Собрать diagnostic bundle для последнего run
python -m digest_core.cli export-diagnostics --trace-id <id> --out /tmp/

# Собрать для конкретной даты
python -m digest_core.cli export-diagnostics --date 2026-03-29
```

**Каналы передачи (от простого к удобному):**
1. **Ручной:** scp/sftp bundle на dev workstation
2. **MM upload:** бот отправляет bundle файлом в DM (MM доступен из обеих сетей)
3. **Автоматический:** при `--collect-logs` flag, пайплайн сам шлёт bundle в MM DM

Рекомендация: **вариант 2 (MM upload)** — MM доступен отовсюду, bundle содержит
только redacted данные, file upload через Bot API тривиален.

### 17.4 Feature Development Workflow

```
Dev workstation (general network)     Corp network
─────────────────────────────────     ────────────────────────
1. Write code + unit tests
2. make lint && make test (mocks)
3. git push → GitHub CI (mocks)
                                      4. git pull on corp machine
                                      5. Real EWS + LLM integration test
                                      6. Review digest quality in MM DM
                                      7. export-diagnostics → MM DM
8. Analyze diagnostic bundle
9. Fix prompt / code
10. goto 2
```

**Принцип: "Code outside, run inside, debug outside"**

- Весь код пишется и тестируется (mock) на dev workstation
- Реальные прогоны (EWS + LLM) только из corp сети
- Diagnostic bundle передаётся через MM для анализа снаружи
- MM delivery тестируется из любой сети

### 17.5 Replay Mode (offline development)

Для комфортной разработки без доступа к corp сети:

**EWS Replay:** Сохранить результат реального EWS fetch как fixture, использовать
для повторных прогонов pipeline без EWS-соединения.

```bash
# Изнутри corp сети: сохранить snapshot
python -m digest_core.cli run --from-date 2026-03-29 --dump-ingest /tmp/ews-snapshot.json

# Снаружи: replay без EWS
python -m digest_core.cli run --replay-ingest /tmp/ews-snapshot.json
```

**LLM Replay:** Аналогично — сохранить LLM request/response для offline replay.

```bash
# Изнутри corp сети: запуск с записью
python -m digest_core.cli run --record-llm /tmp/llm-recording.json

# Снаружи: replay без LLM
python -m digest_core.cli run --replay-llm /tmp/llm-recording.json
```

**Приоритет реализации:**
- Phase 0: `export-diagnostics` CLI command + MM upload
- Phase 0: `--dump-ingest` / `--replay-ingest` (EWS snapshot)
- Phase 1: `--record-llm` / `--replay-llm` (LLM recording)

---

## 18. Testing Strategy

### Unit Tests (anywhere — no network needed)
- Each stage has dedicated test file
- Mock external dependencies (EWS, LLM Gateway)
- Fixture-based: `tests/fixtures/emails/` (10 email samples)
- Schema validation: Pydantic models enforce contracts
- **Run:** `make test` on dev workstation or CI

### Integration Tests — Mock (anywhere)
- End-to-end with mock LLM (`tests/mock_llm_gateway.py`)
- EWS replay fixtures (saved from corp network runs)
- Config loading from fixtures
- Idempotency tests (T-48h window)
- Empty day handling
- **Run:** `make test` — no network dependencies

### Integration Tests — Real (corp network ONLY)
- Real EWS fetch against Exchange server
- Real LLM extraction against qwen35-397b-a17b
- Real MM delivery to test channel
- **Run:** manual from corp workstation
- **Output:** diagnostic bundle → MM DM for analysis

### Smoke Tests
- `make smoke` — dry-run with example config (anywhere)
- Docker build + run validation (anywhere)

### Replay Tests (anywhere, requires prior corp run)
- `--replay-ingest` from saved EWS snapshot
- `--replay-llm` from saved LLM recording
- Full pipeline without any network dependencies
- **Key for prompt iteration:** change prompt → replay → compare output

### Manual Testing
- Checklist in `docs/testing/MANUAL_TESTING_CHECKLIST.md`
- 7 stages: env setup, smoke, integration, edge cases, quality, diagnostics, results
- **Stages 1-4:** anywhere (mocks). **Stages 5-7:** corp network only

### NOT doing (and why)
- Load testing — single user, ≤100 emails, latency is LLM-bound
- UI testing — no UI
- A/B testing — no traffic to split
- Gold-set evaluation — no labeled data yet (build during Phase 1 dog-fooding)

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| **NormalizedMessage** | NamedTuple для email-сообщения. _Naming debt:_ на выходе Stage 1 тело ещё raw (HTML), нормализация — Stage 2 |
| **ConversationThread** | Группа сообщений с общим `conversation_id`, отсортированных по времени |
| **Evidence Chunk** | Фрагмент текста email (64-512 tokens) с priority score и source reference |
| **Watermark** | ISO timestamp последнего обработанного batch, хранится в `.state/ews.syncstate` |
| **T-48h Window** | Idempotency window: артефакты <48h → skip rebuild |
| **Context Diet** | Процесс отбора наиболее релевантных evidence chunks в рамках token budget |
| **Trace ID** | UUID4 на pipeline run, проносится через все логи и артефакты |
| **source_ref** | JSON-объект, связывающий пункт дайджеста с оригинальным письмом |
| **evidence_id** | UUID4 конкретного evidence chunk (уникален в пределах run) |
| **RPM** | Requests Per Minute — rate limit LLM Gateway (15 RPM для qwen35-397b-a17b) |
| **Budget Owner** | Coarse cap: Stage 4 (`_limit_total_tokens`); selection under same `max_total_tokens`: Stage 5 (`ContextSelector`) — см. §4.2 Stage 5 |
| **Diagnostic Bundle** | tar.gz архив с redacted логами, метриками и артефактами для дебага вне corp сети |
| **Replay Mode** | Прогон pipeline из сохранённых EWS/LLM snapshot-ов без реального сетевого доступа |
| **Corp Network** | Закрытая корпоративная сеть с доступом к Exchange и LLM Gateway |

## Appendix B: Quick Reference — CLI

```bash
# Full run (today, default model qwen35-397b-a17b)
python -m digest_core.cli run

# Specific date
python -m digest_core.cli run --from-date 2026-03-28

# Dry run (no LLM, stops after context selection)
python -m digest_core.cli run --dry-run

# Rolling 24h window instead of calendar day
python -m digest_core.cli run --window rolling_24h

# Custom output and state directories
python -m digest_core.cli run --out /tmp/digest --state /tmp/state

# Force rebuild (bypass T-48h idempotency)
python -m digest_core.cli run --force

# Run diagnostics
python -m digest_core.cli diagnose
```
