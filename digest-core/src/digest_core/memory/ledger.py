"""Cross-run delivered-items ledger (EP-7, frontier-audit F8).

Episodic dedup memory for a daily product: remember WHICH evidence already
backed a delivered item so a multi-day action gets ``seen_before: true``
instead of resurfacing as brand new. v1 annotates only — suppression /
down-ranking and the default flip are owner decision D3 (R3 degrade-not-drop).

Privacy by construction (memory-design skill retention rules):

- identifiers are sanitized BEFORE persisting — the ledger stores only SHA-256
  fingerprints of ``evidence_id|msg_id``; no subjects, titles, bodies, or raw ids;
- the TTL sweep on every load IS the data-retention policy: entries whose
  ``last_seen`` is older than ``memory.dedup_ttl_days`` are evicted on read and
  not rewritten; deleting the file is right-to-be-forgotten;
- with ``memory.dedup_ledger=false`` (the default) nothing is ever read or
  written — today's privacy-via-not-storing posture is preserved bit-for-bit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import structlog

logger = structlog.get_logger()


def item_fingerprint(evidence_id: str, msg_id: str) -> str:
    """Stable identity of the evidence behind an item.

    Evidence ids are deterministic content hashes (PR1), so the same underlying
    evidence yields the same fingerprint across runs and days.
    """
    return hashlib.sha256(f"{evidence_id}|{msg_id}".encode("utf-8")).hexdigest()


class DedupLedger:
    """JSONL ledger of hashed fingerprints with first_seen/last_seen + TTL."""

    def __init__(self, path: Path, ttl_days: int = 14, now: Optional[datetime] = None):
        self.path = path
        self.ttl = timedelta(days=ttl_days)
        self._now = now or datetime.now(timezone.utc)
        self._entries: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                fingerprint = entry.get("fp")
                last_seen = datetime.fromisoformat(entry["last_seen"])
                # TTL sweep on load = the enforced retention policy.
                if fingerprint and self._now - last_seen <= self.ttl:
                    self._entries[fingerprint] = entry
        except Exception as exc:
            # A corrupt ledger must never break the digest run (degrade-not-drop).
            logger.warning("Dedup ledger unreadable; starting empty", error=str(exc))
            self._entries = {}

    def __len__(self) -> int:
        return len(self._entries)

    def seen(self, fingerprint: str) -> bool:
        return fingerprint in self._entries

    def record(self, fingerprint: str) -> None:
        now_iso = self._now.isoformat()
        entry = self._entries.get(fingerprint)
        if entry:
            entry["last_seen"] = now_iso
        else:
            self._entries[fingerprint] = {
                "fp": fingerprint,
                "first_seen": now_iso,
                "last_seen": now_iso,
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(entry, ensure_ascii=False) for entry in self._entries.values()]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
