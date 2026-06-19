"""Report language layer: canonical section keys + every report-bound string.

The digest's section identity is a *canonical key*, never a display string.
The LLM emits titles in the configured report language; ``normalize_section``
maps any known title (both languages, case-insensitive, historical variants)
back to its key, and rendering always re-derives the displayed title from the
key — so output language is deterministic even if the model disobeys, and
Russian fixtures/recordings keep working through normalization.

House rule (TERMINAL_DESIGN_ROADMAP L1): report-bound strings live here and
only here. English is the default; ``report.language: ru`` switches reports
to Russian (user setting in configs/config.yaml, asked by the setup wizard).
"""

from __future__ import annotations

from typing import Optional

DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "ru")

# Canonical section keys, in digest order.
MY_ACTIONS = "my_actions"
URGENT = "urgent"
FYI = "fyi"
STATUS = "status"
UNCONFIRMED = "unconfirmed"
# Store-derived cross-day carryover (P3 memory pillar): owner-addressed messages
# from earlier days whose thread has gone quiet → likely still waiting on you.
OPEN_LOOPS = "open_loops"
# Store-derived pending requests (P3): a message from an earlier day that asked
# YOU something (question / approval / request) and you have not replied since.
PENDING = "pending"

SECTION_TITLES: dict[str, dict[str, str]] = {
    "en": {
        MY_ACTIONS: "My actions",
        URGENT: "Urgent",
        FYI: "FYI",
        STATUS: "Status",
        UNCONFIRMED: "Unconfirmed",
        OPEN_LOOPS: "Open loops",
        PENDING: "Awaiting your reply",
    },
    "ru": {
        MY_ACTIONS: "Мои действия",
        URGENT: "Срочное",
        FYI: "К сведению",
        STATUS: "Статус",
        UNCONFIRMED: "Не подтверждено",
        OPEN_LOOPS: "Открытые вопросы",
        PENDING: "Ждут вашего ответа",
    },
}

# Sort weights by key (unknown sections sort last, preserving prior behavior).
# Urgent leads so time-critical items surface first, then My actions, the
# cross-day Awaiting-your-reply asks, the softer Open loops, FYI, and the
# Unconfirmed quarantine stays last.
SECTION_ORDER_BY_KEY: dict[str, int] = {
    URGENT: 0,
    MY_ACTIONS: 1,
    PENDING: 2,
    OPEN_LOOPS: 3,
    FYI: 4,
    UNCONFIRMED: 5,
}

_TITLE_TO_KEY: dict[str, str] = {}
for _lang_titles in SECTION_TITLES.values():
    for _key, _title in _lang_titles.items():
        _TITLE_TO_KEY[_title.lower()] = _key
# Historical/variant spellings seen in output and fixtures.
_TITLE_TO_KEY["к сведению (fyi)"] = FYI
_TITLE_TO_KEY["for your information"] = FYI


def normalize_section(title: str) -> Optional[str]:
    """Canonical key for a section title in any supported language, else None."""
    if not title:
        return None
    return _TITLE_TO_KEY.get(title.strip().lower())


def section_title(key: str, language: str) -> str:
    """Display title for a canonical key; falls back to English, then the key."""
    titles = SECTION_TITLES.get(language, SECTION_TITLES[DEFAULT_LANGUAGE])
    return titles.get(key, SECTION_TITLES[DEFAULT_LANGUAGE].get(key, key))


def section_sort_weight(title: str) -> int:
    """Sort weight for a section by its (any-language) title; unknown -> 99."""
    key = normalize_section(title)
    return SECTION_ORDER_BY_KEY.get(key, 99) if key else 99


def display_title(title: str, language: str) -> str:
    """Re-render a section title in the configured language; unknown passes through."""
    key = normalize_section(title)
    return section_title(key, language) if key else title


