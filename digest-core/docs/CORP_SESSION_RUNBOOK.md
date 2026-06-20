# Corp Session Runbook

> **Цель:** за одну сессию (~30 мин) в корпоративной сети собрать всё необходимое
> для автономной офлайн-разработки и запустить первый реальный дайджест.
>
> **Результат:** снапшоты EWS + LLM для replay, первый дайджест в Mattermost DM,
> eval-отчёт качества промпта.
>
> **Визит EP-14 (validation pack):** этот runbook — базовая процедура (§0–§5).
> Пробы флота (reranker/judge/best-of-N, ①–⑧) и их read-out'ы — в
> **`VISIT_CHECKLIST_EP14.md`** рядом: сначала пройди §1–§3 здесь, затем пробы там.
>
> **Activation cycle (Phase B → C):** to turn on the built-but-dark inventory (store / fleet /
> flywheel), follow the threaded sequence in **§10** — it gates on the PC-2 ADR
> (`PC2_DATA_HANDLING.md`) and links the store + EP-14 checklists.

---

## 0. Подготовка ДО корп-сессии (дома)

### 0.0 Актуальность корп-клона (обязательно)

Корп-клон легко отстаёт: org переехал на `pogorelov-labs` (2026-04-12), старый
`ruspg/...` remote молча не обновляется, а GitHub из корп-сети может быть недоступен.
Перед сессией сверь `git log -1` корп-клона с актуальным `origin/main`; если клон
отстал — обнови remote URL или принеси `git bundle` со свежим `main`.
Сессия 2026-06-10 целиком провалидировала устаревший код (`1b32fbe`) именно из-за
этого — см. `CORP_VALIDATION_FINDINGS_2026-06.md`.

### 0.1 Собрать секреты

Тебе понадобятся три значения. Подготовь их заранее:

```
EWS_PASSWORD=<пароль от Exchange>
LLM_TOKEN=<Bearer-токен LLM Gateway>
MM_WEBHOOK_URL=<URL Mattermost incoming webhook>
```

**Как получить MM_WEBHOOK_URL:**
1. Mattermost → любой канал (или DM с собой) → Integrations → Incoming Webhook
2. Создать webhook, скопировать URL вида `https://mm.corp.com/hooks/xxx`

### 0.2 Проверить MM-коннект (можно из любой сети)

Mattermost доступен отовсюду. Проверь заранее:

```bash
cd digest-core
export MM_WEBHOOK_URL="https://mm.corp.com/hooks/xxx"
python -m digest_core.cli mm-ping
```

Если `OK (HTTP 200)` — webhook работает. Если нет — почини до корп-сессии.

### 0.3 Настройка (интерактивный мастер)

На корп-машине (или заранее дома, чтобы ускорить корп-сессию):

```bash
cd digest-core
python -m digest_core.cli setup           # или make setup (сначала uv sync), или actionpulse → Settings
```

Мастер (вывод на английском) сначала автообнаружит логин/имя/email (скан метаданных
Keychain — отключается флагом `--no-autodetect`), затем проведёт по шагам. Пишет
**только** два файла: `~/.config/actionpulse/env` (chmod 600, secrets, systemd-compatible)
и `configs/config.yaml` (несекретное). **Золотое правило соблюдено:** ни один секрет
не попадает в YAML.

**Что спрашивает (по порядку), и что отвечать на корп-машине:**

| Шаг | Вопрос | Куда пишет | Ответ |
|-----|--------|-----------|-------|
| 1 | Corporate email (UPN) | `EWS_USER_UPN` (env) + выводит `ews.user_*` | твой корп-email; из него выводятся endpoint/login/aliases |
| 2 | EWS endpoint URL | `EWS_ENDPOINT` (env), `ews.endpoint` | авто (Keychain+DNS) или `https://owa.<домен>/EWS/Exchange.asmx` |
| 3 | EWS password | `EWS_PASSWORD` (env) | секрет; Enter — оставить текущий при повторном запуске |
| 4 | LLM gateway endpoint | `LLM_ENDPOINT` (env), `llm.endpoint` | OpenAI-фронт шлюза: `https://<gw>/v1/chat/completions` |
| 5 | LLM token (Bearer) | `LLM_TOKEN` (env) | секрет |
| 6 | Mattermost webhook URL | `MM_WEBHOOK_URL` (env) | incoming webhook канала-получателя |
| 6a | «Это приватный DM/канал?» | `deliver.mattermost.acknowledged_private` | y если личный канал (иначе каждый прогон предупреждает) |
| 6b | (опц., TTY) MM PAT + base URL | `MM_PAT`, `MM_BASE_URL` (env) | **нужно для MM-ingest и api-доставки/реакций**; для webhook-only — пропустить |
| 6c | DM consent ladder | `mm_source.dm_*` | `off` (умолч.) · `own_posts_only` · `selected`+allowlist · `all` — две верхние требуют consent (PII третьих лиц) |
| 7 | Report language | `report.language` | стрелочное меню ↑↓/jk; `en` (умолч.) / `ru` |
| опц. | Encrypted store | `DIGEST_STORE_KEY` (env), `store.enabled` | y чтобы включить 30-дн. зашифрованный стор; ключ **переиспользуется**, не регенерируется |
| опц. | TLS · Corporate CA | `ews.verify_ca` | авто-детект пути / экспорт цепочки из Keychain (macOS) / ручной путь |
| опц. | MCP register (после записи, macOS) | конфиги Claude/opencode/qwen | y чтобы зарегистрировать `actionpulse-mcp` в AI-CLI |

