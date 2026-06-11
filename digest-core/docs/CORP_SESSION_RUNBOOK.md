# Corp Session Runbook

> **Цель:** за одну сессию (~30 мин) в корпоративной сети собрать всё необходимое
> для автономной офлайн-разработки и запустить первый реальный дайджест.
>
> **Результат:** снапшоты EWS + LLM для replay, первый дайджест в Mattermost DM,
> eval-отчёт качества промпта.

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
python -m digest_core.cli setup
```

Мастер задаст 6 вопросов (корпоративный email, EWS endpoint, EWS пароль, LLM endpoint, LLM токен, Mattermost webhook URL) и сгенерирует:
- `~/.config/actionpulse/env` (chmod 600, systemd-compatible)
- `configs/config.yaml`

Безопасно перезапускать: `python -m digest_core.cli setup` читает существующие значения как дефолты.

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

### 1.3 Загрузить секреты

```bash
set -a
source ~/.config/actionpulse/env
set +a
```

### 1.4 Проверить среду

```bash
python -m digest_core.cli diagnose
```

Убедиться:
- ✓ EWS_PASSWORD: set
- ✓ LLM_TOKEN: set
- ✓ MM_WEBHOOK_URL: set
- ✓ EWS endpoint reachable (если diagnose проверяет)

---

## 2. Первый прогон: dry-run + snapshot (~5 мин)

Цель: убедиться, что EWS отдаёт письма, и захватить снапшот.

```bash
python -m digest_core.cli run \
    --dry-run \
    --force \
    --dump-ingest /tmp/actionpulse/ews-snapshot-$(date +%Y-%m-%d).json \
    --out /tmp/actionpulse/out \
    --state /tmp/actionpulse/state
```

**Чеклист:**
- [ ] Exit code 0
- [ ] В логе видно `emails_processed: N` (N > 0 — иначе пустой ящик или фильтр дат)
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

**Если LLM timeout (120s):**
- Модель qwen35-397b-a17b тяжёлая, 120s может не хватить при нагрузке
- Посмотреть `trace-*.meta.json` → `llm_request_trace.latency_ms`
- При необходимости: увеличить `timeout_s` в config.yaml

**Если partial digest (секция "Статус"):**
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
- [ ] Есть ли галлюцинации — пункты, которых нет в письмах?
- [ ] Пропущены ли очевидные действия из сегодняшних писем?

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

**Что в архиве и зачем:**

| Файл | Зачем |
|------|-------|
| `ews-snapshot-*.json` | `--replay-ingest` офлайн — полный пайплайн без EWS |
| `llm-recording-*.json` | `--replay-llm` офлайн — полный пайплайн без LLM Gateway |
| `digest-*.json` + `.md` | Референс для golden-set eval |
| `trace-*.meta.json` | Timing, LLM stats, debug info |
| `eval-*.json` | Baseline eval score для сравнения с будущими промптами |

**Перенос:** USB / scp / MM DM (если <16KB per file) / облако.

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

## Чеклист корп-сессии (quick ref)

```
□  Секреты готовы (EWS_PASSWORD, LLM_TOKEN, MM_WEBHOOK_URL)
□  MM ping OK
□  uv sync --native-tls
□  diagnose — все ✓
□  dry-run + dump-ingest → snapshot создан, N > 0 писем
□  full run + record-llm → дайджест в MM
□  eval-prompt → baseline score
□  tar.gz артефакты → скопированы наружу
□  (однократно) §8 — валидация one-liner setup
□  (бонус) systemd timer установлен
```

Время: ~30 мин при удачном раскладе, ~45 мин с отладкой.
