# Hierarchical Orchestration: Автоматическое масштабирование

> ## ⚠️ STALE — describes REMOVED code (do not rely on this)
> The `hierarchical/` module was **deleted** during the multi-agent redesign (REDESIGN_PLAN
> "hierarchical cleanup"). Only a dead no-op `HierarchicalConfig` remains in `config.py`;
> none of the auto-activation, must-include, or merge behavior described below exists in the
> codebase. This file is kept only as a historical design record and is a candidate for
> `docs/legacy/`. For the current architecture see
> [`digest-core/docs/ARCHITECTURE.md`](../../digest-core/docs/ARCHITECTURE.md) and
> [`docs/planning/ROADMAP.md`](../planning/ROADMAP.md).

## Обзор

Модуль **Hierarchical Orchestration** автоматически переключается в иерархический режим при больших объёмах писем, гарантируя включение обязательных чанков (mentions + last_update) и применяя merge-политику с citations.

**Ключевые возможности:**
- ✅ Авто-активация: `threads>=60` OR `emails>=300`
- ✅ Must-include chunks: упоминания пользователя + последний апдейт
- ✅ Merge-политика: заголовок + 3-5 ключевых цитат
- ✅ Пропуск LLM если нет evidence (экономия токенов)
- ✅ Metrics: hierarchical_runs_total, avg_subsummary_chunks, saved_tokens

---

## Архитектура

### Auto-Enable Logic

```python
def should_use_hierarchical(threads, emails):
    if not config.enable or not config.auto_enable:
        return False
    
    return (len(threads) >= config.min_threads or 
            len(emails) >= config.min_emails)
```

**Thresholds (новые):**
- `min_threads: 60` (было 30)
- `min_emails: 300` (было 150)

**Trigger Reasons (metrics):**
- `auto_threads`: Активировано по количеству тредов
- `auto_emails`: Активировано по количеству писем
- `manual`: Вручную включено в конфиге

---

## Must-Include Chunks

### Гарантия критических чанков

**1. Chunks с user mentions:**
```python
if any(alias.lower() in chunk.text.lower() for alias in user_aliases):
    must_include_chunks.append(chunk)
```

**2. Last update chunk (самый свежий):**
```python
last_update_chunk = max(chunks, key=lambda c: c.timestamp)
must_include_chunks.append(last_update_chunk)
```

### Логика отбора

```
Per-Thread Chunk Selection:
1. Identify must-include chunks (mentions + last_update)
2. Calculate regular_slots = max_chunks - must_include_count
3. If must_include_count > max_chunks:
   - Extend to exception limit (12 instead of 8)
   - Log warning
4. Select top regular chunks by priority
5. Combine: must_include + regular
```

**Пример:**
```
Thread has 15 chunks:
- 3 chunks with user mentions
- 1 last update chunk
- 11 regular chunks

Selection (max_chunks=8):
→ Must-include: 4 (3 mentions + 1 last_update)
→ Regular slots: 8 - 4 = 4
→ Selected: 4 must-include + 4 top regular = 8 total

Exception case (10 mentions):
→ Must-include: 10
→ Extends to exception_limit=12
→ Regular slots: 12 - 10 = 2
→ Selected: 10 must-include + 2 regular = 12 total
```

---

## Merge Policy

### Заголовок + 3-5 цитат

При формировании aggregator input для final LLM:

```
=== Thread: thread123 ===
Summary: Brief thread summary title

Key Citations (5):
  [ev1] Please review the updated proposal by Friday. We need your approval...
  [ev3] User john@example.com mentioned that the deadline is critical...
  [ev5] The decision was made to proceed with option B...
  [ev7] Attachments include the final report and budget spreadsheet...
  [ev9] Last update: All stakeholders have agreed on the timeline...

Summary (full): Detailed thread summary with all key points...
```

**Параметры:**
- `merge_max_citations: 5` (3-5 recommended)
- `merge_include_title: true`

**Преимущества:**
- Сохраняет extractive traceability
- Улучшает качество final aggregation
- Помогает LLM связать thread summary с исходными сообщениями

---

## Skip LLM Optimization

### Экономия токенов

Если после отбора чанков не осталось evidence → пропускаем LLM call.

```python
if not selected_chunks and config.skip_llm_if_no_evidence:
    logger.info("Skipping LLM for thread (no evidence)")
    # Return empty ThreadSummary
    return ThreadSummary(
        thread_id=thread_id,
        key_points=[],
        actions=[],
        deadlines=[]
    )
```

**Сценарии:**
1. Тред содержал только спам/подписи → после очистки пусто
2. Все чанки были отфильтрованы selection политикой
3. Тред слишком старый и все чанки вне time window