Перед записью — таблица **Review** (секреты маскируются: пароль `••••`, токен `••••XXXX`,
MM PAT как `set`) и вопрос `Save the configuration?`. На TTY после записи мастер
предлагает тест webhook'а.

**Чего мастер НЕ спрашивает — правит вручную в `configs/config.yaml` при необходимости:**
- `time.user_timezone` / `time.window` — **дефолт `Europe/Moscow` / `calendar_day`**; если
  машина/ящик в другом поясе, дата дайджеста уедет — проверь и поправь.
- `deliver.mattermost.auth_mode` — дефолт `webhook`; для **захвата post_id и реакций**
  (флайвил) переключи на `api` (+ `delivery_target` / `channel_name`).
- `ews.folders` — дефолт `["Inbox"]`; добавь общие папки, если нужно.
- `mm_source.channel_allowlist` — список публичных каналов для ingest.
- `retention` / `memory.dedup_*` / `reranker` / `judge` — «тёмный» инвентарь, включается
  после PC-2 (см. §10).

> **Идемпотентность:** повторный `setup` читает текущие значения как дефолты и сохраняет
> секреты. **Исключение:** `ews.user_aliases` перезаписываются (email + автодетект +
> машинный логин) — алиасы, добавленные руками в YAML, потеряются; добавляй их **после**
> финального setup.

**Альтернатива без TTY (systemd pre-provision, CI):**
```bash
cp deploy/env.example ~/.config/actionpulse/env
chmod 600 ~/.config/actionpulse/env
# Заполнить реальными значениями: EWS_PASSWORD, LLM_TOKEN, MM_WEBHOOK_URL
```

---

## 1. На корп-машине: setup (~5 мин)

> **Свежий Mac (нет клона):** один-единственный шаг заменяет §1.1 + §1.2 + §0.3 —
> ```bash
> /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)"
> ```
> Установит uv (+ Python 3.11), клонирует репозиторий, поставит зависимости и
> запустит мастер в том же терминале. Первый корп-запуск one-liner'а — пройдите §8.

### 1.1 Подтянуть код

```bash
cd ~/ActionPulse    # или где лежит клон
git fetch origin --prune
git checkout origin/main
cd digest-core
```

### 1.2 Установить зависимости

```bash
uv sync --native-tls
```

### 1.3 Команда `actionpulse` + секреты

После установки доступна глобальная команда **`actionpulse`** (лаунчер в `~/.local/bin`).
CLI **сам подгружает** `~/.config/actionpulse/env` при старте — ручной `source` больше
не нужен (запасной вариант ниже, если `~/.local/bin` не в PATH):

```bash
actionpulse diagnose          # глобальная команда; секреты загружаются автоматически
# Запасной вариант (PATH без ~/.local/bin или из чекаута):
#   cd ~/ActionPulse/digest-core
#   set -a && source ~/.config/actionpulse/env && set +a   # обычно НЕ требуется
#   uv run python -m digest_core.cli diagnose
```

Без аргументов `actionpulse` открывает интерактивное меню (Run · Dry run · Diagnose ·
Settings · Show config · Quit); все примеры ниже одинаково работают как
`actionpulse <cmd>` и как `uv run python -m digest_core.cli <cmd>`.

### 1.4 Проверить среду

```bash
actionpulse diagnose
```

Убедиться:
- ✓ EWS_PASSWORD / LLM_TOKEN / MM_WEBHOOK_URL: set (подхватились из env-файла без `source`)
- ✓ EWS endpoint reachable (если diagnose проверяет)
- ✓ В `configs/config.yaml` `ews.user_login` = **машинный логин** (`whoami`, напр. `ruapgr2`),
  НЕ локальная часть email. `user_upn` = полный email. Если NTLM не пускает — см. §8.3
  (формат логина: `ruapgr2` / `ruapgr2@megacorp.ru` / `DOMAIN\ruapgr2` — корп-вопрос).

