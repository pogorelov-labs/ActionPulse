"""LLM-judge over digest items + P/R/F1/Brier metrics (PR10).

The judge (``qwen35-35b-a3b`` on its own RPM bucket) classifies whether each item is
supported by its cited span. It is OFF by default (judge_enabled=False) and never on
the live run path — it scores frozen/exported outputs offline. The metric functions
are pure and deterministic; the LLM call is record/replay-aware via the gateway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Dict, List

JUDGE_PROMPT = (
    "You are a strict verifier. Decide whether the digest item is fully supported by "
    "the quoted evidence span. Reply with JSON only: "
    '{"supported": true|false, "prob_supported": 0.0-1.0}. '
    "Supported means the action/fact is directly stated in the span; do not infer."
)


@dataclass
class JudgeVerdict:
    item_key: str
    supported: bool
    prob_supported: float


class LLMJudge:
    """Wraps an LLMGateway-like client with the judge model on its own bucket."""

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


def _prf(tp: int, fp: int, fn: int) -> Dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def compute_judge_metrics(
    records: List[dict], *, stratum_key: Callable[[dict], str] = lambda r: r.get("lang", "ru")
) -> Dict[str, Dict[str, float]]:
    """Per-stratum P/R/F1, hallucination rate, Brier.

    Each record: {predicted: bool (judge says supported), gold: bool (human good),
    prob: float, lang: "ru"|"en"}. Positive class = supported/good.
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
    return out
