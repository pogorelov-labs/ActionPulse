"""Replay + citation-fidelity harness (PR7, measure-first).

Runs the real pipeline over a frozen synthetic ingest snapshot with a
deterministic stand-in extractor, then asserts on METRICS (item count, section
taxonomy, span coverage, unverifiable evidence ids) against a committed baseline
— not on bytes. This is the measurement scaffold the shadow gate (PR8) and the
judge/calibration (PR10) build on.

The extraction is a deterministic ``SyntheticExtractor`` rather than a recorded
``--replay-llm`` file: content-hash evidence ids (PR1) make hand-committed
recordings brittle, while the synthetic extractor is fully reproducible offline
and still exercises the whole post-ingest pipeline (threads -> evidence -> select
-> assemble) plus the metric computation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from digest_core.eval.corpus import Case


class SyntheticExtractor:
    """Deterministic stand-in for the LLM gateway in the harness.

    Emits one item per selected evidence chunk, citing that chunk's evidence_id and
    a verbatim span (a prefix of the chunk body). Records the evidence ids it saw as
    ground truth so the harness can flag unverifiable citations without circularity.
    """

    seen_evidence_ids: Set[str] = set()

    def __init__(self, config, **kwargs):
        self.config = config
        self.last_request_meta: Dict[str, Any] = {
            "tokens_in": 0,
            "tokens_out": 0,
            "http_status": 200,
            "latency_ms": 0,
            "retry_count": 0,
            "validation_errors": 0,
        }

    def extract_actions(self, evidence, prompt_template, trace_id) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = []
        for chunk in evidence[:5]:  # cap keeps the digest deterministic and small
            SyntheticExtractor.seen_evidence_ids.add(chunk.evidence_id)
            subject = (chunk.message_metadata or {}).get("subject", "письма")
            quote = (chunk.content or "").strip()[:60]
            items.append(
                {
                    "title": f"Действие по теме «{subject}»",
                    "due": None,
                    "evidence_id": chunk.evidence_id,
                    "confidence": 0.8,
                    "source_ref": {"type": "email", "msg_id": chunk.msg_id},
                    "evidence_spans": ([{"msg_id": chunk.msg_id, "quote": quote}] if quote else []),
                }
            )
        return {"sections": [{"title": "Мои действия", "items": items}]}

    def get_request_stats(self) -> Dict[str, Any]:
        return {
            "last_latency_ms": 0,
            "model": getattr(self.config, "model", "synthetic"),
            "timeout_s": getattr(self.config, "timeout_s", 0),
        }


def compute_metrics(digest_json: Dict[str, Any], seen_ids: Set[str]) -> Dict[str, Any]:
    """Behavioral metrics over a produced digest (not byte comparison)."""
    sections = digest_json.get("sections", [])
    items = [item for section in sections for item in section.get("items", [])]
    cited = [item.get("evidence_id") for item in items]
    unverifiable = [eid for eid in cited if seen_ids and eid not in seen_ids]
    with_spans = sum(1 for item in items if item.get("evidence_spans"))
    return {
        "item_count": len(items),
        "section_counts": {s.get("title", "?"): len(s.get("items", [])) for s in sections},
        "span_coverage": round(with_spans / len(items), 4) if items else 0.0,
        "evidence_ids_unverifiable": len(unverifiable),
        "evidence_ids_checked": bool(seen_ids),
    }


def compare_metrics(
    actual: Dict[str, Any], baseline: Dict[str, Any], *, coverage_tol: float = 0.05
) -> List[str]:
    """Return a list of regression messages (empty == within tolerance)."""
    regressions: List[str] = []
    if actual["item_count"] < baseline.get("item_count", 0):
        regressions.append(
            f"item_count regressed: {actual['item_count']} < {baseline.get('item_count')}"
        )
    if actual["span_coverage"] < baseline.get("span_coverage", 0.0) - coverage_tol:
        regressions.append(
            f"span_coverage regressed: {actual['span_coverage']} < "
            f"{baseline.get('span_coverage')} - {coverage_tol}"
        )
    if actual["evidence_ids_unverifiable"] > baseline.get("evidence_ids_unverifiable", 0):
        regressions.append(
            f"evidence_ids_unverifiable rose: {actual['evidence_ids_unverifiable']} > "
            f"{baseline.get('evidence_ids_unverifiable')}"
        )
    return regressions


def run_case(case: Case, out_dir: Path, *, validate_citations: bool = False) -> Dict[str, Any]:
    """Run the pipeline over a frozen case with the synthetic extractor; return metrics."""
    import digest_core.run as run_module

    SyntheticExtractor.seen_evidence_ids = set()
    swaps = {
        "LLMGateway": SyntheticExtractor,
        "start_health_server": lambda *a, **k: None,
        "MetricsCollector": _NullMetrics,
        "MattermostDeliverer": _NullDeliverer,
    }
    originals = {name: getattr(run_module, name) for name in swaps}
    for name, value in swaps.items():
        setattr(run_module, name, value)
    try:
        run_module.run_digest(
            from_date=case.digest_date,
            sources=["ews"],
            out=str(out_dir),
            model="qwen35-397b-a17b",
            window="calendar_day",
            state=str(out_dir / "state"),  # isolate cross-run memory per case
            force=True,
            validate_citations=validate_citations,
            replay_ingest=str(case.snapshot_path),
        )
    finally:
        for name, value in originals.items():
            setattr(run_module, name, value)

    digest_path = out_dir / f"digest-{case.digest_date}.json"
    digest_json = json.loads(digest_path.read_text(encoding="utf-8"))
    return compute_metrics(digest_json, set(SyntheticExtractor.seen_evidence_ids))


def evaluate_corpus(
    cases: List[Case], out_root: Path, *, update_baseline: bool = False
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Run every case; compare to (or update) its baseline. Returns (ok, reports)."""
    reports: List[Dict[str, Any]] = []
    all_ok = True
    for case in cases:
        metrics = run_case(case, out_root / case.name)
        if update_baseline:
            case.baseline_path.write_text(
                json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            reports.append({"case": case.name, "metrics": metrics, "updated_baseline": True})
            continue
        baseline = _load_baseline(case.baseline_path)
        regressions = (
            compare_metrics(metrics, baseline)
            if baseline is not None
            else ["no committed baseline (run with --update-baseline)"]
        )
        ok = not regressions
        all_ok = all_ok and ok
        reports.append(
            {"case": case.name, "metrics": metrics, "ok": ok, "regressions": regressions}
        )
    return all_ok, reports


def _load_baseline(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class _NullMetrics:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _NullDeliverer:
    def __init__(self, config):
        pass

    def deliver_digest(self, digest, json_path=None, **kwargs):
        return {"status": "skipped", "parts": 0}