---

## 2. Первый прогон: dry-run + snapshot (~5 мин)

Цель: убедиться, что EWS отдаёт письма, и захватить снапшот. Вывод — build-log на stderr
(`✓ INGEST    N messages (…)`); JSON-логи только в файл (путь печатается первой строкой).

```bash
actionpulse run \
    --dry-run \
    --force \
    --dump-ingest /tmp/actionpulse/ews-snapshot-$(date +%Y-%m-%d).json \
    --out /tmp/actionpulse/out \
    --state /tmp/actionpulse/state
```

> С PR #95 вывод — build-log на stderr (`✓ INGEST    124 messages (3.1s)`),
> а JSON-логи уходят **только в файл** (путь печатается первой строкой).
> Старое поведение: `--progress none`. В CI/пайпе автоматически plain-режим.

**Чеклист:**
- [ ] Exit code 0
- [ ] В выводе `✓ INGEST    N messages` (N > 0 — иначе пустой ящик или фильтр дат); подробности — в лог-файле
- [ ] Файл `/tmp/actionpulse/ews-snapshot-*.json` создан и не пустой
- [ ] Размер снапшота адекватен (100KB–5MB для типичного дня)

**Если 0 писем:**
```bash
# Попробовать шире — rolling 24h вместо calendar_day
python -m digest_core.cli run \
    --dry-run --force \
    --window rolling_24h \
    --dump-ingest /tmp/actionpulse/ews-snapshot-rolling.json \
    --out /tmp/actionpulse/out \
    --state /tmp/actionpulse/state
```

**Если EWS ошибка auth:**
- Проверить `EWS_PASSWORD`, `user_upn`, `user_login` в конфиге
- Проверить VPN/сеть: `curl -sI https://owa.corp-domain.ru/EWS/Exchange.asmx`

---

## 3. Полный прогон: LLM + delivery (~10 мин)

Цель: реальный дайджест с доставкой в MM.

```bash
python -m digest_core.cli run \
    --force \
    --replay-ingest /tmp/actionpulse/ews-snapshot-$(date +%Y-%m-%d).json \
    --record-llm /tmp/actionpulse/llm-recording-$(date +%Y-%m-%d).json \
    --out /tmp/actionpulse/out \
    --state /tmp/actionpulse/state
```

> Используем `--replay-ingest` чтобы не тянуть EWS повторно. Свежий снапшот
> из шага 2 уже содержит сегодняшние письма.

**Чеклист:**
- [ ] Exit code 0
- [ ] В Mattermost пришёл дайджест (проверь DM или канал webhook-а)
- [ ] Файлы в `/tmp/actionpulse/out/`:
  - `digest-YYYY-MM-DD.json` — структурированный дайджест
  - `digest-YYYY-MM-DD.md` — markdown-версия
  - `trace-*.meta.json` — метаданные прогона (trace_id, timing, LLM stats)
- [ ] LLM-запись: `/tmp/actionpulse/llm-recording-*.json` создана
- [ ] Из `trace-*.meta.json` выписать read-out гейта (первый реальный baseline):
  `support_recall`, `items_weak`, `items_quarantined`, `llm_budget`
  (calls/tokens против бюджета — D6); при включённых fleet-флагах там же
  `fleet_reranker_calls` / `fleet_judge_calls` / `best_of_n`
- [ ] Там же `stage_health` (U2): retries/errors по стадиям — ключ появляется
  **только при ненулевых значениях** (нет ключа = стадии прошли чисто);
  в `ews_fetch_stats` теперь есть `pages` / `retries` / `skipped`

**Если LLM timeout (120s):**
- Модель qwen35-397b-a17b тяжёлая, 120s может не хватить при нагрузке
- Посмотреть `trace-*.meta.json` → `llm_request_trace.latency_ms`
- При необходимости: увеличить `timeout_s` в config.yaml

**Если partial digest (секция "Status" / «Статус» при `report.language: ru`):**
- LLM упал после ретраев → см. `trace-*.meta.json` → `llm_request_trace.error`
- Повторить через 5 минут (rate limit или gateway overload)

---

## 4. Оценка качества (~5 мин)

```bash
python -m digest_core.cli eval-prompt \
    --digest /tmp/actionpulse/out/digest-$(date +%Y-%m-%d).json \
    --ingest-snapshot /tmp/actionpulse/ews-snapshot-$(date +%Y-%m-%d).json \
    --output-json /tmp/actionpulse/eval-$(date +%Y-%m-%d).json
```