# ---------------------------------------------------------------------------
# Report strings (markdown + mattermost + degrade banners)
# ---------------------------------------------------------------------------

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "digest_header": "Action digest",
        "digest_part_header": "Action digest — part {index}/{total}",
        "no_actions": "No relevant actions found for the period.",
        "due_label": "Due",
        "confidence_label": "Confidence",
        "source_label": "Source",
        "statistics_header": "Statistics",
        "sources_header": "Sources",
        "processed_summary": "Processed {total} emails, {with_actions} ({percent}%) contained actions",
        "found_actions": "Found {total} actions:",
        "and_more_items": "*... and {remaining} more items*",
        "truncated_note": "*[Content truncated to respect the word limit]*",
        "weak_basis": "weak evidence",
        "repaired": "repaired",
        "repeat": "repeat",
        "partial_json_title": "PARTIAL REPORT: LLM returned invalid JSON",
        "partial_json_body": (
            "This digest was produced in fallback mode (extractive) because the"
            " LLM response failed JSON parsing."
        ),
        "partial_llm_title": "PARTIAL REPORT: LLM processing error",
        "partial_llm_body": (
            "This digest was produced in fallback mode (extractive) because the LLM call failed."
        ),
        "partial_note": "The information may be less complete or precise than usual.",
        "confidence_very_high": "very high",
        "confidence_high": "high",
        "confidence_medium": "medium",
        "confidence_low": "low",
        "confidence_very_low": "very low",
        "banner_threads": "Email grouping failed. The digest is incomplete.",
        "banner_evidence": "Evidence preparation failed. The digest is incomplete.",
        "banner_select": "Context selection failed. The digest is incomplete.",
        "banner_pipeline": "Pipeline failure. The digest is incomplete.",
        "mm_ping_text": 'ActionPulse: incoming webhook check (mm-ping). Custom text: `mm-ping --message "..."`.',
        "description_label": "Description",
        "date_iso_label": "Date (ISO)",
        "category_label": "Category",
        "quote_label": "Quote",
        "banner_llm_auth": "LLM Gateway rejected the token (401/403): refresh LLM_TOKEN. The digest is incomplete.",
        "banner_llm_unavailable": "LLM Gateway is unavailable. The digest is incomplete.",
        "banner_llm_timeout": "LLM Gateway timed out. The digest is incomplete.",
        "owners_label": "Owners",
        "response_channel_label": "Response channel",
        "datetime_label": "Date/time",
        "location_label": "Location",
        "participants_label": "Participants",
        "severity_label": "Severity",
        "impact_label": "Impact",
        "subject_word": "subject",
        "enhanced_others_header": "Others' actions",
        "enhanced_deadlines_header": "Deadlines and meetings",
        "enhanced_risks_header": "Risks and blockers",
        "partial_generic_title": "PARTIAL REPORT",
        "partial_generic_body": "This digest was produced in fallback mode (extractive).",
        "carryover_item": 'Awaiting you {days}d — "{subject}"',
        "pending_item": 'Reply needed {days}d — "{subject}"',
    },
    "ru": {
        "digest_header": "Дайджест действий",
        "digest_part_header": "Дайджест действий — часть {index}/{total}",
        "no_actions": "За период релевантных действий не найдено.",
        "due_label": "Срок",
        "confidence_label": "Уверенность",
        "source_label": "Источник",
        "statistics_header": "Статистика",
        "sources_header": "Источники",
        "processed_summary": "Обработано {total} писем, {with_actions} ({percent}%) содержали действия",
        "found_actions": "Найдено {total} действий:",
        "and_more_items": "*... и еще {remaining} элементов*",
        "truncated_note": "*[Содержимое обрезано для соблюдения лимита слов]*",
        "weak_basis": "слабое обоснование",
        "repaired": "повтор",
        "repeat": "повтор",
        "partial_json_title": "ЧАСТИЧНЫЙ ОТЧЁТ: LLM дал невалидный JSON",
        "partial_json_body": (
            "Данный дайджест создан в резервном режиме (extractive fallback)"
            " из-за ошибки парсинга JSON от LLM."
        ),
        "partial_llm_title": "ЧАСТИЧНЫЙ ОТЧЁТ: Ошибка обработки LLM",
        "partial_llm_body": (
            "Данный дайджест создан в резервном режиме (extractive fallback) из-за сбоя LLM."
        ),
        "partial_note": "Информация может быть неполной или менее точной, чем обычно.",
        "confidence_very_high": "очень высокая",
        "confidence_high": "высокая",
        "confidence_medium": "средняя",
        "confidence_low": "низкая",
        "confidence_very_low": "очень низкая",
        "banner_threads": "Сбой при группировке писем. Дайджест неполный.",
        "banner_evidence": "Сбой при подготовке доказательств. Дайджест неполный.",
        "banner_select": "Сбой при отборе контекста. Дайджест неполный.",
        "banner_pipeline": "Сбой пайплайна. Дайджест неполный.",
        "mm_ping_text": 'ActionPulse: проверка incoming webhook (mm-ping). Свой текст: `mm-ping --message "..."`.',
        "description_label": "Описание",
        "date_iso_label": "Дата (ISO)",
        "category_label": "Категория",
        "quote_label": "Цитата",
        "banner_llm_auth": "LLM Gateway отклонил токен (401/403): обновите LLM_TOKEN. Дайджест неполный.",
        "banner_llm_unavailable": "LLM Gateway недоступен. Дайджест неполный.",
        "banner_llm_timeout": "LLM Gateway превысил таймаут. Дайджест неполный.",
        "owners_label": "Ответственные",
        "response_channel_label": "Канал ответа",
        "datetime_label": "Дата/время",
        "location_label": "Место",
        "participants_label": "Участники",
        "severity_label": "Серьёзность",
        "impact_label": "Влияние",
        "subject_word": "тема",
        "enhanced_others_header": "Действия других",
        "enhanced_deadlines_header": "Дедлайны и встречи",
        "enhanced_risks_header": "Риски и блокеры",
        "partial_generic_title": "ЧАСТИЧНЫЙ ОТЧЁТ",
        "partial_generic_body": "Данный дайджест создан в резервном режиме (extractive fallback).",
        "carryover_item": "Ожидает вас {days}д — «{subject}»",
        "pending_item": "Нужен ответ {days}д — «{subject}»",
    },
}