**Metrics:**
```promql
saved_tokens_total{skip_reason="no_evidence"}
saved_tokens_total{skip_reason="empty_selection"}
```

---

## Конфигурация

### `config.yaml`

```yaml
hierarchical:
  enable: true
  auto_enable: true       # NEW: Auto-enable based on thresholds
  min_threads: 60         # NEW: Increased from 30
  min_emails: 300         # NEW: Increased from 150
  
  per_thread_max_chunks_in: 8
  per_thread_max_chunks_exception: 12  # NEW: Exception limit
  
  # Must-include chunks (NEW)
  must_include_mentions: true
  must_include_last_update: true
  
  # Merge policy (NEW)
  merge_max_citations: 5
  merge_include_title: true
  
  # Optimization (NEW)
  skip_llm_if_no_evidence: true
  
  # Existing params
  summary_max_tokens: 90
  parallel_pool: 8
  timeout_sec: 20
  degrade_on_timeout: "best_2_chunks"
  final_input_token_cap: 4000
```

### Примеры конфигураций

**1. Агрессивная экономия (low traffic):**
```yaml
hierarchical:
  auto_enable: true
  min_threads: 100        # ↑ Higher threshold
  min_emails: 500         # ↑ Higher threshold
  skip_llm_if_no_evidence: true
  merge_max_citations: 3  # ↓ Fewer citations
```

**2. Высокое качество (critical emails):**
```yaml
hierarchical:
  auto_enable: true
  min_threads: 40         # ↓ Lower threshold (earlier activation)
  min_emails: 200         # ↓ Lower threshold
  per_thread_max_chunks_exception: 15  # ↑ More chunks for exceptions
  merge_max_citations: 5  # Max citations
  must_include_mentions: true
  must_include_last_update: true
```

**3. Отключить auto-enable (manual control):**
```yaml
hierarchical:
  enable: true            # Still allow hierarchical
  auto_enable: false      # But don't auto-enable
  # Will only use hierarchical if explicitly set
```

---

## Prometheus Metrics

### Новые метрики

```python
# Counter: hierarchical runs by trigger reason
hierarchical_runs_total{trigger_reason="auto_threads"} = 15
hierarchical_runs_total{trigger_reason="auto_emails"} = 8
hierarchical_runs_total{trigger_reason="manual"} = 2

# Gauge: average chunks per subsummary
avg_subsummary_chunks = 6.5

# Counter: saved tokens by skip reason
saved_tokens_total{skip_reason="no_evidence"} = 12000
saved_tokens_total{skip_reason="empty_selection"} = 3000

# Counter: must-include chunks added
must_include_chunks_total{chunk_type="mentions"} = 45
must_include_chunks_total{chunk_type="last_update"} = 60
```

### Prometheus Queries

**1. Hierarchical activation rate:**
```promql
rate(hierarchical_runs_total[1h])
```

**2. Trigger reason distribution:**
```promql
sum(hierarchical_runs_total) by (trigger_reason)
```

**3. Token savings:**
```promql
sum(rate(saved_tokens_total[5m])) by (skip_reason)
```

**4. Must-include usage:**
```promql
sum(rate(must_include_chunks_total[5m])) by (chunk_type)
```

**5. Avg subsummary chunks trend:**
```promql
avg_subsummary_chunks
```

### Grafana Dashboard

**Panel 1: Hierarchical Activation**
```promql
sum(increase(hierarchical_runs_total[1d])) by (trigger_reason)
```

**Panel 2: Token Savings**
```promql
sum(increase(saved_tokens_total[1d])) / 1000  # In thousands
```

**Panel 3: Must-Include Distribution**
```promql
sum(increase(must_include_chunks_total[1d])) by (chunk_type)
```

**Panel 4: Avg Subsummary Chunks**
```promql
avg_subsummary_chunks
```

---

## Тестирование

### Unit Tests

```bash
pytest digest-core/tests/test_hierarchical_orchestration.py -v
```

**Test Classes:**
1. `TestAutoEnableThresholds` - Проверка auto-enable логики
2. `TestMustIncludeChunks` - Mentions + last_update гарантия
3. `TestSkipLLM` - Пропуск LLM при отсутствии evidence
4. `TestMergePolicy` - Citations в merge
5. `TestMailExplosion` - Синтетический mail explosion (latency/cost)
6. `TestF1Preservation` - F1 для actions/mentions не деградирует

### Acceptance Criteria

**Auto-Enable:**
- ✅ Активируется при threads >= 60
- ✅ Активируется при emails >= 300
- ✅ Не активируется при обоих порогах ниже
- ✅ Можно отключить через `auto_enable=false`

**Must-Include:**
- ✅ Chunks с user mentions всегда включены
- ✅ Last update chunk всегда включён
- ✅ Exception limit (12) применяется при >8 must-include
- ✅ Metrics: must_include_chunks_total

