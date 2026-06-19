"""Gold-set bootstrapped from exported Mattermost emoji reactions (PR10).

The JSONL is produced by ``reactions harvest --gold-out`` (auth_mode=api reads
reactions live via the PAT) or exported out-of-band for a webhook deployment. Each
gold label is keyed by ``(trace_id, item_key)``; item_key relies on PR1's stable
content-hash evidence ids so a reaction maps back to the exact delivered item.

Emoji → label uses the *same* :func:`feedback.reactions.classify` vocabulary the
harvest side uses — one source of truth, so a reaction counted ack/nack on harvest
is never silently dropped here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from digest_core.feedback.reactions import classify as _classify_reaction

GoldKey = Tuple[str, str]


def item_key(evidence_id: str, title: str) -> str:
    """Stable per-item key (PR1 ids make this reproducible across runs)."""
    return f"{evidence_id}|{(title or '')[:64]}"


def _emoji_label(emoji: str) -> Optional[bool]:
    """Gold label from a Mattermost ``emoji_name`` via the canonical reaction
    vocabulary: ack → ``True``, nack → ``False``, anything else → ``None`` (ignored)."""
    signal = _classify_reaction(emoji)
    if signal == "ack":
        return True
    if signal == "nack":
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
