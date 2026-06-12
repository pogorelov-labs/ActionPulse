# Тестирование ActionPulse

Документация по ручному тестированию ActionPulse, передаче результатов и
референсным отчётам.

> 📝 **Если вы пришли за инструкцией по установке** — используйте
> [`README.md`](../../README.md) и `cd digest-core && make setup`. Setup wizard
> собирает всё за 7 вопросов (PR #32 / #33 / #92).

---

## Когда что использовать

| Сценарий | Документ |
|----------|---------|
| Ручной чек-лист по этапам (после установки) | [`MANUAL_TESTING_CHECKLIST.md`](./MANUAL_TESTING_CHECKLIST.md) |
| Подготовить и отправить архив диагностики | [`SEND_RESULTS.md`](./SEND_RESULTS.md) |
| Шаблоны отчётов о тестировании | [`examples/successful_test_report.md`](./examples/successful_test_report.md), [`examples/failed_test_report.md`](./examples/failed_test_report.md) |
| Специфика корпоративных ноутбуков | [`examples/corporate_laptop_setup.md`](./examples/corporate_laptop_setup.md) |
| Стратегия автотестов для разработчиков | [`../development/TESTING.md`](../development/TESTING.md) |
| Общий troubleshooting | [`../troubleshooting/TROUBLESHOOTING.md`](../troubleshooting/TROUBLESHOOTING.md) |

---

## Реальные команды для тестирования

Все команды запускаются из `digest-core/`. Setup должен быть выполнен заранее
(`make setup` — см. корневой [`README.md`](../../README.md)).

```bash
cd digest-core

# Загрузить секреты в текущую сессию
set -a && source ~/.config/actionpulse/env && set +a

# 1. Диагностика окружения (без сети)
python -m digest_core.cli diagnose

# 2. Smoke-тест (dry-run, без LLM, без MM)
make smoke
# то же самое: python -m digest_core.cli run --dry-run

# 3. Все unit-тесты (с моками; не требует корп-сети)
make test

# 4. Полный pipeline (только с корп-сети — EWS + LLM + MM)
python -m digest_core.cli run

# 5. Сбор архива диагностики (логи, метрики, конфиг без секретов)
scripts/collect_diagnostics.sh

# 6. Экспорт диагностики через MM по trace_id
python -m digest_core.cli export-diagnostics --trace-id <id> --send-mm
```

См. [`digest-core/CLAUDE.md`](../../digest-core/CLAUDE.md) для полного списка
команд и gotchas (timeout, idempotency, replay-режим).

---

## Замечания

- **Реальный pipeline (EWS + LLM)** работает только из корп-сети. Для разработки
  снаружи периметра — `--dump-ingest` / `--replay-ingest` (см.
  [`digest-core/CLAUDE.md` § Offline Development](../../digest-core/CLAUDE.md)).
- **Unit-тесты (`make test`)** работают из любого места — все внешние сервисы замоканы.
- **Exit codes**: `0` — успех (включая `--dry-run`), `1` — ошибка, `2` —
  только при `--validate-citations` failure. См. таблицу exit codes в
  [`digest-core/CLAUDE.md`](../../digest-core/CLAUDE.md).

---

## Архивные документы

Ранее этот каталог содержал большой E2E-гайд (сейчас в архиве: [`docs/legacy/E2E_TESTING_GUIDE.md`](../legacy/E2E_TESTING_GUIDE.md)),
implementation summary, и validation-чек-лист. Эти файлы ссылались на
shell-скрипты (`install_interactive.sh`, `doctor.sh`, `test_run.sh`), которые
никогда не существовали в репозитории. Они перенесены в
[`docs/legacy/`](../legacy/) с банерами, объясняющими причину архивации.
Не используйте их — реальный путь setup-а описан в корневом
[`README.md`](../../README.md).
