"""Mattermost delivery target."""

from __future__ import annotations

from typing import List

import httpx
import structlog

from digest_core.config import MattermostDeliverConfig
from digest_core.llm.schemas import Digest

logger = structlog.get_logger()

DEFAULT_PING_TEXT = (
    "ActionPulse: проверка incoming webhook (mm-ping). " 'Свой текст: `mm-ping --message "..."`.'
)


def ping_mattermost_webhook(
    config: MattermostDeliverConfig,
    *,
    text: str | None = None,
    timeout_s: float = 20.0,
) -> int:
    """POST a single test message; returns HTTP status on success.

    Does not log the webhook URL or message body.
    """
    webhook_url = config.get_webhook_url()
    payload_text = text if text is not None else DEFAULT_PING_TEXT
    logger.info("mattermost_webhook_ping_start")
    with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
        response = client.post(webhook_url, json={"text": payload_text})
        response.raise_for_status()
    logger.info("mattermost_webhook_ping_ok", status_code=response.status_code)
    return response.status_code


class MattermostDeliverer:
    """Send digest messages to Mattermost via incoming webhook."""

    def __init__(self, config: MattermostDeliverConfig):
        self.config = config

    def deliver_digest(self, digest: Digest, json_path: str | None = None) -> dict:
        """Format and send the digest to Mattermost.

        ``json_path`` (the digest JSON artifact) is threaded into each item's
        traceability sub-line so the delivered message carries P2 evidence refs.
        """
        webhook_url = self.config.get_webhook_url()
        parts = self._split_message(
            self._format_digest(digest, json_path), self.config.max_message_length
        )

        with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
            for index, part in enumerate(parts, start=1):
                payload = {"text": part}
                response = client.post(webhook_url, json=payload)
                response.raise_for_status()
                logger.info(
                    "Mattermost delivery part sent",
                    trace_id=digest.trace_id,
                    part=index,
                    total_parts=len(parts),
                    status_code=response.status_code,
                )

        return {"status": "sent", "parts": len(parts)}

    def _format_digest(self, digest: Digest, json_path: str | None = None) -> str:
        blocks: List[str] = [f"## Дайджест действий — {digest.digest_date}"]

        for section in digest.sections:
            if not section.items:
                continue
            section_lines = [f"**{section.title}**"]
            for index, item in enumerate(section.items, start=1):
                due_part = f" | срок: {item.due}" if item.due else ""
                confidence_part = f" | уверенность: {self._confidence_label(item.confidence)}"
                prefix = (
                    "-"
                    if section.title == "К сведению" or section.title == "Статус"
                    else f"{index}."
                )
                section_lines.append(f"{prefix} {item.title}{due_part}{confidence_part}")
                trace_line = self._format_trace_line(item, json_path)
                if trace_line:
                    section_lines.append(trace_line)
            blocks.append("\n".join(section_lines))

        if self.config.include_trace_footer:
            blocks.append(f"_trace: {digest.trace_id} | items: {self._count_items(digest)}_")

        return "\n\n".join(blocks)

    def _format_trace_line(self, item, json_path: str | None) -> str:
        """Per-item P2 traceability sub-line: evidence id (+ optional json link).

        Legacy/system items without a real evidence id get no sub-line. The
        ``weak_evidence`` badge is getattr-guarded so it lights up once the gate
        (PR8) adds the field.
        """
        evidence_id = getattr(item, "evidence_id", "") or ""
        if not evidence_id or evidence_id == "system":
            return ""
        line = f"   ↳ ev: {evidence_id}"
        if json_path:
            line += f" | [json]({json_path}#{evidence_id})"
        if getattr(item, "weak_evidence", False):
            line += " | ⚠ слабое обоснование"
        if getattr(item, "seen_before", False):
            line += " | ↻ повтор"
        return line

    def _split_message(self, message: str, max_length: int) -> List[str]:
        if len(message) <= max_length:
            return [message]

        blocks = message.split("\n\n")
        chunks: List[str] = []
        current: List[str] = []

        for block in blocks:
            candidate = "\n\n".join([*current, block]) if current else block
            if len(candidate) <= max_length:
                current.append(block)
                continue

            if current:
                chunks.append("\n\n".join(current))
                current = [block]
                continue

            chunks.extend(self._split_long_block(block, max_length))

        if current:
            chunks.append("\n\n".join(current))

        total = len(chunks)
        if total <= 1:
            return chunks

        wrapped_chunks = []
        for index, chunk in enumerate(chunks, start=1):
            header = f"## Дайджест действий — часть {index}/{total}"
            wrapped_chunks.append(f"{header}\n\n{chunk}")
        return wrapped_chunks

    def _split_long_block(self, block: str, max_length: int) -> List[str]:
        lines = block.splitlines()
        chunks: List[str] = []
        current: List[str] = []

        for line in lines:
            candidate = "\n".join([*current, line]) if current else line
            if len(candidate) <= max_length:
                current.append(line)
                continue
            if current:
                chunks.append("\n".join(current))
                current = [line]
            else:
                for start in range(0, len(line), max_length):
                    chunks.append(line[start : start + max_length])
                current = []

        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _count_items(digest: Digest) -> int:
        return sum(len(section.items) for section in digest.sections)

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        if confidence >= 0.9:
            return "очень высокая"
        if confidence >= 0.7:
            return "высокая"
        if confidence >= 0.5:
            return "средняя"
        if confidence >= 0.3:
            return "низкая"
        return "очень низкая"