def report_strings(language: str) -> dict[str, str]:
    """Full string table for a report language; unknown languages fall back to EN."""
    return STRINGS.get(language, STRINGS[DEFAULT_LANGUAGE])


def stage_banner(stage: str, language: str) -> str:
    strings = report_strings(language)
    return strings.get(f"banner_{stage}", strings["banner_pipeline"])


# Show the confidence label only when it adds signal: borderline items below
# this threshold (the «средняя» band; the extraction prompt already drops <0.5,
# so in practice this surfaces only 0.5–0.69 items). At or above it (high / very
# high), the label is noise and is suppressed.
CONFIDENCE_DISPLAY_MAX = 0.7


def should_show_confidence(confidence: float, weak_evidence: bool = False) -> bool:
    """Whether a renderer should print the confidence label for an item.

    Shown only for borderline items (``confidence < CONFIDENCE_DISPLAY_MAX``) and
    never when ``weak_evidence`` is set — the «⚠ слабое обоснование» marker is the
    signal there, and the two must not co-appear and contradict each other.
    """
    if weak_evidence:
        return False
    return confidence < CONFIDENCE_DISPLAY_MAX


def confidence_text(confidence: float, language: str) -> str:
    """Confidence word; thresholds mirror the pre-L1 behavior exactly."""
    strings = report_strings(language)
    if confidence >= 0.9:
        return strings["confidence_very_high"]
    if confidence >= 0.7:
        return strings["confidence_high"]
    if confidence >= 0.5:
        return strings["confidence_medium"]
    if confidence >= 0.3:
        return strings["confidence_low"]
    return strings["confidence_very_low"]
