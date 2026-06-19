"""Hybrid search over the store: FTS5 keyword + brute-force-cosine semantic + RRF.

* ``keyword`` — FTS5 ``MATCH`` ranked by BM25 (works fully offline).
* ``semantic`` — embed the query (via the same backend used at ingest) and rank
  stored chunk vectors by cosine in NumPy (brute force; fine at this corpus size).
* ``hybrid`` — Reciprocal Rank Fusion of the two, fused on ``message_id`` so the
  very different BM25 and cosine scales need no normalization.

NumPy is imported lazily (only the ``store`` extra ships it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence

import structlog

logger = structlog.get_logger(__name__)

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_RRF_K = 60


class EmbeddingBackend(Protocol):
    """Anything that turns texts into dense vectors (gateway EmbeddingsClient fits)."""

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


@dataclass(frozen=True)
class SearchHit:
    message_id: str
    score: float
    subject: str
    snippet: str
    received_at: str
    source: str
    author_email: str
    chunk_id: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)


def _fts_query(query: str) -> Optional[str]:
    """Robust FTS5 MATCH string: AND of the query's word tokens, each wrapped as an
    FTS5 **string literal** so operators (AND/OR/NOT/NEAR) and punctuation are matched
    as literal terms instead of parsed as syntax. Without the quoting, a user query
    like ``budget AND status`` — or a bare ``AND`` — raises ``OperationalError: fts5:
    syntax error`` and crashes keyword()/hybrid(). Returns None for an empty query."""
    tokens = _WORD_RE.findall(query or "")
    if not tokens:
        return None
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _since_epoch(since: Optional[str]) -> Optional[int]:
    if not since:
        return None
    dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _filters(source: Optional[str], since: Optional[str], alias: str = "m"):
    """Build the shared (clause, params) for source/since filters."""
    clauses: List[str] = []
    params: Dict[str, Any] = {}
    if source:
        clauses.append(f"{alias}.source = :source")
        params["source"] = source
    since_epoch = _since_epoch(since)
    if since_epoch is not None:
        clauses.append(f"{alias}.received_epoch >= :since_epoch")
        params["since_epoch"] = since_epoch
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def keyword(
    conn,
    query: str,
    *,
    limit: int = 20,
    source: Optional[str] = None,
    since: Optional[str] = None,
) -> List[SearchHit]:
    match = _fts_query(query)
    if not match:
        return []
    where_extra, params = _filters(source, since)
    params.update({"q": match, "limit": limit})
    sql = (
        "SELECT m.id, m.subject, m.received_at, m.source, m.author_email, "
        "snippet(messages_fts, 1, '[', ']', '…', 12) AS snip, bm25(messages_fts) AS score "
        "FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid "
        "WHERE messages_fts MATCH :q" + where_extra + " ORDER BY score LIMIT :limit"
    )
    hits: List[SearchHit] = []
    for mid, subject, received_at, src, author, snip, score in conn.execute(sql, params).fetchall():
        hits.append(
            SearchHit(
                message_id=mid,
                score=-float(score),  # bm25: smaller is better → negate so higher=better
                subject=subject or "",
                snippet=snip or "",
                received_at=received_at,
                source=src,
                author_email=author or "",
                provenance={"method": "keyword", "bm25": float(score)},
            )
        )
    return hits


def _load_matrix(
    conn, model: str, source: Optional[str], since: Optional[str], *, max_rows: Optional[int] = None
):
    """Return (chunk_ids, np.ndarray[N,dim] float32) for the model.

    Bounded + defensive:
    * ``max_rows`` caps how many vectors are loaded (most-recent first), so an
      unfiltered semantic search over a huge corpus can't OOM. A hit cap is logged.
    * vectors whose decoded dim disagrees with the modal dim are SKIPPED (a gateway/
      model drift would otherwise crash ``np.vstack`` and brick ALL search).
    """
    import numpy as np

    where_extra, params = _filters(source, since)
    params["model"] = model
    sql = (
        "SELECT e.chunk_id, e.dim, e.vector "
        "FROM embeddings e JOIN chunks c ON c.chunk_id = e.chunk_id "
        "JOIN messages m ON m.id = c.message_id "
        "WHERE e.model = :model" + where_extra + " ORDER BY m.received_epoch DESC"
    )
    if max_rows:
        sql += " LIMIT :max_rows"
        params["max_rows"] = int(max_rows)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return [], np.empty((0, 0), dtype=np.float32)
    if max_rows and len(rows) >= int(max_rows):
        logger.warning(
            "store_search_truncated",
            loaded=len(rows),
            cap=int(max_rows),
            hint="semantic search limited to the most recent rows; filter with --since/--source",
        )
    expected_dim = rows[0][1]
    chunk_ids: List[str] = []
    vecs: List[Any] = []
    skipped = 0
    for cid, dim, blob in rows:
        if not dim or dim != expected_dim or len(blob) % dim != 0:
            skipped += 1
            continue
        itemsize = len(blob) // dim
        np_dtype = np.float16 if itemsize == 2 else np.float32
        vec = np.frombuffer(blob, dtype=np_dtype).astype(np.float32)
        if vec.shape[0] != expected_dim:
            skipped += 1
            continue
        chunk_ids.append(cid)
        vecs.append(vec)
    if skipped:
        logger.warning(
            "store_search_dim_mismatch", skipped=skipped, expected_dim=expected_dim, model=model
        )
    if not vecs:
        return [], np.empty((0, 0), dtype=np.float32)
    return chunk_ids, np.vstack(vecs)


def semantic(
    conn,
    backend: EmbeddingBackend,
    query: str,
    *,
    limit: int = 20,
    model: str = "bge-m3",
    source: Optional[str] = None,
    since: Optional[str] = None,
    max_rows: Optional[int] = None,
) -> List[SearchHit]:
    import numpy as np

    if not (query or "").strip():
        return []
    qvecs = backend.embed([query])
    if not qvecs:
        return []
    chunk_ids, mat = _load_matrix(conn, model, source, since, max_rows=max_rows)
    if mat.shape[0] == 0:
        return []
    q = np.asarray(qvecs[0], dtype=np.float32)
    # Guard a query-vs-stored dim mismatch (e.g. the gateway model changed) — bail
    # gracefully instead of raising a numpy broadcast error on the dot product.
    if q.shape[0] != mat.shape[1]:
        logger.warning(
            "store_search_query_dim_mismatch",
            query_dim=int(q.shape[0]),
            stored_dim=int(mat.shape[1]),
            model=model,
        )
        return []
    q = q / (np.linalg.norm(q) or 1.0)
    mat_n = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sims = mat_n @ q
    k = min(limit, sims.shape[0])
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    ranked = [(chunk_ids[i], float(sims[i])) for i in top]
    return _hits_for_chunks(conn, ranked, method="semantic")


def _hits_for_chunks(conn, ranked, *, method: str) -> List[SearchHit]:
    """Build SearchHits for ranked (chunk_id, score) pairs, preserving order."""
    if not ranked:
        return []
    by_id = {cid: score for cid, score in ranked}
    placeholders = ",".join("?" for _ in ranked)
    sql = (
        "SELECT c.chunk_id, c.text, m.id, m.subject, m.received_at, m.source, m.author_email "
        "FROM chunks c JOIN messages m ON m.id = c.message_id "
        f"WHERE c.chunk_id IN ({placeholders})"
    )
    detail = {row[0]: row for row in conn.execute(sql, [cid for cid, _ in ranked]).fetchall()}
    hits: List[SearchHit] = []
    for cid, score in ranked:
        row = detail.get(cid)
        if row is None:
            continue
        _cid, text, mid, subject, received_at, src, author = row
        snippet = (text or "")[:200]
        hits.append(
            SearchHit(
                message_id=mid,
                score=score,
                subject=subject or "",
                snippet=snippet,
                received_at=received_at,
                source=src,
                author_email=author or "",
                chunk_id=cid,
                provenance={"method": method, "cosine": by_id[cid]},
            )
        )
    return hits


def _message_vector(conn, message_id: str, model: str):
    """Mean-pool the source message's stored chunk vectors → one query vector, or None.

    Lets ``related_to_message`` run OFFLINE when the store is already embedded (no
    gateway call): the message's own vectors are the query.
    """
    import numpy as np

    rows = conn.execute(
        "SELECT e.dim, e.vector FROM embeddings e JOIN chunks c ON c.chunk_id = e.chunk_id "
        "WHERE c.message_id = ? AND e.model = ?",
        (message_id, model),
    ).fetchall()
    vecs: List[Any] = []
    for dim, blob in rows:
        if not dim or len(blob) % dim != 0:
            continue
        itemsize = len(blob) // dim
        np_dtype = np.float16 if itemsize == 2 else np.float32
        vec = np.frombuffer(blob, dtype=np_dtype).astype(np.float32)
        if vec.shape[0] == dim:
            vecs.append(vec)
    if not vecs:
        return None
    return np.mean(np.vstack(vecs), axis=0)


def message_cosine(conn, id_a: str, id_b: str, *, model: str = "bge-m3") -> Optional[float]:
    """Cosine similarity between two messages' mean-pooled stored chunk vectors.

    OFFLINE (no gateway): returns None when either message has no stored vectors for
    the model, or their dims disagree — the caller treats None as 'not comparable'.
    """
    import numpy as np

    va = _message_vector(conn, id_a, model)
    vb = _message_vector(conn, id_b, model)
    if va is None or vb is None or va.shape != vb.shape:
        return None
    a = va / (np.linalg.norm(va) or 1.0)
    b = vb / (np.linalg.norm(vb) or 1.0)
    return float(a @ b)


def related_to_message(
    conn,
    message_id: str,
    *,
    model: str = "bge-m3",
    limit: int = 10,
    max_rows: Optional[int] = None,
    backend: Optional[EmbeddingBackend] = None,
) -> List[SearchHit]:
    """Chunks most similar to ``message_id``, excluding the message's own chunks.

    Uses the message's stored chunk vectors as the query (OFFLINE when embedded); if
    it has none and a ``backend`` is given, embeds its body on the fly. Returns []
    when there is nothing to compare (no query vector or no other embedded chunks).
    """
    import numpy as np

    qvec = _message_vector(conn, message_id, model)
    if qvec is None and backend is not None:
        row = conn.execute(
            "SELECT body_normalized FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row and row[0]:
            embs = backend.embed([row[0]])
            if embs:
                qvec = np.asarray(embs[0], dtype=np.float32)
    if qvec is None:
        return []
    chunk_ids, mat = _load_matrix(conn, model, None, None, max_rows=max_rows)
    if mat.shape[0] == 0 or qvec.shape[0] != mat.shape[1]:
        return []
    own = {
        r[0]
        for r in conn.execute("SELECT chunk_id FROM chunks WHERE message_id = ?", (message_id,))
    }
    keep = [i for i, cid in enumerate(chunk_ids) if cid not in own]
    if not keep:
        return []
    sub_ids = [chunk_ids[i] for i in keep]
    sub_mat = mat[keep]
    q = qvec / (np.linalg.norm(qvec) or 1.0)
    mat_n = sub_mat / (np.linalg.norm(sub_mat, axis=1, keepdims=True) + 1e-12)
    sims = mat_n @ q
    k = min(limit, sims.shape[0])
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    ranked = [(sub_ids[i], float(sims[i])) for i in top]
    return _hits_for_chunks(conn, ranked, method="related")


def hybrid(
    conn,
    backend: EmbeddingBackend,
    query: str,
    *,
    limit: int = 20,
    model: str = "bge-m3",
    source: Optional[str] = None,
    since: Optional[str] = None,
    max_rows: Optional[int] = None,
) -> List[SearchHit]:
    pool = max(limit * 4, 50)
    kw = keyword(conn, query, limit=pool, source=source, since=since)
    sem = semantic(
        conn, backend, query, limit=pool, model=model, source=source, since=since, max_rows=max_rows
    )
    fused: Dict[str, Dict[str, Any]] = {}
    for rank, hit in enumerate(kw, 1):
        e = fused.setdefault(hit.message_id, {"hit": hit, "score": 0.0, "prov": {}})
        e["score"] += 1.0 / (_RRF_K + rank)
        e["prov"]["rank_keyword"] = rank
        e["prov"]["bm25"] = hit.provenance.get("bm25")
    for rank, hit in enumerate(sem, 1):
        e = fused.get(hit.message_id)
        if e is None:
            e = fused.setdefault(hit.message_id, {"hit": hit, "score": 0.0, "prov": {}})
        else:
            # prefer the chunk-level hit (carries a real snippet/chunk_id)
            e["hit"] = hit
        e["score"] += 1.0 / (_RRF_K + rank)
        e["prov"]["rank_semantic"] = rank
        e["prov"]["cosine"] = hit.provenance.get("cosine")
    ordered = sorted(fused.values(), key=lambda e: e["score"], reverse=True)[:limit]
    out: List[SearchHit] = []
    for e in ordered:
        hit = e["hit"]
        out.append(
            SearchHit(
                message_id=hit.message_id,
                score=e["score"],
                subject=hit.subject,
                snippet=hit.snippet,
                received_at=hit.received_at,
                source=hit.source,
                author_email=hit.author_email,
                chunk_id=hit.chunk_id,
                provenance={"method": "hybrid", **e["prov"]},
            )
        )
    return out
