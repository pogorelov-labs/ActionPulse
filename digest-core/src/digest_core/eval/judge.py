"""Hybrid LLM-judge over digest items + P/R/F1/Brier metrics (PR10 + EP-5 step 3).

Architectures by job (decision D5; see the quality-loop ``judge-calibration``
skill and its ``judge-architectures.md``):

* **Pointwise** (:class:`LLMJudge`) — the original single-call scorer. Fine as a
  cheap advisory dashboard; research-refuted AS A GATE — never wire its score
  into exit codes.
* **Reference-anchored** (:class:`ReferenceAnchoredJudge`) — binary verdicts
  anchored to ground truth: ``judge_item`` verifies an item against its own
  quoted evidence (the calibration object — κ vs human gold labels decides if
  the judge may ever gate), ``judge_against_reference`` compares a candidate to
  a human-approved gold exemplar (the regression report). Selected via
  ``eval.judge_mode`` (default ``pointwise`` until corp calibration clears
  κ >= 0.41 with the bootstrap CI floor — the no-gate rule is hard, EP-15).
* **Pairwise** (:func:`pairwise_judge`) — position-debiased A/B preference;
  library-only, reserved for EP-10 best-of-N selection.

The judge model (``qwen35-35b-a3b``) rides the gateway with a model override on
its own RPM bucket; it is OFF by default and never on the live run path — it
scores frozen/exported outputs offline. Metric functions are pure and
deterministic; LLM calls are record/replay-aware via the gateway. Per the
calibration skill's guardrail: sanitize identifiers before sharing any labels
CSV/JSONL derived from these records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from digest_core.eval.agreement import compute_agreement, pairs_from_judge_records
from digest_core.eval.gold_set import GoldSet

JUDGE_PROMPT = (
    "You are a strict verifier. Decide whether the digest item is fully supported by "
    "the quoted evidence span. Reply with JSON only: "
    '{"supported": true|false, "prob_supported": 0.0-1.0}. '
    "Supported means the action/fact is directly stated in the span; do not infer."
)

# Reference-anchored verification (D5): same binary supported-vs-evidence
# question, but the decision is the boolean — prob is a diagnostic, never a
# gate score. Kept separate from JUDGE_PROMPT so the two architectures can
# evolve (and be calibrated) independently.
REFERENCE_VERIFY_PROMPT = (
    "You are a strict verifier. The EVIDENCE below is the authoritative reference. "
    "Decide whether the CANDIDATE digest item is fully supported by it. "
    "Judge content only — length or style do not matter. Do not infer beyond the "
    "evidence. Reply with JSON only: "
    '{"supported": true|false, "prob_supported": 0.0-1.0}.'
)

REFERENCE_COMPARE_PROMPT = (
    "You compare a CANDIDATE digest item against a REFERENCE item that a human "
    "approved for the same source email. Decide whether the candidate conveys the "
    "same action/fact at least as faithfully. Judge content only — ignore length "
    "and style. Reply with JSON only: "
    '{"supported": true|false, "prob_supported": 0.0-1.0}.'
)

PAIRWISE_JUDGE_PROMPT = (
    "You compare two digest items extracted from the same evidence. Decide which one "
    "is the more faithful, actionable extraction. Judge content only — a longer "
    "answer is not a better answer. Reply with JSON only: "
    '{"winner": "first"|"second"|"tie"}.'
)


@dataclass
class JudgeVerdict:
    item_key: str
    supported: bool
    prob_supported: float


class LLMJudge:
    """Pointwise judge — advisory dashboard ONLY (the refuted-as-gate pattern).

    Wraps an LLMGateway-like client with the judge model on its own bucket.
    """

    def __init__(self, gateway, *, stage: str = "judge"):
        self.gateway = gateway
        self.stage = stage

    def judge_item(self, item_key: str, title: str, span_quote: str, body: str) -> JudgeVerdict:
        user = f"ITEM: {title}\nSPAN: {span_quote}\n" f"EVIDENCE BODY (authoritative):\n{body}"
        raw = self.gateway.judge(JUDGE_PROMPT, user)  # gateway returns parsed dict
        data = raw if isinstance(raw, dict) else json.loads(raw)
        prob = float(data.get("prob_supported", 1.0 if data.get("supported") else 0.0))
        return JudgeVerdict(
            item_key=item_key, supported=bool(data.get("supported", False)), prob_supported=prob
        )


def _parse_verdict(item_key: str, raw: Any) -> JudgeVerdict:
    data = raw if isinstance(raw, dict) else json.loads(raw)
    prob = float(data.get("prob_supported", 1.0 if data.get("supported") else 0.0))
    return JudgeVerdict(
        item_key=item_key, supported=bool(data.get("supported", False)), prob_supported=prob
    )


class ReferenceAnchoredJudge:
    """Reference-anchored judge for the regression report (EP-5 step 3, D5).

    Binary verdicts anchored to a reference text — never a free rubric score:

    * :meth:`judge_item` — is the item supported by its own quoted evidence?
      These verdicts vs human gold labels are the calibration pairs (κ/α via
      ``compute_judge_metrics``). The κ is exact when the judged output matches
      the labeled delivery (the regression-replay case); under output drift it
      under-estimates agreement — grow the gold set from fresh reactions (EP-15).
    * :meth:`judge_against_reference` — does a candidate convey the same
      action/fact as a human-APPROVED exemplar (gold-positive rows only)?
      Feeds the regression counts in :func:`reference_eval`.

    Report-only by construction: nothing here returns an exit code or gates CI.
    """

    def __init__(self, gateway, *, stage: str = "judge"):
        self.gateway = gateway
        self.stage = stage

    def judge_item(self, item_key: str, title: str, span_quote: str, body: str) -> JudgeVerdict:
        user = (
            f"CANDIDATE: {title}\nSPAN: {span_quote}\n"
            f"EVIDENCE (authoritative reference):\n{body}"
        )
        return _parse_verdict(item_key, self.gateway.judge(REFERENCE_VERIFY_PROMPT, user))

    def judge_against_reference(
        self, item_key: str, candidate_title: str, candidate_span: str, reference_title: str
    ) -> JudgeVerdict:
        user = (
            f"REFERENCE (human-approved): {reference_title}\n"
            f"CANDIDATE: {candidate_title}\n"
            f"CANDIDATE EVIDENCE SPAN: {candidate_span}"
        )
        return _parse_verdict(item_key, self.gateway.judge(REFERENCE_COMPARE_PROMPT, user))


JUDGE_MODES = ("pointwise", "reference")


def make_judge(mode: str, gateway):
    """Judge factory for ``eval.judge_mode`` (D5). Unknown modes fail loudly."""
    if mode == "pointwise":
        return LLMJudge(gateway)
    if mode == "reference":
        return ReferenceAnchoredJudge(gateway)
    raise ValueError(f"Unknown eval.judge_mode '{mode}' (expected one of {JUDGE_MODES})")


def pairwise_judge(gateway, context: str, candidate_a: str, candidate_b: str) -> str:
    """Position-debiased pairwise preference: ``"a"`` | ``"b"`` | ``"tie"``.

    Library function ONLY — D5 reserves pairwise for EP-10 best-of-N selection;
    nothing in the run path or CI consumes it yet. Runs BOTH presentation
    orders and keeps the verdict only when they agree (an order-flip is a tie):
    position bias is measurable in single-order judging, and the prompt pins
    content-over-length to blunt verbosity bias (see judge-architectures.md).
    """

    def _ask(first: str, second: str) -> str:
        user = f"CONTEXT: {context}\nFIRST: {first}\nSECOND: {second}"
        raw = gateway.judge(PAIRWISE_JUDGE_PROMPT, user)
        data = raw if isinstance(raw, dict) else json.loads(raw)
        winner = str(data.get("winner", "tie")).strip().lower()
        return winner if winner in ("first", "second", "tie") else "tie"

    forward = _ask(candidate_a, candidate_b)  # first=a, second=b
    backward = _ask(candidate_b, candidate_a)  # first=b, second=a

    forward_pick = {"first": "a", "second": "b"}.get(forward, "tie")
    backward_pick = {"first": "b", "second": "a"}.get(backward, "tie")
    return forward_pick if forward_pick == backward_pick else "tie"


def _gold_by_evidence(gold: GoldSet) -> Dict[str, Dict[str, Any]]:
    """Index gold rows by evidence_id (the stable half of item_key).

    PR1's content-hash evidence ids are reproducible across runs, so pairing by
    evidence_id lets a fresh replay meet labels recorded on an earlier delivery
    (trace ids differ). The recorded title half of the key is the reference
    exemplar. Last row per evidence wins.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for (trace_id, key), label in gold.labels.items():
        evidence_id, _, reference_title = key.partition("|")
        if not evidence_id:
            continue
        out[evidence_id] = {
            "label": label,
            "reference_title": reference_title,
            "lang": gold.strata.get((trace_id, key), "ru"),
        }
    return out


