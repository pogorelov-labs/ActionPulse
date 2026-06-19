"""``--dump-ingest`` / ``--replay-ingest`` snapshot (de)serialization for NormalizedMessage.

A dev-only on-disk JSON snapshot of a run's normalized ingest, so the LLM/assemble stages can
be exercised offline ("code outside, run inside, debug outside", ADR-012). DM bodies are
redacted at rest (guardrail #9, fail-closed) — a replayed dump carries no DM content.

Extracted from run.py (the god-module split); run.py re-exports these under their historical
``_``-prefixed names for backward-compatible imports.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from digest_core.ingest.ews import NormalizedMessage
from digest_core.store.models import redact_mm_body_at_rest

#: Marker written in place of a DM body in a snapshot (guardrail #9).
DM_AT_REST_REDACTION = "[DM content redacted at rest]"


def serialize_message(message: NormalizedMessage) -> Dict[str, Any]:
    """A NormalizedMessage as a JSON-able dict, with DM bodies redacted at rest."""
    payload = asdict(message)
    for key in ("datetime_received", "received_at"):
        value = payload.get(key)
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    # Privacy boundary (design §6, guardrail #9): never persist raw DM text at rest. The same
    # fail-closed predicate as the store — only Mattermost OPEN ('O') / PRIVATE ('P') channel
    # posts keep their body; DMs ('D'/'G') AND any unknown/missing channel type are redacted, so
    # the type being indeterminate (e.g. lost in a rebuild) can't leak a body. Only the on-disk
    # snapshot is touched (the live in-memory run is unaffected) → a replayed dump has no DM text.
    if redact_mm_body_at_rest(payload.get("source"), payload.get("mm_channel_type")):
        payload["text_body"] = DM_AT_REST_REDACTION
        payload["body_norm"] = DM_AT_REST_REDACTION
    return payload


def deserialize_message(payload: Dict[str, Any]) -> NormalizedMessage:
    message_payload = dict(payload)
    for key in ("datetime_received", "received_at"):
        value = message_payload.get(key)
        if isinstance(value, str):
            message_payload[key] = datetime.fromisoformat(value)
    return NormalizedMessage(**message_payload)


def dump_ingest_snapshot(
    path: Path, messages: Sequence[NormalizedMessage], digest_date: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "source": "ews",
            "digest_date": digest_date,
            "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(messages),
        },
        "messages": [serialize_message(message) for message in messages],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_ingest_snapshot(path: Path) -> List[NormalizedMessage]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = payload if isinstance(payload, list) else payload.get("messages", [])
    return [deserialize_message(message) for message in messages]
