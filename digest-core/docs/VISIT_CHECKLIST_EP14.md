# EP-14 — чеклист корп-визита (W3 validation pack)

> **Target env:** корп-сеть Райффайзенбанка — EWS + LLM gateway доступны только изнутри; Mattermost доступен отовсюду.
> **Сгенерировано дома:** 2026-06-11 · **База:** origin/main на день визита (см. §a — свежий main обязателен, урок 2026-06-10).
> **Last known-good:** сессия 2026-06-10 (`CORP_VALIDATION_FINDINGS_2026-06.md`) — ВНИМАНИЕ: она валидировала устаревший код; числовые baseline'ы оттуда брать с оговоркой, большинство read-out'ов ниже устанавливают baseline впервые.
> **Бандл:** `make bundle` в день сборки · sha256: `TBD — записать при запечатывании` (§b P4).
>
> **Правило offline-честности:** каждый пункт помечен `offline ✓` (доказано дома) или `corp` (доказуемо только внутри). Внутри нет агента — каждый шаг копируемый, с ожидаемым результатом. Секреты — только по именам ENV-переменных (`EWS_PASSWORD`, `LLM_TOKEN`, `MM_WEBHOOK_URL`), значения в этот файл не попадают никогда.
>
> Базовая процедура сессии (setup → dry-run → full run → артефакты) — `CORP_SESSION_RUNBOOK.md` §0–§5. Этот чеклист добавляет к ней пробы EP-14 (①–⑧ из `audits/BACKLOG.md` + исходные пункты пакета).

---

## (a) Carry-in — что проносим

| Что | Деталь | Проверка на месте |
|---|---|---|
| Свежий `main` | runbook §0.0: корп-клон отстаёт; сверить `git log -1` с `origin/main`, при недоступности GitHub — принести `git bundle` | `git log -1` == SHA из заголовка |
| Air-gap бандл | `make bundle` в день визита (см. §b P4) | `shasum -a 256` == заголовок |
| Секреты | `EWS_PASSWORD`, `LLM_TOKEN`, `MM_WEBHOOK_URL` — задаются в корп-shell / `~/.config/actionpulse/env` | `printenv LLM_TOKEN | wc -c` > 1 (значение не печатать) |
| Этот чеклист + runbook | распечатка или файл на устройстве | — |

Конфиг для проб применяется **правками `configs/config.yaml`** (env-переключатели работают только при наличии YAML-секции — особенность `_merge_model`); фрагменты для вставки — в пробах ниже.

## (b) Offline preflight — дома, ДО запечатывания бандла

Провал любого пункта **блокирует визит**.

| # | Проверка | Команда | Статус 2026-06-11 |
|---|---|---|---|
| P1 | Тесты ×2 подряд | `cd digest-core && make test && make test` | ✓ 769 passed / 3 skipped ×2 (повторить на main дня сборки) |
| P2 | Replay-базлайн | `make eval-replay` | ✓ OK |
| P3 | Селектор best-of-N | `PYTHONPATH=src uv run python -m digest_core.cli eval-best-of-n` | ✓ PROOF OK (архив: `audits/baselines/2026-06-11-best-of-n-offline.json`) |
| P4 | Бандл + network-off smoke | `make bundle`; установить в чистый venv **без сети**; import-smoke | лок не менялся с доказательства #80 (`uv.lock` @ 2a9cf98) → в день сборки: пересобрать, записать sha256 в заголовок; полный smoke повторить только если `uv.lock` изменился |
| P5 | MM-вебхук | `python -m digest_core.cli mm-ping` | сделать перед выездом (MM доступен отовсюду) |

## (c) Флаги: состояние внутри

D4/PC-2 **подписан** (все три fleet-эндпоинта одобрены — тот же gateway/ключ/контур, что extractor), поэтому **пробные** включения ниже разрешены. **Постоянное** включение в ежедневном прогоне/systemd — только после домашнего разбора read-out'ов; гейты качества — только после EP-15 (κ ≥ 0.41 + CI-floor, правило жёсткое).