def reference_eval(digest_json: Dict[str, Any], gold: GoldSet, judge) -> Dict[str, Any]:
    """Reference-anchored eval vs gold rows (D5): calibration records + regression.

    For every current item whose ``evidence_id`` has a gold row:

    * a calibration record — ``judge_item`` verdict vs the human label —
      feeding :func:`compute_judge_metrics` (per-stratum P/R + the κ/α
      ``agreement`` block with ``may_gate``);
    * for gold-POSITIVE rows, a regression check — ``judge_against_reference``
      candidate vs the approved exemplar (matched / regressed); gold rows whose
      evidence is absent from the digest count as ``missing``.

    Report-only by design (D2): exit codes and CI stay untouched until
    reactions-based calibration clears κ >= 0.41 with the CI floor (EP-15).
    """
    by_evidence = _gold_by_evidence(gold)
    items = [
        item
        for section in digest_json.get("sections", [])
        for item in section.get("items", [])
        if item.get("evidence_id") and item.get("evidence_id") != "system"
    ]

    records: List[dict] = []
    matched = regressed = 0
    seen_evidence = set()
    for item in items:
        evidence_id = item["evidence_id"]
        row = by_evidence.get(evidence_id)
        if row is None or evidence_id in seen_evidence:
            continue
        seen_evidence.add(evidence_id)
        title = item.get("title", "")
        spans = item.get("evidence_spans") or []
        span = (spans[0] or {}).get("quote", "") if spans else ""
        key = f"{evidence_id}|{title[:64]}"

        # The digest artifact carries verbatim spans, not full bodies — the
        # span IS the available evidence reference here (offset-verified by
        # the P2 gate upstream).
        verdict = judge.judge_item(key, title, span, span)
        records.append(
            {
                "item_key": key,
                "predicted": verdict.supported,
                "prob": verdict.prob_supported,
                "gold": row["label"],
                "lang": row["lang"],
            }
        )
        if row["label"]:
            comparison = judge.judge_against_reference(key, title, span, row["reference_title"])
            if comparison.supported:
                matched += 1
            else:
                regressed += 1

    references = sum(1 for row in by_evidence.values() if row["label"])
    return {
        "records": records,
        "metrics": compute_judge_metrics(records) if records else {},
        "regression": {
            "references": references,
            "matched": matched,
            "regressed": regressed,
            "missing": references - matched - regressed,
        },
    }


