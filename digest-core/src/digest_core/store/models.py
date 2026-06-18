"""Projection from the in-memory ``NormalizedMessage`` to a stored row.

Owns the stable URN id scheme and the content hash that drives idempotent upsert
(same family as run.py's PR1 idempotency: id|subject|normalized-body).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def build_urn(source: str, msg_id: str) -> str:
    """Stable URN id for a message (BR v3.0 ``urn:email:..`` / ``urn:mm:..``).

    EWS: ``urn:email:<InternetMessageId>``. Mattermost: ``urn:mm:<postId>`` —
    the MM ``msg_id`` is ``mm:<postId>``; the fuller ``urn:mm:<team>/<channel>/..``
    form is a later enrichment (team/channel are not on ``NormalizedMessage``),
    and ``postId`` alone is already globally unique.
    """
    s = (source or "email").lower()
    mid = msg_id or ""
    if s == "mm":
        post_id = mid.split("mm:", 1)[-1] if mid.startswith("mm:") else mid
        return f"urn:mm:{post_id}"
    return f"urn:email:{mid}"


def content_hash(urn: str, subject: str, body_normalized: str) -> str:
    """Deterministic per-message content hash (changed-vs-unchanged guard)."""
    payload = "\x01".join((urn, subject or "", body_normalized or ""))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _to_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def message_to_row(
    msg: Any,
    *,
    schema_version: int,
    pipeline_version: str = "",
    raw_body: Optional[str] = None,
) -> Dict[str, Any]:
    """Map a ``NormalizedMessage`` to a ``messages`` row dict (sans timestamps).

    ``body_normalized`` is the cleaned text (``body_norm``); ``body_raw`` is the
    pre-normalize body when provided by the caller (the pipeline hook captures it
    before NORMALIZE overwrites it), else it falls back to the normalized body.
    """
    urn = build_urn(getattr(msg, "source", "email"), msg.msg_id)
    body_norm = getattr(msg, "body_norm", "") or getattr(msg, "text_body", "") or ""
    body_raw = raw_body if raw_body is not None else body_norm
    received = _to_utc(getattr(msg, "datetime_received", None))
    return {
        "id": urn,
        "source": getattr(msg, "source", "email") or "email",
        "canonical_url": None,
        "thread_id": getattr(msg, "conversation_id", None),
        "parent_id": None,
        "received_at": received.isoformat(),
        "received_epoch": int(received.timestamp()),
        "author_display": getattr(msg, "from_name", None),
        "author_email": getattr(msg, "from_email", "") or getattr(msg, "sender_email", ""),
        "author_role": None,
        "subject": getattr(msg, "subject", "") or "",
        "body_raw": body_raw,
        "body_normalized": body_norm,
        "content_hash": content_hash(urn, getattr(msg, "subject", "") or "", body_norm),
        "lang": None,
        "importance": getattr(msg, "importance", "Normal") or "Normal",
        "is_flagged": 1 if getattr(msg, "is_flagged", False) else 0,
        "has_attachments": 1 if getattr(msg, "has_attachments", False) else 0,
        "attachment_types": json.dumps(list(getattr(msg, "attachment_types", []) or [])),
        "to_recipients": json.dumps(list(getattr(msg, "to_recipients", []) or [])),
        "cc_recipients": json.dumps(list(getattr(msg, "cc_recipients", []) or [])),
        "risk_level": None,
        "pipeline_version": pipeline_version,
        "schema_version": schema_version,
    }
