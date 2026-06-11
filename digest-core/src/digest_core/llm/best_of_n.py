"""Best-of-N candidate selection by the citation gate (EP-10, D5/D6).

The citation gate is a deterministic offset+SHA verifier — here it doubles as
the SELECTOR over N extraction candidates: each candidate digest is annotated
fidelity-only (zero network — selection never spends the scarce reranker) and
the one with the best offset-verifiable support recall wins.

Tie-breaks, in order: fewer weak items, more evidence-backed items, then the
EARLIEST candidate — candidate 0 is the deterministic temp-0 extraction, so an
all-tie selects today's behavior exactly. An optional pairwise judge
(``eval/judge.py pairwise_judge`` — the path D5 reserves for EP-10) can break
remaining exact ties when a judge gateway is provided (corp-only); any judge
failure falls back to the deterministic order (degrade-not-drop).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import structlog

from digest_core.config import RerankerConfig
from digest_core.evidence.citation_gate import CitationGate, support_recall
from digest_core.llm.schemas import Digest

logger = structlog.get_logger()


@dataclass
class CandidateScore:
    index: int
    support_recall: float
    weak_items: int
    backed_items: int

    def sort_key(self) -> Tuple[float, int, int, int]:
        """Higher is better; the trailing -index makes earlier candidates win ties."""
        return (self.support_recall, -self.weak_items, self.backed_items, -self.index)


def score_candidate(index: int, digest: Digest, msg_map: Dict[str, str]) -> CandidateScore:
    """Annotate a candidate with a fidelity-only gate and read its recall.

    The gate mutates item annotations in place — fine for selection, since the
    winning candidate proceeds to the same shadow-gate annotation it would get
    anyway, and losers are discarded.
    """
    gate = CitationGate(msg_map, reranker=None, config=RerankerConfig())
    gate.annotate(digest)
    recall, weak = support_recall(digest)
    backed = sum(
        1 for section in digest.sections for item in section.items if item.evidence_id != "system"
    )
    return CandidateScore(index=index, support_recall=recall, weak_items=weak, backed_items=backed)


def _candidate_repr(digest: Digest) -> str:
    """Compact content view for the pairwise judge (titles only — no bodies)."""
    titles = [
        item.title
        for section in digest.sections
        for item in section.items
        if item.evidence_id != "system"
    ]
    return " | ".join(titles) or "(empty)"


def select_best_candidate(
    candidates: Sequence[Digest],
    msg_map: Dict[str, str],
    *,
    judge_gateway=None,
) -> Tuple[int, List[CandidateScore]]:
    """Pick the candidate with the best offset-verifiable support recall.

    Returns ``(selected_index, scores)``. Deterministic for a fixed candidate
    order; with ``judge_gateway`` set, exact ties between the deterministic
    front-runner and a challenger go through the position-debiased pairwise
    judge (both orders; an order-flip keeps the front-runner).
    """
    if not candidates:
        raise ValueError("select_best_candidate needs at least one candidate")

    scores = [score_candidate(i, digest, msg_map) for i, digest in enumerate(candidates)]
    best = max(scores, key=CandidateScore.sort_key)

    if judge_gateway is not None:
        best = _pairwise_tiebreak(best, scores, candidates, judge_gateway)

    return best.index, scores


def _pairwise_tiebreak(
    best: CandidateScore,
    scores: List[CandidateScore],
    candidates: Sequence[Digest],
    judge_gateway,
) -> CandidateScore:
    """Let the pairwise judge pick among candidates with IDENTICAL gate scores.

    Only exact metric ties reach the judge — the gate's verdict is never
    overridden by model preference (the gate is the selector; the judge is the
    tie-break, per D5). Judge failure keeps the deterministic winner.
    """
    tied = [
        s
        for s in scores
        if s.index != best.index
        and (s.support_recall, s.weak_items, s.backed_items)
        == (best.support_recall, best.weak_items, best.backed_items)
    ]
    if not tied:
        return best

    from digest_core.eval.judge import pairwise_judge

    winner = best
    for challenger in tied:
        try:
            verdict = pairwise_judge(
                judge_gateway,
                "Two candidate digests extracted from the same evidence.",
                _candidate_repr(candidates[winner.index]),
                _candidate_repr(candidates[challenger.index]),
            )
        except Exception as exc:
            logger.warning(
                "Pairwise tie-break degraded; keeping the deterministic winner",
                error_type=type(exc).__name__,
                winner_index=winner.index,
            )
            return winner
        if verdict == "b":
            winner = challenger
    return winner


def candidate_summary(scores: Sequence[CandidateScore], selected: int) -> Dict[str, object]:
    """run_meta payload (D6 visibility): counts and scores only, no content."""
    return {
        "n_candidates": len(scores),
        "selected": selected,
        "support_recall": [round(s.support_recall, 4) for s in scores],
        "weak_items": [s.weak_items for s in scores],
    }


def best_of_n_meta(scores: Sequence[CandidateScore], selected: int) -> Optional[Dict[str, object]]:
    """``candidate_summary`` when sampling actually happened, else None."""
    if len(scores) <= 1:
        return None
    return candidate_summary(scores, selected)