**Что смотреть в eval-отчёте:**
- `evidence_id_valid` — все ли ссылки на evidence валидны
- `confidence_calibration` — не врёт ли модель с уверенностью
- `section_rules` — правильная ли категоризация (Мои действия / Срочное / К сведению)
- `errors` — список конкретных проблем

**Ручная оценка MM-дайджеста (прочитай его!):**
- [ ] Действия реально адресованы тебе? (или чужие)
- [ ] Срочное реально срочное? (или обычное)
- [ ] К сведению — не потерялось ли что-то важное?
- [ ] «Не подтверждено» (карантин D1) — пункты там действительно слабые,
      или гейт зря придрался? (вход для tau/floor дома)
- [ ] «↻ повтор» — пометки повторов корректны? (ledger D3)
- [ ] Есть ли галлюцинации — пункты, которых нет в письмах?
- [ ] Пропущены ли очевидные действия из сегодняшних писем?

**Реакции в MM (вход EP-15):** поставь 👍/👎 на пункты дайджеста прямо сейчас —
экспорт реакций потом превращается в gold-set (`eval-gold`), без них калибровка
`recall_floor` и judge-κ не стартует.

Запиши заметки — это вход для итерации промпта.

---

## 5. Забрать артефакты (~2 мин)

Всё ценное — в `/tmp/actionpulse/`. Скопируй на устройство,
доступное из внешней сети:

```bash
tar czf ~/actionpulse-corpus-$(date +%Y-%m-%d).tar.gz \
    /tmp/actionpulse/ews-snapshot-*.json \
    /tmp/actionpulse/llm-recording-*.json \
    /tmp/actionpulse/out/ \
    /tmp/actionpulse/eval-*.json
```

> Если в сессии включался реранкер, рядом с LLM-записью лежит sidecar
> `llm-recording-*.json.fleet.json` — добавь его в список выше (без реранкера
> файла нет, и tar споткнётся о пустой glob). Для визита EP-14 полный список
> выноса шире — см. `VISIT_CHECKLIST_EP14.md` §f.

**Что в архиве и зачем:**

| Файл | Зачем |
|------|-------|
| `ews-snapshot-*.json` | `--replay-ingest` офлайн — полный пайплайн без EWS |
| `llm-recording-*.json` | `--replay-llm` офлайн — полный пайплайн без LLM Gateway |
| `digest-*.json` + `.md` | Референс для golden-set eval |
| `trace-*.meta.json` | Timing, LLM stats, debug info |
| `eval-*.json` | Baseline eval score для сравнения с будущими промптами |

**Перенос:** USB / scp / MM DM (если <16KB per file) / облако.

> Для разовой валидации C1–C3 (EN-default + terminal design) список выноса
> шире — см. §9.4.

### 5.1 Логи и debug-bundle (для разбора снаружи)

**Где логи.** Структурированные JSON-логи пишутся в
`<data-home>/var/logs/run-<timestamp>.log` (путь печатается **первой строкой**
прогона). На TTY в терминал идёт build-log на stderr (`✓ INGEST …`), а JSON —
только в файл. Под systemd — ещё и в journal:
```bash
journalctl --user -u 'actionpulse-digest@*' -f                    # live во время прогона
journalctl --user -u 'actionpulse-digest@*' --since today -p err  # только ошибки
tail -f <data-home>/var/logs/run-*.log | jq .                     # live JSON из файла
```
Тоггл — `observability.log_to_file` (дефолт **on**). Логи **не** чистятся ретеншеном
(он трогает только дайджесты) — при необходимости `actionpulse clean --logs`.

**Редакция (что гарантировано).** Процессор structlog маскирует секреты/PII **до**
сериализации в JSON: имена полей (`password`/`token`/`secret`/`key`/`pat`/`mm_pat`/
`authorization`/`bearer`/`access_token`/`email`/…) и значения по паттернам (`Bearer …`,
email, SSN, номер карты) → `[[REDACTED]]`. **Тела писем/сообщений не логируются вообще** —
только счётчики и id. OTel-трейсинг (если включён) — тоже без контента.

**Чистый bundle наружу — `export-diagnostics` (предпочтительно):**
```bash
actionpulse export-diagnostics --date $(date +%Y-%m-%d) --out /tmp/actionpulse
#   или: actionpulse export-diagnostics --trace-id <id> --out /tmp/actionpulse
```
Кладёт `diagnostic-<trace>-<date>.tar.gz`: редактированный `run.log`,
**санитизированный** конфиг (секреты → `[[REDACTED]]`), метрики стадий, LLM-трейс
(модель/токены/finish-reason, без контента) и `env-info.txt` (только python/платформа/
версии пакетов — без env-дампа).

