"""Gold-set bootstrapped from exported Mattermost emoji reactions (PR10).

The MM incoming-webhook is outbound-only, so reactions cannot be read live — they
are exported to a JSONL out-of-band and ingested here. Each gold label is keyed by
``(trace_id, item_key)``; item_key relies on PR1's stable content-hash evidence ids
so a reaction maps back to the exact delivered item.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

POSITIVE_EMOJI = {"+1", "thumbsup", "white_check_mark", "heavy_check_mark", "ok_hand"}
NEGATIVE_EMOJI = {"-1", "thumbsdown", "x", "no_entry", "no_entry_sign"}

GoldKey = Tuple[str, str]


def item_key(evidence_id: str, title: str) -> str:
    """Stable per-item key (PR1 ids make this reproducible across runs)."""
    return f"{evidence_id}|{(title or '')[:64]}"


def _emoji_label(emoji: str) -> Optional[bool]:
    name = (emoji or "").strip().strip(":").lower()
    if name in POSITIVE_EMOJI:
        return True
    if name in NEGATIVE_EMOJI:
        return False
    return None


@dataclass
class GoldSet:
    labels: Dict[GoldKey, bool]
    strata: Dict[GoldKey, str]  # key -> "ru" | "en"

    def label(self, trace_id: str, key: str) -> Optional[bool]:
        return self.labels.get((trace_id, key))

    def stratum(self, trace_id: str, key: str) -> str:
        return self.strata.get((trace_id, key), "ru")

    def __len__(self) -> int:
        return len(self.labels)

    def stats(self) -> Dict[str, int]:
        positive = sum(1 for v in self.labels.values() if v)
        return {
            "total": len(self.labels),
            "positive": positive,
            "negative": len(self.labels) - positive,
        }


def load_gold_jsonl(path: Path) -> GoldSet:
    """Load reactions JSONL. Each line: {trace_id, evidence_id|item_key, title?, emoji, lang?}.

    The last reaction for a key wins. Unknown emojis are ignored.
    """
    labels: Dict[GoldKey, bool] = {}
    strata: Dict[GoldKey, str] = {}
    for record in _iter_jsonl(path):
        trace_id = record.get("trace_id")
        if not trace_id:
            continue
        key = record.get("item_key") or item_key(
            record.get("evidence_id", ""), record.get("title", "")
        )
        verdict = _emoji_label(record.get("emoji", ""))
        if verdict is None:
            continue
        labels[(trace_id, key)] = verdict
        strata[(trace_id, key)] = (record.get("lang") or "ru").lower()
    return GoldSet(labels=labels, strata=strata)


def _iter_jsonl(path: Path) -> Iterable[dict]:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
