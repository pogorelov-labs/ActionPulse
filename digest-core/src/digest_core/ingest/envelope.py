"""Source-neutral message envelope (PR12b).

A thin, source-agnostic wrapper so the pipeline can ingest from EWS (today) and
other sources later behind one shape. This is the seam only — EWS stays the single
live source (D4: design for source-neutrality, don't build extra sources).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from digest_core.ingest.ews import NormalizedMessage


@dataclass(frozen=True)
class Envelope:
    """A normalized message tagged with the source it came from."""

    source: str  # "ews" | "slack" | ...
    message: NormalizedMessage
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def msg_id(self) -> str:
        return self.message.msg_id


def envelopes_from_messages(source: str, messages: List[NormalizedMessage]) -> List[Envelope]:
    return [Envelope(source=source, message=message) for message in messages]


def messages_from_envelopes(envelopes: List[Envelope]) -> List[NormalizedMessage]:
    return [envelope.message for envelope in envelopes]