> ⚠️ В bundle **входят сами дайджесты** (`digest-*.json`/`.md`) — а это извлечённый
> контент (темы/цитаты), т.е. сам продукт. Обращайся с архивом как с экспортом почты:
> шифруй при передаче, ограничь получателей.
>
> `run --collect-logs` и `diagnose` зовут shell-скрипт `collect_diagnostics.sh`, который
> кладёт ещё env и `.state`. Секреты там теперь редактируются корректно (env-переменные
> `*PASSWORD/TOKEN/SECRET/KEY/PAT/WEBHOOK_URL` и YAML-значения), но для выноса наружу
> предпочитай `export-diagnostics`: чище и не тянет `.state`.

---

## 6. (Бонус) Установить systemd-таймер для dog-fooding

Если есть постоянная корп-машина (не VPN-сессия):

```bash
cd ~/ActionPulse/digest-core
bash deploy/install-systemd.sh
```

Проверить:
```bash
systemctl --user status actionpulse-digest.timer
systemctl --user list-timers
```

Ручной тест:
```bash
systemctl --user start actionpulse-digest@$(whoami).service
journalctl --user -u actionpulse-digest@$(whoami) -f
```

Если работает — дайджест будет приходить каждый день в 08:00.
Это запускает Phase 1 dog-fooding (цель: 5 дней подряд).

---

## 7. Офлайн: что делать с артефактами

### Replay полного пайплайна без сети

```bash
cd digest-core
python -m digest_core.cli run \
    --force \
    --replay-ingest ~/corpus/ews-snapshot-2026-04-01.json \
    --replay-llm ~/corpus/llm-recording-2026-04-01.json \
    --out /tmp/replay-out
```

Exit code 0, дайджест идентичен оригиналу. Теперь можно менять промпт
и сравнивать.

### Итерация промпта

```bash
# 1. Отредактировать промпт
vim prompts/extract_actions.v1.txt

# 2. Прогнать с реальным evidence (нужен LLM — или replay)
python -m digest_core.cli run \
    --force \
    --replay-ingest ~/corpus/ews-snapshot-2026-04-01.json \
    --out /tmp/prompt-test

# 3. Оценить
python -m digest_core.cli eval-prompt \
    --digest /tmp/prompt-test/digest-2026-04-01.json \
    --ingest-snapshot ~/corpus/ews-snapshot-2026-04-01.json

# 4. Сравнить с baseline eval
diff <(jq .scores ~/corpus/eval-2026-04-01.json) \
     <(jq .scores /tmp/prompt-test-eval.json)
```

> **Важно:** итерация промпта без `--replay-llm` требует доступа к LLM Gateway
> (корп-сеть). Для чисто офлайн-работы — replay воспроизводит старый ответ,
> но не покажет эффект изменения промпта. Для настоящей итерации нужен
> либо LLM-доступ, либо локальная модель.

---

## 8. Однократно: валидация one-liner setup (~10 мин)

