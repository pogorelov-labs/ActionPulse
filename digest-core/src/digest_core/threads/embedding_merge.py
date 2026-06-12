"""Embedding-assisted thread merging (REDESIGN PR12a — the cosine tier).

The heuristic builder can never merge two threads whose normalized subjects
differ ("Re: Project X" vs "Question about project X") — that is exactly
where dense vectors help. This tier runs AFTER the heuristic grouping and
only over the groups the heuristics are weakest on (subject-keyed ``subj_``
and single-message ``single_`` ids); EWS ``conv_`` groups carry an
authoritative conversation id and are never touched.

Mechanics, deterministic by construction:

- one representative text per candidate thread (subject + head of the first
  body), embedded in **one batched** ``/v1/embeddings`` call;
- cosine over all candidate pairs; union–find merges pairs ≥ threshold;
- a merged group keeps the **lexicographically smallest** thread id (stable
  across runs; ids are content-hashes since PR1);
- more candidates than ``max_candidates`` → the whole tier is skipped for
  the run and logged (never a silent partial merge);
- any embeddings failure degrades to the heuristic grouping (degrade-not-
  drop) — this tier may only ever merge, never lose a message.

The reranker-pairwise band and the LLM adjudication residual from the plan
are deliberately NOT here: they would share the P2 gate's scarce reranker
budget (≤10/run) and need their own budget design — recorded as the open
remainder in REDESIGN_PLAN.md.

Privacy: enabling sends thread text to ``/v1/embeddings`` — live use is
gated by PC-2 (``threading.embedding_merge`` defaults off); offline replay
via the fleet sidecar never touches the network.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import structlog

from digest_core.ingest.ews import NormalizedMessage

logger = structlog.get_logger()

#: Head of the first body included in the representative text (the subject
#: carries most of the signal; the head disambiguates re-used subjects).
_REPRESENTATIVE_BODY_CHARS = 500

#: Thread-id prefixes eligible for embedding merge — the heuristically weak
#: groups. ``conv_`` (EWS-authoritative) ids are never candidates.
_CANDIDATE_PREFIXES = ("subj_", "single_")


def representative_text(messages: Sequence[NormalizedMessage]) -> str:
    """Subject + head of the earliest body — what gets embedded per thread."""
    if not messages:
        return ""
    first = min(messages, key=lambda m: m.datetime_received)
    subject = (first.subject or "").strip()
    body = " ".join((first.text_body or "").split())[:_REPRESENTATIVE_BODY_CHARS]
    return f"{subject}\n{body}".strip()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class _UnionFind:
    def __init__(self, items: Sequence[str]):
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        # Deterministic root: the lexicographically smallest id wins, so the
        # merged thread id is stable across runs (PR1 determinism holds).
        keep, drop = sorted((root_a, root_b))
        self._parent[drop] = keep


class EmbeddingThreadMerger:
    """Merge heuristic-weak thread groups whose representatives are close."""

    def __init__(self, embeddings, *, similarity_threshold: float = 0.85, max_candidates: int = 64):
        self._embeddings = embeddings
        self._threshold = float(similarity_threshold)
        self._max_candidates = int(max_candidates)
        self.merges_made = 0  # read by ThreadBuilder stats

    def merge(
        self, thread_groups: Dict[str, List[NormalizedMessage]]
    ) -> Dict[str, List[NormalizedMessage]]:
        self.merges_made = 0
        candidates = sorted(tid for tid in thread_groups if tid.startswith(_CANDIDATE_PREFIXES))
        if len(candidates) < 2:
            return thread_groups
        if len(candidates) > self._max_candidates:
            logger.warning(
                "Embedding merge skipped: candidate threads exceed max_candidates",
                candidates=len(candidates),
                max_candidates=self._max_candidates,
            )
            return thread_groups

        texts = [representative_text(thread_groups[tid]) for tid in candidates]
        try:
            vectors = self._embeddings.embed(texts)  # ONE batched call
        except Exception as exc:  # noqa: BLE001 - degrade to heuristic grouping
            logger.warning(
                "Embedding merge degraded: embeddings call failed",
                error=str(exc),
                candidates=len(candidates),
            )
            return thread_groups
        if len(vectors) != len(candidates):
            logger.warning(
                "Embedding merge degraded: vector count mismatch",
                expected=len(candidates),
                received=len(vectors),
            )
            return thread_groups

        union = _UnionFind(candidates)
        pairs_merged: List[Tuple[str, str, float]] = []
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                score = cosine(vectors[i], vectors[j])
                if score >= self._threshold:
                    union.union(candidates[i], candidates[j])
                    pairs_merged.append((candidates[i], candidates[j], round(score, 4)))

        if not pairs_merged:
            return thread_groups

        merged: Dict[str, List[NormalizedMessage]] = {}
        for tid, messages in thread_groups.items():
            target = union.find(tid) if tid in union._parent else tid
            if target != tid:
                self.merges_made += 1
            merged.setdefault(target, []).extend(messages)
        logger.info(
            "Embedding merge applied",
            pairs=len(pairs_merged),
            threads_before=len(thread_groups),
            threads_after=len(merged),
        )
        return merged