| Флаг | Ежедневный прогон | Пробы этого визита | Постоянный флип разблокирует |
|---|---|---|---|
| `reranker.enabled` | **OFF** | ON в пробах ②④ | разбор ② дома (первый `tau`) |
| `judge.enabled` | **OFF** | ON в пробе ⑤ | разбор ⑤ дома (`items_repaired`, качество вердиктов) |
| `extract.best_of_n` | **1** | 3 в пробе ⑧ (+ extractor-бюджет 4) | разбор ⑧ дома (качество сэмплов vs детерминированный) |
| `llm.spotlight_evidence` | **OFF** | ON в пробе I-1 | diff базлайна после пробы |
| `observability.otel_enabled` | **OFF** | по решению пробы O-1 | наличие in-net коллектора |
| `eval.judge_mode` | **pointwise** | не трогать (проба ⑦ передаёт `--mode reference` явно) | только EP-15 |

## (d) Пробы — по порядку, после базовой сессии runbook §1–§3

Подготовка: базовая сессия даёт `SNAP=/tmp/actionpulse/ews-snapshot-<дата>.json`, `REC=/tmp/actionpulse/llm-recording-<дата>.json`, дайджест в MM. Всё ниже использует `--replay-ingest "$SNAP"` (EWS повторно не дёргаем). `LLM_BASE` = базовый URL гейтвея без `/v1/...` (как в `configs/config.yaml llm.endpoint` до `/v1/`).

**Проба 0 — первый read-out гейта (исходный пункт пакета).** Из `trace-*.meta.json` базового прогона выписать: `support_recall`, `items_weak`, `items_quarantined`, `llm_budget`. Прочитать секцию «Не подтверждено» в MM глазами: пункты там — реально слабые? _(corp; baseline устанавливается впервые)_

**Проба ① — путь и форма `/rerank`.**
```bash
curl -sS -X POST "$LLM_BASE/rerank" \
  -H "Authorization: Bearer $LLM_TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"bge-reranker-v2-m3","query":"подготовить отчёт","documents":["прошу подготовить отчёт к пятнице","обед в 13:00"]}'
```
Ожидаемо: JSON с `results[].relevance_score` (или `scores[]`); релевантный документ выше. Если 404 — повторить с `$LLM_BASE/v1/rerank`, затем `$LLM_BASE/v1/score`. **Записать рабочий путь + сырую форму ответа**; если не `/rerank` — вписать в `configs/config.yaml`: `reranker:` → `endpoint_path: "/v1/rerank"` (или что сработало).

**Проба ② — реранкер в гейте, первый `tau` read-out.** В `configs/config.yaml` добавить/включить:
```yaml
reranker:
  enabled: true
```
Прогон: `python -m digest_core.cli run --force --replay-ingest "$SNAP" --out /tmp/actionpulse/out-rr --state /tmp/actionpulse/state-rr`.
Записать: `fleet_reranker_calls` из `trace-*.meta.json`; все `support_score` из `digest-*.json` (распределение → выбор `tau` дома). Ожидаемо: прогон зелёный; при 429/таймауте — деградация в fidelity-only БЕЗ падения (так и задумано, зафиксировать факт).

**Проба ③ — RPM/латентность флота.** Во время ②/⑤/⑧ собрать из structured-логов: ожидания брокера, события `penalize`/429, латентности вызовов. Ничего не настраивать — только записать. _(corp)_

**Проба ④ — запись fleet-sidecar.** Прогон как ②, плюс `--record-llm /tmp/actionpulse/llm-rr-rec.json`. Ожидаемо: рядом появился `llm-rr-rec.json.fleet.json`. Забрать оба; replay-проверка — дома.

**Проба ⑤ — judge → repair, первый `items_repaired`.** В `configs/config.yaml`:
```yaml
judge:
  enabled: true
```
Прогон с `--out /tmp/actionpulse/out-judge`. Записать: `items_repaired`, `items_weak`, `fleet_judge_calls`; из логов — вердикты (счётчики, без текстов писем). Сверка глазами: «отремонтированные» пункты действительно подтверждены телом письма? _(corp; baseline впервые)_

**Проба ⑥ — judge-канал записи (read-out для домашнего дизайна).** Канала записи judge-вызовов пока нет (под replay judge отключается). Записать из ⑤: сколько вызовов, какой латентности, формат вердиктов корректен ли. Дизайн канала — дома, не внутри.

