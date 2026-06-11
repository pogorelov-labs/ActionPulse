# Quick Start Guide

Быстрый старт с ActionPulse - получите первый дайджест за 5 минут.

## Установка

Одна команда на чистом macOS (Python и Homebrew не нужны — `uv` сам скачает Python 3.11):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/pogorelov-labs/ActionPulse/main/install.sh)"
```

Скрипт ставит `uv`, клонирует репозиторий в `~/ActionPulse` (спросит, можно изменить), устанавливает зависимости и запускает мастер настройки в этом же окне терминала. Повторный запуск безопасен. Флаги: `--dir DIR`, `--ref REF`, `--no-wizard`.

Ручной вариант:

```bash
git clone https://github.com/pogorelov-labs/ActionPulse.git
cd ActionPulse/digest-core
make setup    # установка зависимостей + интерактивный мастер (6 вопросов)
```

Мастер сначала сам находит логин, имя и корпоративный email (скан метаданных Keychain, всё локально; `--no-autodetect` отключает), затем спросит недостающее: EWS endpoint, EWS пароль, LLM endpoint, LLM токен, Mattermost webhook URL. Сгенерирует:
- `~/.config/actionpulse/env` (chmod 600, systemd-compatible)
- `configs/config.yaml`

Безопасно перезапускать: `python -m digest_core.cli setup` читает существующие значения как дефолты. Секреты скрываются при вводе.

### Headless / CI (без TTY)

Если TTY недоступен (CI, systemd pre-provision), используйте ручной флоу:

```bash
mkdir -p ~/.config/actionpulse
cp digest-core/deploy/env.example ~/.config/actionpulse/env
chmod 600 ~/.config/actionpulse/env
# Заполнить: EWS_PASSWORD, LLM_TOKEN, MM_WEBHOOK_URL
cp digest-core/configs/config.example.yaml digest-core/configs/config.yaml
# Отредактировать endpoint'ы, user_upn, aliases
```

## Первый запуск

### 1. Активируйте окружение

```bash
set -a && source ~/.config/actionpulse/env && set +a
```

### 2. Проверьте конфигурацию

```bash
cd digest-core
python -m digest_core.cli diagnose
```

### 3. Запустите первый дайджест

> Python отдельно ставить не нужно: `uv` использует закреплённую в репозитории версию (`.python-version`, сейчас 3.11) и скачает её сам при необходимости.

```bash
# Тестовый запуск (без LLM) - проверяет только EWS подключение
uv run python -m digest_core.cli run --dry-run

# Полный запуск для сегодня
uv run python -m digest_core.cli run
```

## Основные команды

```bash
# Базовый запуск (дайджест за сегодня)
uv run python -m digest_core.cli run

# Для конкретной даты
uv run python -m digest_core.cli run --from-date 2024-01-15

# Dry-run режим (только ingest+normalize, без LLM)
uv run python -m digest_core.cli run --dry-run

# Другая модель LLM
uv run python -m digest_core.cli run --model "qwen35-397b-a17b"

# Кастомная директория вывода
uv run python -m digest_core.cli run --out ./my-digests

# Используя make
make run
```

## Просмотр результатов

После успешного запуска в директории `digest-core/out/` будут созданы файлы:

- `digest-YYYY-MM-DD.json` - структурированные данные с полной схемой
- `digest-YYYY-MM-DD.md` - человеко-читаемый дайджест (≤400 слов)

### Пример просмотра результатов

```bash
# Посмотреть JSON структуру
cat digest-core/out/digest-2024-01-15.json | jq '.'

# Посмотреть Markdown дайджест
cat digest-core/out/digest-2024-01-15.md

# Найти все дайджесты
ls -la digest-core/out/digest-*.md
```

## Структура выходных файлов

- Каждый элемент содержит `evidence_id` для ссылки на источник
- `source_ref` указывает на исходное сообщение
- `confidence` показывает уверенность в извлечении (0-1)
- `owners_masked` содержит замаскированные имена ответственных

## Автоматизация

### Настройка cron для ежедневного запуска

```bash
# Добавить в crontab (запуск каждый день в 8:00)
crontab -e

# Добавить строку:
0 8 * * * cd /path/to/ActionPulse/digest-core && set -a && source ../.env && set +a && .venv/bin/python -m digest_core.cli run
```

### Использование systemd (Linux)

Для детальных инструкций по настройке systemd таймера см. [DEPLOYMENT.md](../operations/DEPLOYMENT.md#scheduling).

## Мониторинг и отладка

```bash
# Проверить метрики Prometheus
curl http://localhost:9108/metrics

# Проверить health check
curl http://localhost:9109/healthz

# Посмотреть логи (если запущено в Docker)
docker logs digest-core-container

# Проверить конфигурацию
cd digest-core && make env-check
```

## Что дальше?

### Изучите документацию

- 📚 [Полная документация](../README.md)
- 🔧 [Детальное руководство по установке](INSTALL.md)
- 🐳 [Развертывание в продакшене](../operations/DEPLOYMENT.md)
- 📊 [Мониторинг и observability](../operations/MONITORING.md)
- 🚨 [Решение проблем](../troubleshooting/TROUBLESHOOTING.md)

### Настройте автоматизацию

- ⏰ [Настройка автоматизации](../operations/AUTOMATION.md)
- 🔄 [Управление состоянием и ротация](../operations/AUTOMATION.md#state-management)
- 📈 [Мониторинг производительности](../operations/MONITORING.md)

### Расширьте функциональность

- 🗺️ [Roadmap развития](../planning/ROADMAP.md)
- 🤖 [Интеграция с Mattermost](../planning/MATTERMOST_INTEGRATION.md)
- 📊 [Метрики качества](../reference/QUALITY_METRICS.md)

## Получение помощи

### Если что-то не работает

1. **Проверьте [TROUBLESHOOTING.md](../troubleshooting/TROUBLESHOOTING.md)**
2. **Запустите диагностику:**
   ```bash
   cd digest-core && make env-check
   ```
3. **Проверьте логи:**
   ```bash
   python -m digest_core.cli run --verbose
   ```

### Создание issue

При создании issue включите:
- Вывод `make env-check`
- Соответствующие логи
- Конфигурацию (без секретов)
- Шаги для воспроизведения

---

**Поздравляем!** 🎉 Вы успешно запустили ActionPulse. Теперь у вас есть автоматический дайджест корпоративных коммуникаций!