**Skip LLM:**
- ✅ LLM пропускается если нет evidence
- ✅ Возвращается пустой ThreadSummary
- ✅ Metrics: saved_tokens_total

**Merge Policy:**
- ✅ 3-5 key citations добавляются в aggregator input
- ✅ Citations содержат evidence_id + snippet
- ✅ Title включён если merge_include_title=true

**Performance (Mail Explosion):**
- ✅ Latency: Grouping 100 threads < 1s
- ✅ F1 для actions/mentions: не теряются must-include chunks

---

## Pipeline Flow

```
1. Ingest (EWS) → Messages
2. Normalize (HTML→text, cleaning)
3. Thread Building → Threads
4. Evidence Chunking → Chunks

5. 🔥 AUTO-ENABLE CHECK (NEW)
   ├─ threads >= 60? → trigger_reason="auto_threads"
   ├─ emails >= 300? → trigger_reason="auto_emails"
   └─ Record: hierarchical_runs_total

6. IF hierarchical_mode:
   ├─ Group chunks by thread
   ├─ For each thread:
   │  ├─ 🔥 SELECT WITH MUST-INCLUDE (NEW)
   │  │  ├─ Identify mentions chunks
   │  │  ├─ Identify last_update chunk
   │  │  ├─ Select top regular chunks (up to 8 or 12)
   │  │  └─ Record: must_include_chunks_total
   │  │
   │  ├─ 🔥 SKIP LLM IF NO EVIDENCE (NEW)
   │  │  └─ Record: saved_tokens_total
   │  │
   │  └─ LLM per-thread summarization
   │
   ├─ 🔥 MERGE POLICY (NEW)
   │  ├─ Extract 3-5 key citations per thread
   │  ├─ Add to aggregator input
   │  └─ Include thread summary title
   │
   └─ Final aggregation → EnhancedDigest v2

7. Context Selection
8. Ranking
9. JSON/Markdown Assembly
```

---

## Примеры использования

### CLI

```bash
# Auto-enable hierarchical (default)
digest-cli run --date 2024-12-15

# Disable auto-enable (manual control)
# Set hierarchical.auto_enable=false in config.yaml

# Check logs for trigger reason
grep "trigger_reason" digest-test.log
```

### Logs

```log
# Auto-enabled by threads
INFO Using hierarchical mode threads=75 emails=200 trigger_reason=auto_threads

# Auto-enabled by emails
INFO Using hierarchical mode threads=45 emails=350 trigger_reason=auto_emails

# Must-include detected
INFO Selected chunks with must-include total_selected=10 must_include=3 regular=7 max_chunks=8

# LLM skipped
INFO Skipping LLM for thread (no evidence after selection) thread_id=thread_empty

# Merge policy applied
INFO Preparing aggregator input with merge policy key_citations=5
```

---

## Troubleshooting

### Проблема 1: Hierarchical не активируется

**Симптомы:** Всегда non-hierarchical mode

**Причины:**
1. `enable=false` в конфиге
2. `auto_enable=false`
3. Ниже обоих порогов (threads < 60 AND emails < 300)

**Решение:**
```yaml
hierarchical:
  enable: true
  auto_enable: true
  min_threads: 60  # Check your actual volume
  min_emails: 300
```

### Проблема 2: Must-include chunks теряются

**Симптомы:** Mentions/last_update отсутствуют в digest

**Причины:**
1. `must_include_mentions=false` или `must_include_last_update=false`
2. `user_aliases` не настроены
3. Exception limit не применяется

**Решение:**
```yaml
hierarchical:
  must_include_mentions: true
  must_include_last_update: true
  per_thread_max_chunks_exception: 12  # Allow more chunks

ews:
  user_aliases:
    - "user@example.com"
    - "user.name@example.com"
```

### Проблема 3: Слишком много токенов экономится (качество падает)

**Симптомы:** `saved_tokens_total` высокий, но digest пустой

**Причины:**
1. `skip_llm_if_no_evidence=true` слишком агрессивно
2. Selection политика слишком строгая

**Решение:**
```yaml
hierarchical:
  skip_llm_if_no_evidence: false  # Disable optimization
```

### Проблема 4: Merge policy не добавляет citations

**Симптомы:** Aggregator input без "Key Citations"

**Причины:**
1. `merge_max_citations=0`
2. `merge_include_title=false`

**Решение:**
```yaml
hierarchical:
  merge_max_citations: 5    # 3-5 recommended
  merge_include_title: true
```

---

## Roadmap

