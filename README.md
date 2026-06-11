# ActionPulse

**Daily pulse of actions from your inbox.**

Каждое утро — автоматический дайджест из корпоративной почты: что от тебя ждут, что срочно, что решили. Каждый пункт трассируется до оригинального письма.

---

## Что это

Single-tenant CLI инструмент. Читает Exchange inbox, прогоняет через 8-стадийный pipeline, доставляет итог в Mattermost через **incoming webhook** (целевой канал задаётся при создании webhook в Mattermost).

**Не суммаризатор** — LLM извлекает факты из писем, а не пишет от себя. Три секции на выходе:
- **Мои действия** — что от тебя ожидают
- **Срочное** — дедлайны ≤2 рабочих дней
- **К сведению** — что решили без тебя

**Не SaaS** — работает на корп-инфраструктуре, данные не покидают периметр.

---

## Быстрый старт

Одна команда на чистом macOS — без предустановленного Python и Homebrew:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)"
```

Скрипт проверит окружение, поставит [uv](https://docs.astral.sh/uv/) (он сам скачает Python 3.11 — версия системного Python не важна), склонирует репозиторий в `~/ActionPulse`, установит зависимости и запустит мастер настройки в этом же окне терминала. Повторный запуск безопасен: обновит код и предложит текущие значения как ответы по умолчанию. Флаги: `--dir`, `--ref`, `--no-wizard` (headless).

<details>
<summary>Ручная установка (git clone + make)</summary>

```bash
git clone https://github.com/pogorelov-labs/ActionPulse.git
cd ActionPulse/digest-core

# Установка зависимостей + интерактивный мастер (6 вопросов, без редактирования файлов)
make setup
```

</details>

После установки:

```bash
cd ~/ActionPulse/digest-core

# Загрузить секреты в текущую сессию и проверить конфигурацию
set -a && source ~/.config/actionpulse/env && set +a
uv run python -m digest_core.cli diagnose

# Dry-run (без LLM, только ingest + normalize)
uv run python -m digest_core.cli run --dry-run

# Полный запуск
uv run python -m digest_core.cli run
```

Мастер задаст: корпоративный email, EWS endpoint, EWS пароль, LLM endpoint, LLM токен, Mattermost webhook URL. Сгенерирует `~/.config/actionpulse/env` (chmod 600) и `configs/config.yaml`. Повторная настройка: `make setup` или напрямую `uv run python -m digest_core.cli setup` из `digest-core/` (оба вызывают один и тот же wizard).

Если видите ошибку `No module named 'digest_core'`, значит команда запущена системным Python вне окружения проекта. Используйте `uv run python -m ...` (как в примерах выше) или активируйте `.venv` вручную.

### Mattermost интеграция (важно)
ActionPulse использует **incoming webhook** Mattermost для **доставки** готового дайджеста (Stage 8). Для чтения сообщений/DM пассивно собирать данные не требуется — в MVP не используется API/WebSocket “для чтения”.

Подробнее — в [`digest-core/CLAUDE.md`](digest-core/CLAUDE.md).

---

## Архитектура

```
Exchange (EWS)
    └── INGEST → NORMALIZE → THREADS → EVIDENCE → SELECT → LLM → ASSEMBLE → DELIVER
                                                                               └── Mattermost (webhook)
```

LLM: `qwen35-397b-a17b` через корп. gateway, 15 RPM, **max 2 вызова за запуск** (1 primary extraction + опциональный quality retry, см. ADR-008).

Полные контракты стадий: [`digest-core/docs/ARCHITECTURE.md`](digest-core/docs/ARCHITECTURE.md).

---

## Принципы

| | |
|--|--|
| **Extract-over-Generate** | LLM извлекает из evidence, каждый пункт привязан к `evidence_id` |
| **Traceability** | Пункт → `evidence_id` → `source_ref` → оригинальное письмо |
| **Privacy-first** | Локальный модуль маскировки PII снят в 1.1.0; обработка персональных данных на стороне корпоративного LLM Gateway. Тела писем и секреты не пишутся в логи |
| **Idempotency** | Артефакты за выбранную дату: при повторных запусках в окне **T−48h** пропуск пересборки, если JSON/MD уже свежие (`run --force` обходит проверку) |

---

## Разработка

```bash
cd digest-core
make test    # все тесты (mocked, без сети)
make lint
make smoke   # dry-run smoke test
```

EWS и LLM Gateway — только с корп. сети. Для разработки вне периметра:

```bash
# Снять снапшот inbox'а изнутри
python -m digest_core.cli run --dump-ingest /tmp/snapshot.json

# Воспроизвести снаружи
python -m digest_core.cli run --replay-ingest /tmp/snapshot.json
```

---

## License

Internal corporate use only.
