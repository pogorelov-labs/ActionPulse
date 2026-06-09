"""
Signal extraction utilities for evidence chunks.
"""

import re as _stdre
from datetime import datetime
from typing import List
import pytz

# Try to use external regex module with safe Unicode properties
try:
    import regex

    _HAS_REGEX = True
except ImportError:
    regex = None
    _HAS_REGEX = False


# Safe Cyrillic word pattern with fallback
def _get_cyrillic_word_pattern():
    """Get compiled Cyrillic word pattern with safe implementation."""
    if _HAS_REGEX:
        # Use Unicode property - safe, no bad character ranges
        _INWORD_SEP = r"[\-'']"
        pattern = rf"(?:\p{{IsCyrillic}}+(?:{_INWORD_SEP}\p{{IsCyrillic}}+)*)"
        try:
            return regex.compile(pattern, flags=regex.UNICODE | regex.IGNORECASE)
        except Exception:
            pass  # Fall through to stdlib fallback

    # Fallback: stdlib re with explicit Unicode range (no hyphen-as-range)
    # U+0400-U+04FF covers Cyrillic block, including Ё/ё
    pattern = r"(?:[\u0400-\u04FF]+(?:[\-''][\u0400-\u04FF]+)*)"
    return _stdre.compile(pattern, flags=_stdre.UNICODE | _stdre.IGNORECASE)


# Compiled pattern (initialized once)
CYRILLIC_WORD = _get_cyrillic_word_pattern()


# Action verbs in Russian and English
ACTION_VERBS_RU = [
    # Requests
    "пожалуйста",
    "прошу",
    "просьба",
    "можете",
    "могли бы",
    "не могли бы",
    # Requirements
    "нужно",
    "требуется",
    "необходимо",
    "должны",
    "обязательно",
    # Approvals
    "одобрить",
    "одобрите",
    "согласовать",
    "согласуйте",
    "утвердить",
    "утвердите",
    # Verifications
    "проверить",
    "проверьте",
    "подтвердить",
    "подтвердите",
    # Completions
    "завершить",
    "завершите",
    "выполнить",
    "выполните",
    "сделать",
    "сделайте",
    # Preparations
    "подготовить",
    "подготовьте",
    "доработать",
    "доработайте",
    # Responses
    "ответить",
    "ответьте",
    "уточнить",
    "уточните",
    # Urgency
    "срочно",
    "срок",
    "дедлайн",
    "не позднее",
    # Updates
    "обновить",
    "обновите",
    "актуализировать",
    "актуализируйте",
]

ACTION_VERBS_EN = [
    "please",
    "need",
    "required",
    "necessary",
    "approve",
    "review",
    "complete",
    "finish",
    "prepare",
    "urgent",
    "deadline",
    "due",
    "asap",
    "request",
    "could you",
    "can you",
    "would you",
    "update",
    "confirm",
    "verify",
    "respond",
    "reply",
]

ALL_ACTION_VERBS = ACTION_VERBS_RU + ACTION_VERBS_EN


def extract_action_verbs(text: str) -> List[str]:
    """
    Extract action verbs from text (both Russian and English).

    Args:
        text: Text to analyze

    Returns:
        List of found action verbs
    """
    if not text:
        return []

    text_lower = text.lower()
    found_verbs = []

    for verb in ALL_ACTION_VERBS:
        if _stdre.search(rf"\b{_stdre.escape(verb)}\b", text_lower):
            if verb not in found_verbs:
                found_verbs.append(verb)

    return found_verbs


