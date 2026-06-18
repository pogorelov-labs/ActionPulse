"""TTL sweep + space reclamation for the encrypted store.

The store's own retention domain (default 30 days), distinct from the plaintext
``var/out`` artifacts (``retention.keep_days``, 7d) and the hash-only dedup ledger
(``memory.dedup_ttl_days``, 7d). The longer window is acceptable because the store
is encrypted at rest. Deleting a message cascades to its chunks/embeddings (FK
``ON DELETE CASCADE``) and removes its FTS row (delete trigger).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def sweep_ttl(conn, ttl_days: int, *, now: Optional[datetime] = None) -> int:
    """Delete messages older than ``ttl_days`` (by ``received_at``). Returns count.

    ``ttl_days < 1`` is a no-op safety rail (mirrors ``maintenance.prune_artifacts``).
    """
    if ttl_days < 1:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = int(now.timestamp()) - ttl_days * 86400
    conn.execute("BEGIN")
    try:
        cur = conn.execute("DELETE FROM messages WHERE received_epoch < ?", (cutoff,))
        deleted = cur.rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return deleted


def checkpoint(conn) -> None:
    """Fold the WAL back into the main file and truncate it (bounds sidecar size)."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def vacuum(conn) -> None:
    """Reclaim free pages after large deletes (rewrites the encrypted file)."""
    conn.execute("VACUUM")