Установщик и автообнаружение (PR #76/#78) проверены **вне периметра**; ниже —
то, что можно подтвердить только на реальном корп-Mac. Пройти один раз,
результаты — в `CORP_VALIDATION_FINDINGS` или issue.

**8.1 One-liner за корп-прокси.** В чистую папку:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)" install.sh --dir ~/ActionPulse-validate
```
Записать: дошёл ли `raw.githubusercontent.com`; какой источник uv сработал
(astral.sh или GitHub-fallback — видно в логе шага при падении); прошёл ли
`uv sync --native-tls` с первой попытки. При падении шага скрипт сам печатает
хвост лога (`$TMPDIR/actionpulse-install-log…`).

**8.2 Автообнаружение.** В панели «Автообнаружение» мастера записать:
- какой email выбран и с какими причинами (имя/домен/частота) — верный ли UPN;
- нашёлся ли EWS host из keychain-артефактов (`<login>@owa.…`) и стоит ли `DNS ✓`;
- что в «Домены сети» (dsconfigad / scutil).

Если email выбран **неверно** — сохранить весь вывод панели (кандидаты выше/ниже),
анонимизировать и завести issue: эвристика калибрована по одной машине (n=1).

**8.3 NTLM-логин.** `run --dry-run` с дефолтным `user_login` (= local-part email).
При auth-ошибке: раскомментировать `EWS_USER_LOGIN=<ad-login>` в
`~/.config/actionpulse/env` (мастер уже вписал подсказку с логином машины) и
повторить. Зафиксировать, какой вариант работает → решение, менять ли деривацию
по умолчанию в `config.py`.

**8.4 CA chain из Keychain.** Экспорт по алиасам Raiffeisen (Root + Issuing):
оба ли сертификата нашлись; EWS/LLM/MM ходят с verify без `verify_ssl=false`?

**8.5 MM live-check.** Тестовое сообщение мастера дошло в канал?

**8.6 Полный прогон.** §2–§3 как обычно — дайджест доставлен.

```
□  8.1 one-liner: curl + uv + sync за прокси
□  8.2 автообнаружение: UPN верный, EWS host найден, DNS ✓
□  8.3 NTLM: email-local или EWS_USER_LOGIN — что работает
□  8.4 CA chain: Root + Issuing экспортированы, TLS verify OK
□  8.5 MM тест-сообщение в канале
□  8.6 полный run → дайджест в MM
```

---

## 9. Однократно: валидация EN-default + terminal design (C1–C3, ~20 мин)

PRs #92–#98 (английский по умолчанию, canonical section keys, prompt
`extract_actions.en.v2`, live-прогресс) проверены **вне периметра**. Ниже —
три вопроса, на которые отвечает только корп-машина (+ §9.4: U-track UX,
PR #105/#107). Результаты — новый раздел в
`CORP_VALIDATION_FINDINGS_2026-06.md` (+ issues), артефакты — §9.5.

### 9.1 C1 — качество EN-экстракции vs RU baseline (критичное)

Gold F1 = 0.601 измерялся на RU-пайплайне; качество en.v2 на реальных RU
письмах не измерено. Один снапшот — два прогона (4 LLM-вызова суммарно,
в 15 RPM укладывается; запускать последовательно):

```bash
SNAP=/tmp/actionpulse/ews-snapshot-$(date +%Y-%m-%d).json

# RU-референс (старый контракт вывода):
DIGEST_REPORT_LANGUAGE=ru python -m digest_core.cli run --force \
    --replay-ingest "$SNAP" \
    --record-llm /tmp/actionpulse/llm-rec-ru.json \
    --out /tmp/actionpulse/out-ru --state /tmp/actionpulse/state-ru

# EN (новый дефолт):
python -m digest_core.cli run --force \
    --replay-ingest "$SNAP" \
    --record-llm /tmp/actionpulse/llm-rec-en.json \
    --out /tmp/actionpulse/out-en --state /tmp/actionpulse/state-en
