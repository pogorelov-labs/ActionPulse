"""Cross-digest history — search/browse items across all past digest artifacts.

Complements ``read`` (one day's digest) and ``search`` (the raw message store): this searches
the curated digest OUTPUT history — what your digests actually surfaced — over time. It scans the
artifacts in the out dir (reusing the reader's loader); no store or network is needed, so it works
even when the encrypted store is off.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from digest_core.assemble.labels import normalize_section
from digest_core.llm.schemas import Item
from digest_core.ui.reader import list_digests, load_digest


@dataclass(frozen=True)
class HistoryHit:
    """One matching item, tagged with the digest day + section it came from."""

    digest_date: str
    section_title: str
    section_key: Optional[str]
    item: Item


def _haystack(item: Item) -> str:
    """The searchable text of an item: title + source subject/from + the cited quotes."""
    parts = [item.title or "", item.source_subject or "", item.source_from or ""]
    parts += [span.preview or "" for span in item.evidence_spans]
    parts += [cite.quote or "" for cite in item.citations]
    return " ".join(parts).lower()


def search_history(
    out_dir: Path,
    query: Optional[str] = None,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    section: Optional[str] = None,
    limit: int = 50,
) -> List[HistoryHit]:
    """Items across every digest in ``out_dir`` (newest first), filtered by keyword + date range
    + canonical section key.

    Dates are ``YYYY-MM-DD`` so lexicographic comparison is chronological. ``query`` is a
    case-insensitive substring over the item's text; ``section`` is a canonical key
    (``my_actions`` / ``urgent`` / ``fyi`` / ``status`` / ``unconfirmed``). A single unreadable
    artifact is skipped, never fatal. Newest-first means ``limit`` keeps the most recent matches.
    """
    q = (query or "").strip().lower()
    want_section = (section or "").strip().lower() or None
    hits: List[HistoryHit] = []
    for path in list_digests(out_dir):  # newest first
        date = path.stem.replace("digest-", "")
        if since and date < since:
            continue
        if until and date > until:
            continue
        try:
            digest = load_digest(path)
        except Exception:  # noqa: BLE001 - one bad artifact must not abort the whole browse
            continue
        for sec in digest.sections:
            key = normalize_section(sec.title)
            if want_section and key != want_section:
                continue
            for item in sec.items:
                if q and q not in _haystack(item):
                    continue
                hits.append(HistoryHit(date, sec.title, key, item))
                if len(hits) >= limit:
                    return hits
    return hits
