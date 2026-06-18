"""Offset-aware text chunking for the store (embedding + evidence-span units).

Mirrors the runtime ``EvidenceSplitter`` algorithm — paragraph-first, sentence
split when a paragraph is too long, ``words * 1.3`` token estimate — but is
self-contained and tracks **character offsets** into the normalized body so each
chunk backs a BR v3.0 ``evidence_span`` (exact substring ``body[start:end]``).
"""

from __future__ import annotations

import hashlib
import re
from typing import List, NamedTuple

_TOKEN_RATIO = 1.3
_DEFAULT_MAX_TOKENS = 512
_PARA_RE = re.compile(r"\n\s*\n")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


class TextChunk(NamedTuple):
    text: str
    char_start: int
    char_end: int  # exclusive
    token_count: int


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * _TOKEN_RATIO)


def chunk_id(urn: str, chunk_index: int, text: str) -> str:
    """Deterministic store chunk id (parallels evidence_id's ``ev_`` scheme)."""
    digest = hashlib.sha256(f"{urn}\x01{chunk_index}\x01{text}".encode("utf-8")).hexdigest()
    return "ch_" + digest[:16]


def _segments(text: str, delim: re.Pattern, base: int = 0) -> List[TextChunk]:
    """Non-empty segments split on ``delim``, carrying absolute offsets."""
    out: List[TextChunk] = []
    pos = 0
    for m in delim.finditer(text):
        seg = text[pos : m.start()]
        if seg.strip():
            out.append(TextChunk(seg, base + pos, base + m.start(), estimate_tokens(seg)))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        out.append(TextChunk(tail, base + pos, base + len(text), estimate_tokens(tail)))
    return out


def _hard_slices(text: str, base: int, max_tokens: int) -> List[TextChunk]:
    """Last-resort fixed-width slices for an over-long single sentence."""
    max_chars = max(1, max_tokens * 5)  # ~5 chars/token incl. spaces
    out: List[TextChunk] = []
    for i in range(0, len(text), max_chars):
        seg = text[i : i + max_chars]
        if seg.strip():
            out.append(TextChunk(seg, base + i, base + i + len(seg), estimate_tokens(seg)))
    return out


def chunk_text(body: str, *, max_tokens: int = _DEFAULT_MAX_TOKENS) -> List[TextChunk]:
    """Split ``body`` into offset-accurate chunks (``body[start:end]`` round-trips)."""
    body = body or ""
    if not body.strip():
        return []

    out: List[TextChunk] = []
    buf: List[TextChunk] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if not buf:
            return
        start, end = buf[0].char_start, buf[-1].char_end
        text = body[start:end]
        out.append(TextChunk(text, start, end, estimate_tokens(text)))
        buf = []
        buf_tokens = 0

    for para in _segments(body, _PARA_RE):
        if para.token_count > max_tokens:
            flush()
            # Split the long paragraph by sentences; hard-slice any giant sentence.
            for sent in _segments(para.text, _SENT_RE, base=para.char_start):
                pieces = (
                    [sent]
                    if sent.token_count <= max_tokens
                    else _hard_slices(sent.text, sent.char_start, max_tokens)
                )
                out.extend(pieces)
        elif buf_tokens + para.token_count > max_tokens:
            flush()
            buf = [para]
            buf_tokens = para.token_count
        else:
            buf.append(para)
            buf_tokens += para.token_count
    flush()
    return out