def extract_dates(text: str) -> List[str]:
    """
    Extract dates from text in various formats.

    Supported formats:
    - DD/MM/YYYY, DD.MM.YYYY
    - YYYY-MM-DD
    - Relative: today, tomorrow, yesterday (RU/EN)
    - Russian date deadlines: "до 15 января", "к 3 марта", "не позднее 20 декабря"

    Args:
        text: Text to analyze

    Returns:
        List of found date strings
    """
    if not text:
        return []

    found_dates = []

    # Pattern 1: DD/MM/YYYY or DD.MM.YYYY
    date_pattern_1 = r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"
    matches_1 = _stdre.findall(date_pattern_1, text)
    found_dates.extend(matches_1)

    # Pattern 2: YYYY-MM-DD
    date_pattern_2 = r"\b\d{4}-\d{2}-\d{2}\b"
    matches_2 = _stdre.findall(date_pattern_2, text)
    found_dates.extend(matches_2)

    # Pattern 3: Russian date deadlines "до/к/не позднее [число] [месяц]"
    # Safe implementation without hyphen-as-range in character classes
    if _HAS_REGEX:
        # Use regex module for safer Unicode handling
        ru_date_pattern = regex.compile(
            r"\b(до|к|не позднее)\s+(\d{1,2})\s+(январ[ья]|феврал[ья]|марта|апрел[ья]|ма[яй]|"
            r"июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\b",
            regex.IGNORECASE | regex.UNICODE,
        )
    else:
        # Stdlib re fallback: explicit alternatives instead of range
        ru_date_pattern = _stdre.compile(
            r"\b(до|к|не позднее)\s+(\d{1,2})\s+(январ[ья]|феврал[ья]|марта|апрел[ья]|ма[яй]|"
            r"июн[ья]|июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\b",
            _stdre.IGNORECASE | _stdre.UNICODE,
        )
    ru_date_matches = ru_date_pattern.findall(text)
    for match in ru_date_matches:
        # match is tuple: (prefix, day, month)
        date_str = f"{match[0]} {match[1]} {match[2]}"
        if date_str not in found_dates:
            found_dates.append(date_str)

    # Pattern 4: Relative dates
    relative_dates_ru = ["сегодня", "завтра", "вчера", "послезавтра"]
    relative_dates_en = ["today", "tomorrow", "yesterday"]
    all_relative = relative_dates_ru + relative_dates_en

    text_lower = text.lower()
    for rel_date in all_relative:
        if rel_date in text_lower:
            if rel_date not in found_dates:
                found_dates.append(rel_date)

    return found_dates


def contains_question(text: str) -> bool:
    """
    Check if text contains a question mark.

    Args:
        text: Text to analyze

    Returns:
        True if text contains "?"
    """
    if not text:
        return False

    return "?" in text


def normalize_datetime_to_tz(dt: datetime, tz_name: str) -> str:
    """
    Convert datetime to ISO-8601 format with specified timezone.

    Args:
        dt: Datetime object (should have timezone info)
        tz_name: Timezone name (e.g., "Europe/Moscow", "America/Sao_Paulo")

    Returns:
        ISO-8601 formatted datetime string with timezone
    """
    try:
        # Get target timezone
        target_tz = pytz.timezone(tz_name)

        # Convert datetime to target timezone
        if dt.tzinfo is None:
            # Assume UTC if no timezone
            dt = pytz.utc.localize(dt)

        dt_in_tz = dt.astimezone(target_tz)

        # Return ISO-8601 format
        return dt_in_tz.isoformat()

    except Exception:
        # Fallback to UTC ISO format
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.utc)
        return dt.isoformat()


def calculate_sender_rank(sender_email: str, important_senders=None) -> int:
    """
    Calculate sender rank.

    Args:
        sender_email: Sender email address
        important_senders: optional list of important addresses or substrings
            (e.g. "ceo@", "example.com"). A match promotes the sender to rank 2,
            which makes the `critical_senders` selection bucket live (PR9).

    Returns:
        2 if the sender matches an important_senders pattern, else 1 (internal).
        With no important_senders configured this is the legacy constant 1.
    """
    if important_senders:
        sender = (sender_email or "").lower()
        if any(pattern and pattern.lower() in sender for pattern in important_senders):
            return 2
    return 1