```

Сравнить и записать таблицей (RU vs EN):
- [ ] items по секциям (digest-*.json) — расхождение ≤1 пункта?
- [ ] `support_recall`, `items_weak`, `items_quarantined` из `trace-*.meta.json`
- [ ] **цитаты остались на языке письма** (verbatim, НЕ переведены) — инвариант
      citation gate; выборочно сверить 3–5 quote против тел писем
- [ ] EN-заголовки пунктов адекватны исходникам (глазами, 5–10 пунктов)

Порог решения: потеря >1 пункта или падение `support_recall` >0.05 →
issue + рассмотреть `report.language: ru` как временный дефолт корп-профиля
или prompt v2.1. **`llm-rec-en.json` — первая запись с en.v2-промптом**:
записи request-hash-keyed, старые RU-записи en.v2 не реплеят — без этого
файла офлайн-разработка EN-ветки невозможна.

### 9.2 C2 — Terminal.app: палитра и live-прогресс

Корп-профиль Terminal.app — 256 цветов, часто **светлый фон**:

- [ ] мастер: `uv run python -m digest_core.cli setup` — баннер-градиент,
      токены читаемы, стрелочное меню языка (↑↓/jk/Enter/Esc=default, 1-9)
- [ ] live: повторный run с `--replay-llm /tmp/actionpulse/llm-rec-en.json` —
      спиннер, тикающий elapsed, постоянные ✓-строки над футером,
      `↑/↓ tok` в строке LLM; футер исчез по завершении, курсор восстановлен
- [ ] `--progress plain` и `--progress none` — ожидаемое поведение
- [ ] `NO_COLOR=1 …` (без цвета, глифы на месте) и `TERM=dumb …` (плоский вывод)

Захват: `mkdir -p /tmp/actionpulse/visual` — скриншоты (мастер/меню/live) +
typescript: `script -q /tmp/actionpulse/visual/live-run.typescript python -m
digest_core.cli run …` (в настоящем Terminal `script` работает).

### 9.3 C3 — EN-дайджест в корп-Mattermost

Из EN-прогона 9.1 (если delivery включён; иначе повторить с webhook):
- [ ] заголовок "Action digest — YYYY-MM-DD", секции My actions / Urgent / FYI
- [ ] глифы ✓ ⚠ ↻ ↳ и саблайны `↳ ev: …` рендерятся в MM
- [ ] при длинном дайджесте — заголовки частей "part i/n"
- [ ] скриншот сообщения → `/tmp/actionpulse/visual/`

### 9.4 C4 — U-track UX на реальном прогоне (U2 live-телеметрия + U4 reader)

Вне периметра U2/U4 проверены на replay/pty; реальная сеть отвечает на два
вопроса (наблюдательно, без гейта; ~5 мин поверх обычного прогона):

**U2 — intra-stage телеметрия (#105):**
- [ ] во время live-прогона футер показывает `N messages · page K` на INGEST
      и `n/total messages` на NORMALIZE (не только тикающий elapsed)
- [ ] если случился transient retry (EWS reconnect / LLM 429) — футер
      пожелтел сразу и показал `↻ retry n/8 — причина`; в постоянной строке
      стадии появился суффикс `↻N retries`
- [ ] `trace-*.meta.json` → `stage_health` согласуется с увиденным
      (ключ отсутствует = стадии чистые); `ews_fetch_stats.pages` ≈ N/100

**U4 — reader на реальном дайджесте (#107):**
- [ ] `actionpulse read` — у пунктов заполнены From/Subject (реальные
      кириллические темы/имена; обрезка длинных строк в списках корректна)
- [ ] карточка: цитата verbatim на языке письма + `evidence … · msg …`
- [ ] Esc-навигация вверх по уровням; пагинация на секции >6 пунктов
- [ ] скриншот карточки → `/tmp/actionpulse/visual/`

**Fleet — калибровка embedding-merge (C6, гейтит reranker-band):**
- [ ] на снапшоте дня включить `threading.embedding_merge: true` (PC-2: эмбеддинги
      корп-only; один батч-вызов `/v1/embeddings`) + `--record-llm` → fleet-sidecar
- [ ] сравнить треды с/без флага: каждое слияние — правда один разговор?
      (точность глазами; записать precision и порог)
- [ ] при необходимости подобрать `threading.similarity_threshold` (дефолт 0.85);
      вынести вердикт: пригоден ли cosine-tier как база для reranker-band
- [ ] sidecar забрать наружу — оффлайн-калибровка порога без сети

**U7 — `actionpulse explain` на реальном прогоне (C5):**
- [ ] после любого partial/failed прогона: `actionpulse explain` — карточка
      likely cause / explanation / next steps; вердикт согласуется с
      телеметрией (это ОДИН доп. вызов гейтвея на своём бюджете, не из 2
      вызовов пайплайна)
- [ ] качество вердикта на реальной телеметрии записать (полезен/мимо);
      при `report.language: ru` ответ по-русски
- [ ] оффлайн-поведение уже проверено вне периметра (fail-fast с подсказкой)

Примечание: PIPELINE_VERSION 1.1.0→1.2.0 — первый прогон пересоберёт
вчерашний артефакт (один лишний LLM-вызов, ожидаемо, не баг).

### 9.5 Вынос (дополнение к §5)

```bash
tar czf ~/actionpulse-c1c3-$(date +%Y-%m-%d).tar.gz \
    /tmp/actionpulse/out-ru /tmp/actionpulse/out-en \
    /tmp/actionpulse/llm-rec-ru.json /tmp/actionpulse/llm-rec-en.json \
    /tmp/actionpulse/visual
