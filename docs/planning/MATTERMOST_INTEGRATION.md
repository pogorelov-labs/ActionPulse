# Mattermost Integration Plan

> ## ⚠️ SUPERSEDED — historical planning doc (its "not implemented" list is now wrong)
>
> Live Mattermost source-of-truth:
> [`MATTERMOST_INTEGRATION_DESIGN.md`](../../digest-core/docs/research/MATTERMOST_INTEGRATION_DESIGN.md).
> Current state + forward plan: [`ROADMAP.md`](./ROADMAP.md).
>
> **What actually shipped** (this doc still calls it "🔴 not implemented"): the MM **read
> side** — owner @-mentions (#133), allowlisted **channels** (#136), **consent-gated DMs**
> (#138); plus **api-mode delivery** to an owner-only private channel (#137) with a
> per-user slug (#139). Still open: an interactive **bot / slash commands** and **reaction
> harvest** — see ROADMAP §4 (Phase 2 flywheel, Phase 4 reach).
>
> The LVL-by-LVL prose below is the original desired state, kept for history only.

Детальный план интеграции ActionPulse с Mattermost для расширения источников данных и создания интерактивного бота.

## Обзор интеграции

### Цели интеграции (Phase 1+, 🔴 not implemented)

1. **LVL3** - Подключение публичных каналов Mattermost
2. **LVL4** - Добавление личных сообщений (DM) с соблюдением приватности
3. **LVL5** - Создание интерактивного бота для доставки дайджестов

### Архитектура интеграции

```
Mattermost API → ingest → normalize → thread → evidence split → context select
  → LLM Gateway → validate → assemble (JSON/MD) → deliver (MM Bot)
```

## LVL3 - Публичные каналы Mattermost · 🔴 Not implemented

### Бизнес-смысл

Объединить почту и публичные каналы MM с явным указанием происхождения каждого пункта.

### Функциональность

#### Источники данных

- **Exchange** + **Mattermost (public channels)**
- Каноникализация ID: `urn:mm:{team}/{channel}/{postId}`
- Поддержка множественных команд и каналов

#### Метки источника

Каждый пункт дайджеста содержит явные метки происхождения:

```
[source: email | subject:"Budget Q4"]
[source: mm-public | channel:#project-a | permalink]
[source: mm-public | channel:#general | team:engineering]
```

#### Приложение "Источники и ссылки"

Сводный список включённых каналов/тредов с прямыми ссылками:

```markdown
## Источники и ссылки

### Email
- Письмо "Q3 Budget plan" (2024-01-15 10:30)
- Письмо "SLA incident update" (2024-01-15 14:20)

### Mattermost
- #project-a: Обсуждение архитектуры (5 сообщений)
- #general: Анонс митинга (2 сообщения)
- engineering/backend: Техническая дискуссия (8 сообщений)
```

#### Семантическое объединение

- Кластеры тем с «шапкой» (top-n keyphrases)
- Дедупликация кросс-постов/пересылок (sha1/MinHash + canonical URL)
- Приоритизация по релевантности и свежести

### Техническая реализация

#### Mattermost API Client

```python
class MattermostClient:
    def __init__(self, base_url: str, token: str, team_id: str):
        self.base_url = base_url
        self.token = token
        self.team_id = team_id
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        })
    
    def get_public_channels(self) -> List[Channel]:
        """Получить список публичных каналов"""
        pass
    
    def get_channel_posts(self, channel_id: str, since: datetime) -> List[Post]:
        """Получить сообщения канала за период"""
        pass
```

#### Конфигурация каналов

```yaml
mattermost:
  enabled: true
  base_url: "https://mm.corp.com"
  token_env: "MM_TOKEN"
  team_id: "engineering"
  channels:
    include:
      - "#general"
      - "#project-a"
      - "#backend"
    exclude:
      - "#random"
      - "#off-topic"
  lookback_hours: 24
  max_posts_per_channel: 100
```

### DoD для LVL3

- ✅ У каждого пункта есть `source` + `canonical_url`/`permalink`
- ✅ В отчёт не попадают приватные чаты; neg-лист каналов применён и залогирован
- ✅ При тесте 3–5 каналов/50 писем — T90 ≤ 90 сек
- ✅ Дедуп кросс-постов/пересылок (sha1/MinHash + canonical URL) включён

## LVL4 - Личные сообщения (DM) · 🔴 Not implemented

### Бизнес-смысл

Личный, полнота «моих действий»: DM-события приоритетно попадают в блок _My Actions_.

### Доступ и приватность

#### Журнал согласий

```json
{
  "consent_id": "uuid-v4",
  "user_id": "user@corp.com",
  "obtained_at": "2024-01-15T10:00:00Z",
  "scope": ["read-dm", "read-public"],
  "revoked_at": null,
  "expires_at": "2024-12-31T23:59:59Z"
}
```

#### Токены минимальных прав

- **Scope:** `read-dm` (только личные сообщения)
- **Возможность отключения:** настройки пользователя
- **Ротация токенов:** автоматическая каждые 90 дней

### Функциональность и ИБ

#### Пометка приватности

```
[source: mm-dm | chat:@username | privacy:private]
```

#### Минимизация цитат

- Символный лимит: ≤ 200 символов на цитату
- Маскирование PII: телефоны, аккаунты, e-mail
- Исключение из поиска по содержимому

#### Ретеншн политика

- **Хранение:** ≤ 7 дней (конфигурируемо)
- **Процедура удаления:** по запросу субъекта в течение 24 часов
- **Аудит доступа:** логирование всех обращений к DM данным

### Техническая реализация

#### DM Client

```python
class MattermostDMClient:
    def __init__(self, base_url: str, token: str, user_id: str):
        self.base_url = base_url
        self.token = token
        self.user_id = user_id
        self.consent_manager = ConsentManager()
    
    def get_dm_conversations(self, since: datetime) -> List[DMConversation]:
        """Получить DM разговоры за период"""
        if not self.consent_manager.has_consent(self.user_id, "read-dm"):
            raise ConsentRequired("DM access requires explicit consent")
        
        # Реализация получения DM
        pass
    
    def process_dm_message(self, message: DMMessage) -> ProcessedMessage:
        """Обработать DM сообщение с маскированием PII"""
        # Минимизация цитат
        # Маскирование PII
        # Приоритизация для "My Actions"
        pass
```

### DoD для LVL4

- ✅ Согласие зафиксировано (IB-аудит); при отзыве — источник отключён в течение 24 ч
- ✅ DM-пункты помечены `privacy=private`; проверка отсутствия ПДн сверх допустимого
- ✅ Тест «DM-утечка»: пересылка не раскрывает приватные каналы адресатам без доступа

## LVL5 - Mattermost-бот · 🔴 Not implemented

### Бизнес-смысл

Автодоставка дайджеста в личку; интерактивные запросы и детализация.

### Функциональность

#### Плановое формирование

- **Расписание:** cron-based (ежедневно в 8:00)
- **Доставка:** Markdown-дайджест в DM пользователя
- **Формат:** структурированный с интерактивными элементами

#### Команды и фильтры

```bash
# Базовые команды
/digest today                    # Дайджест за сегодня
/digest yesterday               # Дайджест за вчера
/digest week                    # Дайджест за неделю

# Фильтры
/digest details #project-a      # Детали по каналу
/digest since:2024-01-10        # С конкретной даты
/digest only:actions            # Только действия
/digest lang:ru                 # Только русский контент

# Настройки
/digest settings                # Настройки пользователя
/digest help                    # Справка по командам
```

#### История и пересылка

- **Хранение:** 7 дней в зашифрованном виде
- **Пересылка:** с ACL/аудитом
- **Экспорт:** JSON/Markdown по запросу

### Техническая реализация

#### Bot Framework

```python
class MattermostBot:
    def __init__(self, webhook_url: str, digest_service: DigestService):
        self.webhook_url = webhook_url
        self.digest_service = digest_service
        self.command_handlers = {
            'digest': self.handle_digest_command,
            'settings': self.handle_settings_command,
            'help': self.handle_help_command
        }
    
    async def handle_digest_command(self, user_id: str, args: List[str]) -> BotResponse:
        """Обработать команду /digest"""
        # Парсинг аргументов
        # Генерация дайджеста
        # Форматирование ответа
        pass
    
    async def send_daily_digest(self, user_id: str) -> bool:
        """Отправить ежедневный дайджест"""
        digest = await self.digest_service.generate_digest(user_id, "today")
        return await self.send_message(user_id, digest.to_markdown())
```

#### Webhook Handler

```python
@app.post("/webhook/mattermost")
async def handle_mattermost_webhook(request: MattermostWebhook):
    """Обработать webhook от Mattermost"""
    if request.type == "slash_command":
        return await bot.handle_slash_command(request)
    elif request.type == "interactive_button":
        return await bot.handle_button_click(request)
```

### Качество и SLO

#### SLA доставки

- **Цель:** ≥ 95% в окно расписания (±5 мин)
- **Мониторинг:** метрики доставки, таймауты, ошибки
- **Алерты:** при падении SLA ниже 90%

#### Производительность

- **Ответ на команду:** ≤ 5 сек
- **Сплит длинных отчётов:** на серию сообщений (≤ 4000 символов)
- **Rate-limit:** 10 команд/минуту на пользователя

#### Idempotent delivery

- **Message-key:** `digest-{user_id}-{date}-{hash}`
- **Retry с jitter:** экспоненциальный backoff
- **Дедупликация:** предотвращение дублирования доставки

### DoD для LVL5

- ✅ Ежедневная доставка подтверждается логами «сформировано/доставлено»
- ✅ Команды возвращают корректные фильтрованные данные
- ✅ Логируются: длительности стадий, размер отчёта, источники, статус доставки

## Конфигурация интеграции

### Полная конфигурация

```yaml
mattermost:
  enabled: true
  base_url: "https://mm.corp.com"
  token_env: "MM_TOKEN"
  team_id: "engineering"
  
  # Публичные каналы (LVL3)
  public_channels:
    enabled: true
    include:
      - "#general"
      - "#project-a"
      - "#backend"
      - "#devops"
    exclude:
      - "#random"
      - "#off-topic"
    lookback_hours: 24
    max_posts_per_channel: 100
  
  # Личные сообщения (LVL4)
  dm:
    enabled: false  # Требует явного согласия
    consent_required: true
    retention_days: 7
    max_quote_length: 200
    pii_masking: true
  
  # Бот (LVL5)
  bot:
    enabled: false
    webhook_url: "https://digest.corp.com/webhook/mattermost"
    daily_schedule: "0 8 * * *"  # 8:00 каждый день
    commands:
      - "digest"
      - "settings"
      - "help"
    rate_limit: 10  # команд/минуту
    max_message_length: 4000
```

## Безопасность и соответствие

### Управление согласиями

```python
class ConsentManager:
    def grant_consent(self, user_id: str, scope: List[str]) -> ConsentRecord:
        """Предоставить согласие на доступ к данным"""
        pass
    
    def revoke_consent(self, user_id: str, consent_id: str) -> bool:
        """Отозвать согласие"""
        pass
    
    def has_consent(self, user_id: str, scope: str) -> bool:
        """Проверить наличие согласия"""
        pass
    
    def cleanup_expired_data(self, user_id: str) -> int:
        """Очистить истёкшие данные пользователя"""
        pass
```

### Аудит и логирование

- **Доступ к DM:** все обращения логируются
- **Команды бота:** история команд с временными метками
- **Пересылка сообщений:** ACL проверки и аудит
- **Ошибки доступа:** детальное логирование для расследования

### Соответствие требованиям

- **GDPR/CCPA:** право на удаление, портабельность данных
- **Корпоративная политика:** соответствие внутренним стандартам ИБ
- **Аудит:** регулярные проверки доступа и использования данных

## Мониторинг и метрики

### Метрики интеграции

```prometheus
# Mattermost API метрики
mm_api_requests_total{status, endpoint}
mm_api_latency_seconds{endpoint}
mm_api_errors_total{error_type}

# Каналы и сообщения
mm_channels_processed_total{team, channel}
mm_posts_processed_total{channel, type}
mm_dm_messages_processed_total{user_id}

# Бот метрики
mm_bot_commands_total{command, user_id}
mm_bot_delivery_success_total{user_id}
mm_bot_response_time_seconds{command}

# Согласия и приватность
mm_consents_granted_total{scope}
mm_consents_revoked_total{scope}
mm_data_cleanup_total{user_id}
```

### Алерты

- **SLA доставки < 95%** - критический алерт
- **Ошибки API > 5%** - предупреждение
- **Отзыв согласий > 10/день** - уведомление
- **Превышение rate-limit** - мониторинг

## План внедрения

### Этап 1: LVL3 (Публичные каналы)
- **Неделя 1-2:** Разработка Mattermost API клиента
- **Неделя 3:** Интеграция с основным пайплайном
- **Неделя 4:** Тестирование и отладка

### Этап 2: LVL4 (DM)
- **Неделя 5-6:** Система управления согласиями
- **Неделя 7:** DM клиент и обработка приватных данных
- **Неделя 8:** Тестирование приватности и безопасности

### Этап 3: LVL5 (Бот)
- **Неделя 9-10:** Bot framework и команды
- **Неделя 11:** Webhook обработка и доставка
- **Неделя 12:** Мониторинг, алерты и финальное тестирование

## Риски и митигации

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Ограничения API Mattermost | Средняя | Высокое | Graceful degradation, кэширование |
| Проблемы с согласиями | Низкая | Критическое | Юридическая экспертиза, аудит |
| Перегрузка бота | Средняя | Среднее | Rate limiting, горизонтальное масштабирование |
| Утечки приватных данных | Низкая | Критическое | Тестирование безопасности, аудит кода |

---

**Итог:** План интеграции с Mattermost обеспечивает поэтапное расширение функциональности с соблюдением принципов приватности и безопасности. Каждый уровень имеет четкие критерии приёмки и метрики качества.
