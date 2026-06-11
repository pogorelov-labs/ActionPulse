"""Offline best-of-N proof harness (EP-10 acceptance criterion).

Builds candidate sets with CONTROLLED quality over the frozen replay corpus and
runs the real selector on them — no LLM, no network, fully deterministic:

* candidate 0 simulates an imperfect deterministic extraction (every other
  span paraphrased, so its offset verification fails);
* candidate 1 is fully verbatim (every span offset-verifiable);
* candidate 2 is fully paraphrased (the worst sample).

The acceptance proof (ENHANCEMENT_PROGRAM EP-10): **support-recall(selected) >=
support-recall(N=1) on every corpus case** — and strictly greater here, because
the selector must recover the verbatim candidate when the deterministic one is
degraded. Ties prefer candidate 0, so the selector can never do worse than
today's single-shot behavior. Live sampling quality under real temperature is a
separate question (`requires corp validation`, EP-14); this harness proves the
SELECTOR, not the sampler.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from digest_core.eval.corpus import Case
from digest_core.llm.best_of_n import select_best_candidate
from digest_core.llm.schemas import Digest, EvidenceSpan, Item, Section

PARAPHRASE_SUFFIX = " (пересказ, не дословно)"


def _candidate(messages, *, paraphrase_every: int | None) -> Digest:
    """One item per message; spans verbatim except every k-th, which is paraphrased.

    ``paraphrase_every=1`` degrades every span; ``None`` degrades nothing. The
    paraphrase suffix guarantees the quote is NOT a substring of the body, so
    the gate's offset check fails exactly where intended.
    """
    items: List[Item] = []
    for i, message in enumerate(messages):
        body = (message.text_body or "").strip()
        if not body:
            continue
        quote = body[:60].strip()
        if paraphrase_every and i % paraphrase_every == 0:
            quote += PARAPHRASE_SUFFIX
        items.append(
            Item(
                title=f"Действие по письму {i + 1}",
                evidence_id=f"bofn-{i}",
                confidence=0.8,
                source_ref={"type": "email", "msg_id": message.msg_id},
                evidence_spans=[EvidenceSpan(msg_id=message.msg_id, quote=quote)],
            )
        )
    return Digest(
        schema_version="1.0",
        prompt_version="bofn-harness",
        digest_date="harness",
        trace_id="bofn",
        sections=[Section(title="Мои действия", items=items)],
    )


def build_candidates(messages) -> List[Digest]:
    return [
        _candidate(messages, paraphrase_every=2),  # degraded deterministic shot
        _candidate(messages, paraphrase_every=None),  # fully verbatim sample
        _candidate(messages, paraphrase_every=1),  # fully paraphrased sample
    ]


def evaluate_case(case: Case) -> Dict[str, Any]:
    """Run the selector over controlled candidates built from one corpus case."""
    from digest_core.run import _load_ingest_snapshot

    messages = _load_ingest_snapshot(case.snapshot_path)
    msg_map = {m.msg_id: m.text_body for m in messages if m.msg_id}
    candidates = build_candidates(messages)
    selected, scores = select_best_candidate(candidates, msg_map)

    recall_n1 = scores[0].support_recall
    recall_best = scores[selected].support_recall
    return {
        "case": case.name,
        "n_candidates": len(candidates),
        "selected": selected,
        "support_recall_n1": round(recall_n1, 4),
        "support_recall_best": round(recall_best, 4),
        "scores": [round(s.support_recall, 4) for s in scores],
        "ok": recall_best >= recall_n1,
    }


def evaluate_corpus_best_of_n(cases: List[Case]) -> Tuple[bool, List[Dict[str, Any]]]:
    """The EP-10 offline proof over the whole corpus. Returns (all_ok, reports)."""
    reports = [evaluate_case(case) for case in cases]
    return all(report["ok"] for report in reports), reports