**Проба ⑦ — первая калибровка reference-judge (report-only).** Нужен экспорт MM-реакций (`reactions.jsonl`: `{trace_id, evidence_id|item_key, emoji}` построчно). Если реакций ещё нет — пометить «skipped: нет реакций» и НЕ изобретать данные.
```bash
PYTHONPATH=src uv run python -m digest_core.cli eval-judge-run \
  --digest /tmp/actionpulse/out/digest-<дата>.json \
  --gold /tmp/actionpulse/reactions.jsonl \
  --mode reference --out /tmp/actionpulse/judge-records.jsonl
```
Забрать вывод (κ/α + regression) + records. Никаких гейтов по результату — только в архив `audits/baselines/` дома.

**Проба ⑧ — best-of-N вживую.** В `configs/config.yaml`:
```yaml
extract:
  best_of_n: 3
llm:
  stage_call_budgets:
    extractor: 4        # ADR-008 v2: бюджет поднимается в конфиге вместе с флагом
```
Прогон с `--out /tmp/actionpulse/out-bofn`. Записать: `run_meta.best_of_n` (scores/selected), латентность стадии LLM, какой кандидат выбран. Дайджест-варианты забрать. После пробы вернуть `best_of_n: 1` и бюджет 2. _(corp; baseline впервые)_

**Проба I-1 — инъекции вживую (threat-model §6).** Включить `llm.spotlight_evidence: true`, прогнать на снапшоте; если в ящике есть «инъекционное» письмо из fixtures-набора — проверить, что инструкции из тела не протекли в JSON. Записать diff поведения. _(corp)_

**Проба E-1 — реальный 401 (EP-3).** Один прогон с заведомо испорченным `LLM_TOKEN` (например, `LLM_TOKEN=ОТОЗВАННЫЙ`): ожидаемо — partial digest с банером «обновите LLM_TOKEN», reason `llm_auth_failed` в метриках, БЕЗ retry-шторма. Вернуть токен. _(corp)_

**Проба M-1 — exporter (EP-2).** Во время любого прогона из второго shell: `curl -s localhost:9108/metrics | head -30` → метрики отдаются; снять дамп в файл. Заодно: есть ли в корп-сети OTLP-коллектор для EP-8 (`спросить/посмотреть`) — записать ответ как вход решения O-1. _(corp)_

## (e) Критерии успеха

| Критерий | Цель | Сторона | ☐ |
|---|---|---|---|
| Тесты/replay/селектор | 769 ×2, eval-replay OK, proof OK | offline ✓ (2026-06-11) | ☑ |
| Дайджест доставлен в MM | exit 0, сообщение в канале | **corp** | ☐ |
| Read-out'ы проб 0–⑧ записаны | каждый пункт «Записать…» заполнен или «skipped: причина» | **corp** | ☐ |
| Деградации не роняют прогон | 429/таймаут/бюджет → warning + деградация, exit 0 | **corp** | ☐ |
| Ноль секретов/payload'ов в логах | проверить глазами перед выносом | **corp** | ☐ |

## (f) Bring-back — что выносим (вход `artifact-reconcile` следующего цикла)

Перед выносом: вычистить тела писем/PII из заметок; снапшоты/записи выносим как обычно (это и есть replay-корпус), но НЕ публикуем за пределы рабочих машин.

```
tar czf ~/actionpulse-ep14-$(date +%Y-%m-%d).tar.gz \
    /tmp/actionpulse/ews-snapshot-*.json \
    /tmp/actionpulse/llm-recording-*.json \
    /tmp/actionpulse/llm-rr-rec.json /tmp/actionpulse/llm-rr-rec.json.fleet.json \
    /tmp/actionpulse/out*/ \
    /tmp/actionpulse/eval-*.json \
    /tmp/actionpulse/judge-records.jsonl \
    /tmp/actionpulse/reactions.jsonl \
    /tmp/actionpulse/metrics-dump.txt \
    /tmp/actionpulse/EP14-READOUTS.md      # рукописные read-out'ы проб 0–⑧, I-1, E-1, M-1
```

Этот архив — то, с чего начинается следующий домашний цикл (reconcile → tau/floor/N решения → EP-15).
