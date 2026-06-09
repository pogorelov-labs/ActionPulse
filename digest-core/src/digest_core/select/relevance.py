"""Fleet-backed relevance scorer for context selection (PR9).

relevance(chunk) = embeddings cosine(query, chunk) batched over all chunks, then a
cross-encoder reranker refinement on the top-K only (the reranker is the scarce,
non-batchable fleet resource, budgeted <=K/run).

NOT wired into the live run path in PR9 — it is constructed only when
``enable_relevance`` flips on (post-PC-2). Offline/tests inject mock clients; with
no embeddings client or query it returns ``{}`` so the fused score falls back to
metadata-only.
"""

from __future__ import annotations

import math
from typing import Dict, List


class RelevanceScorer:
    def __init__(self, query: str, *, embeddings=None, reranker=None, top_k: int = 10):
        self.query = query
        self.embeddings = embeddings
        self.reranker = reranker
        self.top_k = top_k

    def score_chunks(self, chunks) -> Dict[str, float]:
        if not chunks or self.embeddings is None or not self.query:
            return {}
        texts = [getattr(c, "content", "") for c in chunks]
        vectors = self.embeddings.embed([self.query] + texts)
        if not vectors or len(vectors) != len(texts) + 1:
            return {}
        query_vec, chunk_vecs = vectors[0], vectors[1:]
        scores: Dict[str, float] = {
            chunk.evidence_id: _cosine(query_vec, vec) for chunk, vec in zip(chunks, chunk_vecs)
        }

        # Reranker refinement on the top-K by cosine (budgeted; one call).
        if self.reranker is not None and self.top_k > 0:
            top = sorted(chunks, key=lambda c: scores.get(c.evidence_id, 0.0), reverse=True)
            top = top[: self.top_k]
            rerank_scores = self.reranker.score(self.query, [c.content for c in top])
            for chunk, score in zip(top, rerank_scores):
                scores[chunk.evidence_id] = float(score)
        return scores


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