def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def compute_judge_metrics(
    records: List[dict], *, stratum_key: Callable[[dict], str] = lambda r: r.get("lang", "ru")
) -> Dict[str, Dict[str, float]]:
    """Per-stratum P/R/F1, hallucination rate, Brier — plus chance-corrected agreement.

    Each record: {predicted: bool (judge says supported), gold: bool (human good),
    prob: float, lang: "ru"|"en"}. Positive class = supported/good.

    The reserved ``"agreement"`` key (never a stratum name — strata are language
    codes) carries Cohen's κ / Krippendorff's α judge-vs-gold (EP-5): drift
    trackers and the may-gate floor, NOT the gate itself (D5).
    """
    strata: Dict[str, List[dict]] = {}
    for record in records:
        strata.setdefault(stratum_key(record), []).append(record)

    out: Dict[str, Dict[str, float]] = {}
    for name, rows in strata.items():
        tp = sum(1 for r in rows if r["predicted"] and r["gold"])
        fp = sum(1 for r in rows if r["predicted"] and not r["gold"])
        fn = sum(1 for r in rows if not r["predicted"] and r["gold"])
        metrics = _prf(tp, fp, fn)
        # hallucination = judged supported but gold bad, over all judged-supported
        judged_supported = sum(1 for r in rows if r["predicted"])
        metrics["hallucination_rate"] = round(fp / judged_supported, 4) if judged_supported else 0.0
        brier = sum(
            (float(r.get("prob", 1.0 if r["predicted"] else 0.0)) - (1.0 if r["gold"] else 0.0))
            ** 2
            for r in rows
        )
        metrics["brier"] = round(brier / len(rows), 4) if rows else 0.0
        metrics["n"] = len(rows)
        out[name] = metrics

    pairs = pairs_from_judge_records(records)
    if pairs:
        out["agreement"] = compute_agreement(pairs)
    return out
