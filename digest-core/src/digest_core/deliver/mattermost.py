"""Mattermost delivery target."""

from __future__ import annotations

from typing import List

import httpx
import structlog

from digest_core.assemble.labels import (
    DEFAULT_LANGUAGE,
    FYI,
    STATUS,
    confidence_text,
    display_title,
    normalize_section,
    report_strings,
    should_show_confidence,
)
from digest_core.config import MattermostDeliverConfig
from digest_core.llm.schemas import Digest

logger = structlog.get_logger()

DEFAULT_PING_TEXT = report_strings(DEFAULT_LANGUAGE)["mm_ping_text"]


def _blen(s: str) -> int:
    """UTF-8 byte length — Mattermost limits are in bytes, not code points."""
    return len(s.encode("utf-8"))


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

    def __init__(self, config: MattermostDeliverConfig, language: str = DEFAULT_LANGUAGE):
        self.config = config
        self.language = language
        self._s = report_strings(language)

    def deliver_digest(
        self,
        digest: Digest,
        json_path: str | None = None,
        llm_budget: dict | None = None,
    ) -> dict:
        """Format and send the digest to Mattermost.

        The delivered message is recipient-facing (owner decision C5/C8): it
        carries only user signals, never operator metadata. ``json_path`` and
        ``llm_budget`` are threaded for signature compatibility but no longer
        surface in the message — ``json_path`` was a local operator filesystem
        path the recipient cannot open, and the LLM budget is operator-only
        (``run_meta.llm_budget`` + structured log, the narrowed ADR-008 v2
        visibility clause). Both are still persisted in the run artifacts.
        """
        # D4 delivery guard ("guard + warn"): an incoming-webhook URL is an
        # opaque token, so the target audience is NOT derivable. When the
        # operator has not confirmed the target is a private DM/channel, emit one
        # payload-free warning and continue — never block delivery.
        if self.config.enabled and not self.config.acknowledged_private:
            logger.warning(
                "mattermost_target_privacy_unconfirmed",
                trace_id=digest.trace_id,
                hint=(
                    "Webhook target not confirmed private; the personal digest may"
                    " be visible to the channel audience. Re-run setup to confirm."
                ),
            )

        webhook_url = self.config.get_webhook_url()
        parts = self._split_message(
            self._format_digest(digest, json_path, llm_budget), self.config.max_message_length
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

    def _format_digest(
        self,
        digest: Digest,
        json_path: str | None = None,
        llm_budget: dict | None = None,
    ) -> str:
        blocks: List[str] = [f"## {self._s['digest_header']} — {digest.digest_date}"]

        for section in digest.sections:
            if not section.items:
                continue
            section_lines = [f"**{display_title(section.title, self.language)}**"]
            for index, item in enumerate(section.items, start=1):
                due_part = f" | {self._s['due_label'].lower()}: {item.due}" if item.due else ""
                confidence_part = ""
                if should_show_confidence(item.confidence, getattr(item, "weak_evidence", False)):
                    confidence_part = (
                        f" | {self._s['confidence_label'].lower()}:"
                        f" {self._confidence_label(item.confidence)}"
                    )
                prefix = "-" if normalize_section(section.title) in (FYI, STATUS) else f"{index}."
                section_lines.append(f"{prefix} {item.title}{due_part}{confidence_part}")
                trace_line = self._format_trace_line(item, json_path)
                if trace_line:
                    section_lines.append(trace_line)
            blocks.append("\n".join(section_lines))

        # Empty digest: no section had any items. Surface the "no actions"
        # block so the delivered message is not a bare header (matches the .md).
        if len(blocks) == 1:
            blocks.append(self._s["no_actions"])

        # No trace footer (owner decision C5/C8): trace_id, item count and the
        # LLM budget are operator metadata, not recipient signals. They live in
        # run_meta + the structured log; the delivered message stays clean.
        return "\n\n".join(blocks)

    def _format_trace_line(self, item, json_path: str | None) -> str:
        """Per-item recipient sub-line: user-facing badges only (owner C5/C8).

        Operator metadata (the internal ``ev: <id>`` token and the local
        ``[json](...)`` filesystem link) is stripped — the recipient cannot use
        either. What remains is recipient signal: ``⚠ <weak_basis>`` when the
        evidence is weak and ``↻ <repeat>`` when the item was seen before. An
        item with neither badge gets no sub-line. ``json_path`` is accepted for
        signature compatibility but unused.
        """
        del json_path  # operator path, no longer rendered (owner C5/C8)
        badges: List[str] = []
        if getattr(item, "weak_evidence", False):
            badges.append(f"⚠ {self._s['weak_basis']}")
        if getattr(item, "seen_before", False):
            badges.append(f"↻ {self._s['repeat']}")
        if not badges:
            return ""
        return "   ↳ " + " | ".join(badges)

    def _header_blen(self, total: int) -> int:
        """Byte length of the worst-case part header for a ``total``-part split.

        The widest ``index/total`` is ``total/total`` (most digits), so we size
        against that to guarantee every prepended header fits within budget.
        """
        header = "## " + self._s["digest_part_header"].format(index=total, total=total)
        return _blen(header) + len("\n\n")

    def _split_message(self, message: str, max_length: int) -> List[str]:
        if _blen(message) <= max_length:
            return [message]

        # The message is over budget, so it will split into >= 2 parts and each
        # delivered part will carry a "## <part i/total>" header. Reserve the
        # worst-case header byte length so a near-limit chunk does not overflow
        # once the header is prepended. The header digit count depends on the
        # chunk count, which in turn depends on the reserved space, so re-split
        # until the effective limit stabilizes (bounded: digit growth is slow).
        effective = max_length
        chunks: List[str] = []
        for _ in range(8):
            chunks = self._split_into_chunks(message, effective)
            new_effective = max_length - self._header_blen(max(len(chunks), 2))
            if new_effective == effective:
                break
            effective = new_effective

        total = len(chunks)
        if total <= 1:
            return chunks

        wrapped_chunks = []
        for index, chunk in enumerate(chunks, start=1):
            header = "## " + self._s["digest_part_header"].format(index=index, total=total)
            wrapped_chunks.append(f"{header}\n\n{chunk}")
        return wrapped_chunks

    def _split_into_chunks(self, message: str, max_length: int) -> List[str]:
        """Greedily pack blocks (then lines) into chunks of <= ``max_length`` bytes."""
        blocks = message.split("\n\n")
        chunks: List[str] = []
        current: List[str] = []

        for block in blocks:
            candidate = "\n\n".join([*current, block]) if current else block
            if _blen(candidate) <= max_length:
                current.append(block)
                continue

            if current:
                chunks.append("\n\n".join(current))
                current = []
                if _blen(block) <= max_length:
                    current = [block]
                    continue
                # The lone block still overflows: split it by lines.
                chunks.extend(self._split_long_block(block, max_length))
                continue

            chunks.extend(self._split_long_block(block, max_length))

        if current:
            chunks.append("\n\n".join(current))

        return chunks

    def _split_long_block(self, block: str, max_length: int) -> List[str]:
        lines = block.splitlines()
        chunks: List[str] = []
        current: List[str] = []

        for line in lines:
            candidate = "\n".join([*current, line]) if current else line
            if _blen(candidate) <= max_length:
                current.append(line)
                continue
            if current:
                chunks.append("\n".join(current))
                current = [line]
                if _blen(line) <= max_length:
                    continue
                chunks.extend(self._split_long_line(line, max_length))
                current = []
            else:
                chunks.extend(self._split_long_line(line, max_length))
                current = []

        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _split_long_line(line: str, max_length: int) -> List[str]:
        """Split one over-budget line into <= ``max_length``-byte pieces.

        Prefers a space boundary (A5 polish); never emits a piece whose UTF-8
        byte length exceeds ``max_length``.
        """
        pieces: List[str] = []
        remaining = line
        while _blen(remaining) > max_length:
            cut = MattermostDeliverer._byte_prefix_len(remaining, max_length)
            cut = max(cut, 1)  # always make progress, even if a char > max_length
            # Prefer to break at the last space within the byte budget.
            space = remaining.rfind(" ", 1, cut)
            if space > 0:
                pieces.append(remaining[:space])
                remaining = remaining[space:].lstrip(" ")
            else:
                pieces.append(remaining[:cut])
                remaining = remaining[cut:]
        if remaining:
            pieces.append(remaining)
        return pieces

    @staticmethod
    def _byte_prefix_len(s: str, max_bytes: int) -> int:
        """Largest character count whose UTF-8 encoding is <= ``max_bytes``."""
        total = 0
        for index, ch in enumerate(s):
            total += len(ch.encode("utf-8"))
            if total > max_bytes:
                return index
        return len(s)

    def _confidence_label(self, confidence: float) -> str:
        return confidence_text(confidence, self.language)
