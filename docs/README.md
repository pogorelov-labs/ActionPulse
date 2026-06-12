# ActionPulse Documentation

Полная документация по ActionPulse - системе ежедневного дайджеста корпоративных коммуникаций.

## 📚 Обзор документации

ActionPulse - это privacy-first система для автоматического создания дайджестов корпоративных коммуникаций с использованием LLM. Система извлекает действия, упоминания и важную информацию из email и других источников, создавая краткие, структурированные отчёты.

## 🚀 Быстрый старт

### Установка

```bash
git clone https://github.com/pogorelov-labs/ActionPulse.git
cd ActionPulse/digest-core
make setup    # установка зависимостей + интерактивный мастер (7 вопросов)
```

### Первый запуск

```bash
set -a && source ~/.config/actionpulse/env && set +a
cd digest-core
python -m digest_core.cli diagnose                 # Проверить конфигурацию
python -m digest_core.cli run --dry-run            # Тест (без LLM)
python -m digest_core.cli run                      # Полный запуск
```

📖 **[Полное руководство по быстрому старту →](installation/QUICK_START.md)**

## 📋 Содержание документации

### 🛠️ Установка и настройка

- **[Quick Start](installation/QUICK_START.md)** - Быстрый старт за 5 минут
- **[Installation Guide](installation/INSTALL.md)** - Подробное руководство по установке

### 🏗️ Операции и развертывание

- **[Deployment](operations/DEPLOYMENT.md)** - Развертывание в Docker, systemd, dedicated machine
- **[Automation](operations/AUTOMATION.md)** - Настройка автоматизации (cron, systemd timer, rotation)
- **[Monitoring](operations/MONITORING.md)** - Мониторинг, метрики, health checks, логирование

### 👨‍💻 Разработка

- **[Architecture](../digest-core/docs/ARCHITECTURE.md)** - Архитектура системы и компоненты (**единственный SoT**; `development/ARCHITECTURE.md` — только редирект)
- **[Technical Details](development/TECHNICAL.md)** — редирект на `digest-core/docs/ARCHITECTURE.md` (SoT)
- **[Implementation Guide](development/IMPLEMENTATION_GUIDE.md)** - Руководство по реализации с примерами кода
- **[Code Examples](development/CODE_EXAMPLES.md)** - Практические примеры кода
- **[Testing](development/TESTING.md)** - Стратегии тестирования и quality assurance
- **[Terminal Design](development/TERMINAL_DESIGN.md)** - Дизайн-система терминала: цвет, анимация, live-прогресс, клавиатура, деградация
- **[Terminal Design Roadmap](development/TERMINAL_DESIGN_ROADMAP.md)** - Integration map + PR-by-PR план внедрения (incl. English-by-default)
- **[Code Quality](development/CODE_QUALITY.md)** - Настройка pre-commit hooks и линтеров

### 📊 Планирование и roadmap

- **[Roadmap](planning/ROADMAP.md)** - Полная дорожная карта развития (MVP → LVL5)
- **[Mattermost Integration](planning/MATTERMOST_INTEGRATION.md)** - План интеграции с Mattermost
- **[Development Roadmap](planning/DEVELOPMENT_ROADMAP.md)** - План разработки по неделям

### 📖 Справочная информация

- **[Business Requirements (MVP)](reference/BRD.md)** - Бизнес-требования для MVP
- **[Business Requirements (Full)](reference/BRD_FULL.md)** - Полные бизнес-требования с roadmap
- **[KPI](reference/KPI.md)** - Ключевые показатели эффективности
- **[Quality Metrics](reference/QUALITY_METRICS.md)** - Метрики качества AI и система оценки
- **[Cost Management](reference/COST_MANAGEMENT.md)** — Лимиты токенов и контекста (Phase 0); целевой USD-контроль (Phase 1+, TD-006)
- **[API Documentation](reference/API.md)** - Документация API (в разработке)

### 🔧 Решение проблем

- **[Troubleshooting](troubleshooting/TROUBLESHOOTING.md)** - Общее руководство по решению проблем
- **[EWS Connection](troubleshooting/EWS_CONNECTION.md)** - Проблемы с подключением к Exchange

## 🎯 Основные возможности

### ✅ Текущие возможности (MVP)

- **EWS Integration** - Подключение к Exchange Web Services с NTLM аутентификацией
- **Privacy-First Design** - Обработка PII на стороне LLM Gateway API
- **Idempotent Processing** - Детерминированные результаты с T-48h окном пересборки
- **Dry-Run Mode** - Тестирование без вызовов LLM
- **Observability** - Prometheus метрики, health checks, структурированные логи
- **Schema Validation** - Строгая валидация Pydantic для всех выходов

### 🚧 Планируемые возможности

- **LVL2** - Обнаружение упоминаний пользователя («мои действия»)
- **LVL3** - Интеграция с публичными каналами Mattermost
- **LVL4** - Поддержка личных сообщений Mattermost (DM)
- **LVL5** - Mattermost-бот для интерактивной доставки