```

Вернуть: архив + заполненная таблица 9.1 + вердикты 9.2/9.3/9.4 →
`CORP_VALIDATION_FINDINGS_2026-06.md`; issues на каждое расхождение.

---

## 10. Activation cycle — turning on the dark inventory (Phase B → C)

§0–§9 set up and run the **Conservative** deployment (extractor `/v1/chat` only; fleet off; store
keyword-only; webhook delivery). This section threads the **activation cycle** that flips the
built-but-dark inventory to live — the single highest-value corp sequence (see
[`docs/planning/STATUS.md`](../../docs/planning/STATUS.md) for what each unlock buys). §0–§9 are the
~30-min session; this arc adds a ~1–2-week api-delivery window + an offline calibration pass. Run
in order — each step links its detailed checklist.

0. **PC-2 first (the gate).** Before content reaches a new endpoint, get the platform team's written
   logging / retention / residency statement per endpoint and fill the `<TBD>`s in
   [`PC2_DATA_HANDLING.md`](./PC2_DATA_HANDLING.md); mark each endpoint **CONFIRMED**. No flag flips
   until its endpoint is confirmed. (PC-1 service-account role is already ✅ Personal — §"today".)
1. **Prove ingest live.** §1–§3 exercise EWS; additionally prove **MM ingest** (never run in prod):
   `actionpulse run --sources ews,mm` (needs `MM_PAT` + `MM_BASE_URL` — the wizard collects them now,
   §0.3). Confirm both sources land messages.
2. **Store live-validation.** Enable the store (`store.enabled`), run, then `actionpulse store reembed`
   against the real gateway and exercise `search` / `ask` / `history` on real mail — follow
   [`STORE_VALIDATION_CHECKLIST.md`](./STORE_VALIDATION_CHECKLIST.md). (`reembed` hits `/v1/embeddings`
   → that endpoint must be CONFIRMED in PC-2.)
3. **Quality validation (EP-14).** Run the fleet probes + read-outs in
   [`VISIT_CHECKLIST_EP14.md`](./VISIT_CHECKLIST_EP14.md): items/section, `support_recall`,
   weak/quarantined counts, the verbatim-quote invariant; EN-vs-RU per §9.1.
4. **Flip the fleet — per CONFIRMED endpoint only.** With evidence from 2–3 and the PC-2 rows
   confirmed, set `reranker.enabled` / `enable_relevance` / `judge.enabled` /
   `threading.embedding_merge`. Each rides its own RPM bucket + stage budget; any failure
   degrades-not-drops. **Also flip `llm.spotlight_evidence`** (C11 injection containment — fences
   untrusted bodies as DATA across extractor + `ask` + judge; default-off pending exactly this
   real-LLM eval). Confirm the EP-14 quality read-out doesn't regress with it on, and probe
   `tests/fixtures/emails_injection.json` against the real LLM (the live half of the red-team set).
5. **api-mode delivery (~1–2 weeks).** Configure the owner-only channel id + PAT (`auth_mode=api`);
   each run records delivered post-ids to the `delivered-posts` ledger. Let recipients react ✓/✗ over
   the window — this is the flywheel's fuel.
6. **Close the flywheel (Phase C — offline after harvest).**
   `actionpulse reactions harvest --gold-out gold.jsonl` (the bridge: emits the per-reaction JSONL
   directly) → `eval-gold --reactions gold.jsonl` → `eval-calibrate` → set `recall_floor > 0` and
   flip the judge gate. Trust goes from *annotate-only* to **measured & gated**; publish the first
   real P/R/F1.

> **Order matters:** step 0 gates everything; 2 needs 0's embeddings row; 4 needs 0 + the 2–3
> evidence; 6 needs 5's reactions. Steps 5–6 span the ~2-week window + an offline calibration pass.

## Чеклист корп-сессии (quick ref)

```
□  Секреты готовы (EWS_PASSWORD, LLM_TOKEN, MM_WEBHOOK_URL)
□  MM ping OK
□  uv sync --native-tls
□  diagnose — все ✓
□  dry-run + dump-ingest → snapshot создан, N > 0 писем
□  full run + record-llm → дайджест в MM
□  read-out гейта выписан (support_recall / items_weak / items_quarantined / llm_budget)
□  реакции 👍/👎 на пункты поставлены (вход EP-15)
□  eval-prompt → baseline score
□  tar.gz артефакты → скопированы наружу
□  (визит EP-14) пробы ①–⑧ + I-1/E-1/M-1 по VISIT_CHECKLIST_EP14.md
□  (однократно) §8 — валидация one-liner setup
□  (однократно) §9 — C1–C3: EN vs RU прогоны + llm-rec-en.json + visual/ + вердикты
□  (однократно) §9.4 — C4: U2 live-телеметрия + U4 reader на реальном прогоне
□  (бонус) systemd timer установлен
```

### Activation cycle (§10) — quick ref

```
□  PC-2: per-endpoint statements filled → each CONFIRMED in PC2_DATA_HANDLING.md   (gate)
□  ingest live: actionpulse run --sources ews,mm — both sources land
□  store: store.enabled → run → store reembed → search/ask/history (STORE_VALIDATION_CHECKLIST)
□  EP-14 quality pack passed (VISIT_CHECKLIST_EP14)
□  fleet flipped (only CONFIRMED endpoints): reranker / enable_relevance / judge / embedding_merge
□  api-mode delivery live (owner channel + PAT) → delivered-posts ledger fills (~1–2 wks)
□  flywheel closed: reactions harvest --gold-out → eval-gold --reactions → eval-calibrate → recall_floor>0 + judge gate
□  PC-2 Status → ACCEPTED; first real P/R/F1 published
```

Время: ~30 мин при удачном раскладе, ~45 мин с отладкой.