### v1.0 (Current) ✅
- ✅ Auto-enable: threads>=60 OR emails>=300
- ✅ Must-include: mentions + last_update
- ✅ Merge policy: title + 3-5 citations
- ✅ Skip LLM optimization
- ✅ Metrics: hierarchical_runs_total, avg_subsummary_chunks, saved_tokens, must_include_chunks
- ✅ Tests: unit + mail explosion

### v1.1 (Planned) 🚧
- 🔄 Adaptive thresholds: auto-tune min_threads/min_emails based on load
- 🔄 Must-include: add "high_priority_senders" chunks
- 🔄 Merge policy: semantic clustering for better citations
- 🔄 Cost tracking: detailed cost_saved_total metric

### v2.0 (Future) 💡
- 💡 ML-based must-include: predict critical chunks
- 💡 Dynamic exception limit: auto-adjust based on thread complexity
- 💡 Parallel final aggregation: split EnhancedDigest sections

---

## DoD (Definition of Done)

### Code ✅
- ✅ HierarchicalConfig: auto_enable, must_include_*, merge_*, skip_llm_*
- ✅ HierarchicalProcessor: _select_chunks_with_must_include, _extract_key_citations
- ✅ run.py: trigger_reason detection, user_aliases passing
- ✅ Metrics: 4 new metrics

### Tests ✅
- ✅ TestAutoEnableThresholds: 60/300 thresholds
- ✅ TestMustIncludeChunks: mentions + last_update + exception
- ✅ TestSkipLLM: no evidence optimization
- ✅ TestMergePolicy: 3-5 citations
- ✅ TestMailExplosion: 100 threads, 500 emails
- ✅ TestF1Preservation: actions/mentions not lost

### Metrics ✅
- ✅ hierarchical_runs_total{trigger_reason}
- ✅ avg_subsummary_chunks
- ✅ saved_tokens_total{skip_reason}
- ✅ must_include_chunks_total{chunk_type}

### Documentation ✅
- ✅ HIERARCHICAL_ORCHESTRATION.md (this file)
- ✅ Config examples: standard, aggressive, high-quality
- ✅ Prometheus queries + Grafana dashboard
- ✅ Troubleshooting guide

### Deployment ✅
- ✅ config.example.yaml updated
- ✅ Backward compatible (existing deployments work)
- ✅ No breaking changes

---

## Commit Message

```
feat(hier): auto hierarchical mode with sub-summaries + must-include chunks + metrics + tests

Implementation:
- Auto-enable: threads>=60 OR emails>=300 (configurable)
- Trigger reasons: auto_threads, auto_emails, manual (for metrics)
- Must-include chunks:
  * User mentions (by user_aliases)
  * Last update chunk (most recent by timestamp)
  * Exception limit: 12 chunks (vs normal 8) for must-include overflow
- Merge policy: thread summary title + 3-5 key citations (extractive)
- Skip LLM optimization: if no evidence after selection → saved_tokens
- user_aliases passed through pipeline to hierarchical processor

Metrics:
- hierarchical_runs_total{trigger_reason}: auto_threads, auto_emails, manual
- avg_subsummary_chunks: average chunks per thread subsummary
- saved_tokens_total{skip_reason}: no_evidence, empty_selection
- must_include_chunks_total{chunk_type}: mentions, last_update

Tests:
- TestAutoEnableThresholds: 60/300 thresholds, auto/manual
- TestMustIncludeChunks: mentions detection, last_update, exception limit
- TestSkipLLM: empty evidence optimization
- TestMergePolicy: 3-5 citations extraction
- TestMailExplosion: 100 threads, 500 emails, latency check
- TestF1Preservation: actions/mentions not lost with must-include

Configuration:
- hierarchical.auto_enable: true (NEW)
- hierarchical.min_threads: 60 (was 30)
- hierarchical.min_emails: 300 (was 150)
- hierarchical.per_thread_max_chunks_exception: 12 (NEW)
- hierarchical.must_include_mentions: true (NEW)
- hierarchical.must_include_last_update: true (NEW)
- hierarchical.merge_max_citations: 5 (NEW)
- hierarchical.merge_include_title: true (NEW)
- hierarchical.skip_llm_if_no_evidence: true (NEW)

Documentation:
- docs/HIERARCHICAL_ORCHESTRATION.md: architecture, config, metrics, troubleshooting

Acceptance:
✅ Auto-enable at 60 threads OR 300 emails
✅ Must-include chunks guaranteed (mentions + last_update)
✅ Exception limit (12) applied when needed
✅ Merge policy adds 3-5 citations
✅ Skip LLM saves tokens
✅ F1 for actions/mentions preserved
✅ Mail explosion test passes (100 threads, 500 emails)
```

---

## Контакты

- **Вопросы:** Создайте issue в GitHub
- **Metrics:** Проверьте Grafana dashboard "Hierarchical Orchestration"