## 🏗️ Архитектура

```
EWS → normalize → thread → evidence split → context select
  → LLM Gateway (PII handling) → validate → assemble (JSON/MD)
  → metrics + logs
```

### Ключевые компоненты

- **CLI (Typer)** - Точка входа `run_digest()`
- **Ingest/EWS** - `exchangelib` NTLM, без autodiscover
- **Normalize** - HTML→текст, чистка цитат/подписей
- **Threads/Build** - Группировка по `conversation_id`
- **Evidence/Split** - Разбиение на фрагменты 256–512 токенов
- **Select/Context** - Эвристики отбора релевантных фрагментов
- **LLM/Gateway** - Вызов модели с промптами, валидация JSON
- **Assemble** - Сборка JSON/Markdown с evidence-ссылками
- **Observability** - Prometheus-экспортер, structlog

## 📊 Метрики и KPI

### Основные KPI

- **Покрытие значимых событий:** ≥ 90%
- **Точность Action Items:** ≥ 80%
- **Время генерации (T90):** ≤ 60/90 сек
- **SLA доставки бота:** ≥ 95%
- **Пользовательская оценка:** ≥ 4/5

### Технические метрики

- **EWS Connection Time:** ≤ 5 сек
- **LLM Response Time (P95):** ≤ 30 сек
- **Memory Usage:** ≤ 512 MB
- **Success Rate:** ≥ 99%

📊 **[Подробные KPI и метрики →](reference/KPI.md)**

## 🔒 Безопасность и приватность

### PII Policy

- **Обработка PII** выполняется на стороне LLM Gateway API
- **Тела сообщений** никогда не логируются
- **Секреты** хранятся только в переменных окружения

### Безопасность

- **Non-root Container** - Docker запускается от UID 1001
- **TLS Verification** - Проверка с корпоративным CA
- **Secret Management** - Credentials только через ENV
- **Audit Logging** - Структурированные логи всех операций

## 🛠️ Разработка

### Требования

- **Python 3.11+**
- **uv** (быстрый package manager)
- **Docker** (опционально)
- **Доступ к EWS endpoint**
- **LLM Gateway endpoint**

### Быстрый старт для разработчиков

```bash
# Клонировать репозиторий
git clone https://github.com/pogorelov-labs/ActionPulse.git
cd ActionPulse

# Установить зависимости
cd digest-core
make setup

# Запустить тесты
make test

# Запустить линтеры
make lint
```

👨‍💻 **[Руководство для разработчиков →](development/IMPLEMENTATION_GUIDE.md)**

## 📈 Roadmap

### Текущий статус: MVP ✅

- ✅ EWS интеграция
- ✅ LLM-powered извлечение действий
- ✅ Privacy-first дизайн
- ✅ Observability и метрики

### LVL2: Mentions Detection 🟡

- 🟡 Обнаружение упоминаний пользователя
- 🟡 Словарь алиасов и склонений
- 🟡 Метрики качества P/R/F1

### LVL3: Mattermost Integration 🟠

- 🔴 Публичные каналы Mattermost
- 🔴 Семантическое объединение тем
- 🔴 Дедупликация кросс-постов

### LVL4: DM Support 🔵

- 🔴 Личные сообщения Mattermost
- 🔴 Журнал согласий
- 🔴 Политика приватности DM

### LVL5: Mattermost Bot 🟣

- 🔴 Интерактивный бот
- 🔴 Команды и фильтры
- 🔴 Автоматическая доставка

🗺️ **[Полный roadmap →](planning/ROADMAP.md)**

## 🤝 Участие в разработке

### Как внести вклад

1. **Fork** репозиторий
2. **Создайте feature branch** (`git checkout -b feature/amazing-feature`)
3. **Commit** изменения (`git commit -m 'Add amazing feature'`)
4. **Push** в branch (`git push origin feature/amazing-feature`)
5. **Откройте Pull Request**

### Стандарты кода

- **Python 3.11+** с type hints
- **ruff + black + isort** для форматирования
- **mypy** для статической типизации
- **pytest** для тестирования
- **pre-commit hooks** для качества кода

📝 **[Руководство по участию →](../CONTRIBUTING.md)**

## 📞 Поддержка

### Получение помощи

- **📚 Документация** - Изучите соответствующие разделы
- **🔍 Troubleshooting** - Проверьте [руководство по решению проблем](troubleshooting/TROUBLESHOOTING.md)
- **🐛 Issues** - Создайте issue на GitHub с подробным описанием
- **💬 Discussions** - Используйте GitHub Discussions для вопросов

### Диагностика проблем

```bash
# Проверить конфигурацию
cd digest-core && make env-check

# Запустить диагностику
./digest-core/scripts/print_env.sh

# Проверить подключения
python -m digest_core.cli run --dry-run --verbose
```

## 📄 Лицензия

Internal corporate use only. Proprietary and confidential.

---

**ActionPulse** - делаем корпоративные коммуникации более управляемыми и эффективными! 🚀
