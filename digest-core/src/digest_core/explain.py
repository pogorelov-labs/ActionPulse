"""LLM-powered failure explanation (roadmap U7).

"Something went wrong, but the LLM endpoint works" → collect the run's own
telemetry (trace meta + the redacted log tail), send ONE compact JSON-mode
request through the existing :class:`LLMGateway`, print a short
cause/explanation/next-steps card.

Design decisions (recorded in TERMINAL_DESIGN_ROADMAP.md U7):

- **No agent CLI under the hood.** The corp gateway is the only reachable
  LLM (ADR-012); a third-party headless agent would receive LLM_TOKEN and
  bring tool-use we don't need for a deterministic collect→ask→print step.
  The gateway already owns retries, 429 penalties, auth classification and
  rate limiting — the explainer rides it on its own ``explain`` stage budget.
- **Separate from the pipeline budget** (ADR-008): `explain` is its own
  command with its own RateBroker — it never competes with a run's 2 calls.
- **Privacy:** the prompt embeds run telemetry only — log lines are redacted
  at write time, the meta is sanitized at write time, and email bodies are
  never part of either. Corp-only by nature; offline it fails fast.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from digest_core.config import Config
from digest_core.diagnostics import _find_metadata
from digest_core.llm.gateway import LLMGateway
from digest_core.llm.rate_broker import RateBroker

logger = structlog.get_logger()

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_VERSION = "explain_failure.v1"

#: A verdict is short by contract (<~150 words) — no reason to allow more.
EXPLAIN_MAX_OUTPUT_TOKENS = 700
#: One logical call + headroom for the gateway's internal transient retry.
EXPLAIN_CALL_BUDGET = 2
#: Log tail size: enough to see the failure context, small enough to stay
#: far inside the context window alongside the meta excerpt.
LOG_TAIL_LINES = 80

#: run_meta keys that go to the model — an explicit whitelist, never the
#: whole document (config_sanitized alone would dwarf the useful signal).
_META_WHITELIST = (
    "status",
    "partial",
    "error",
    "digest_date",
    "trace_id",
    "degraded_stages",
    "stage_health",
    "stage_durations_ms",
    "pipeline_metrics",
    "llm_request_trace",
    "llm_budget",
    "ews_fetch_stats",
    "delivery_receipt",
    "skip_reason",
)


class ExplainUnavailable(RuntimeError):
    """The explanation could not be produced (no meta, or no LLM reachable)."""


@dataclass
class ExplainResult:
    likely_cause: str
    explanation: str
    next_steps: List[str]
    model: str
    trace_id: str
    digest_date: str
    status: str
    log_file: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


def collect_log_tail(meta: Dict[str, Any], max_lines: int = LOG_TAIL_LINES) -> str:
    """Last lines of the run's structured log (redacted at write time)."""
    log_file = meta.get("log_file")
    if not log_file:
        return ""
    path = Path(log_file)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])


def build_user_payload(meta: Dict[str, Any], log_tail: str) -> str:
    """The compact JSON document the model sees: whitelisted meta + log tail."""
    excerpt = {key: meta[key] for key in _META_WHITELIST if key in meta}
    provenance = meta.get("provenance") or {}
    excerpt["pipeline_version"] = provenance.get("pipeline_version") or meta.get("pipeline_version")
    payload = {"run_meta": excerpt, "log_tail": log_tail}
    return json.dumps(payload, ensure_ascii=False, indent=1)


def _system_prompt(language: str) -> str:
    prompt = (PROMPTS_DIR / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    if language == "ru":
        prompt += "\nAnswer in Russian (keys stay English; values in Russian).\n"
    return prompt


def find_run_meta(trace_id: Optional[str] = None, date: Optional[str] = None) -> Dict[str, Any]:
    """Locate the run to explain (newest by default); raises ExplainUnavailable."""
    try:
        meta_path = _find_metadata(trace_id=trace_id, date=date)
    except FileNotFoundError as exc:
        raise ExplainUnavailable(
            "No run metadata found — run `actionpulse run` first"
            + (f" (looked for {trace_id or date})" if (trace_id or date) else "")
        ) from exc
    return json.loads(meta_path.read_text(encoding="utf-8"))


def explain_run(
    trace_id: Optional[str] = None,
    date: Optional[str] = None,
    config: Optional[Config] = None,
) -> ExplainResult:
    """Collect telemetry for a run and ask the corp LLM for a short verdict."""
    meta = find_run_meta(trace_id=trace_id, date=date)
    config = config or Config()
    language = config.report.language

    explain_llm = config.llm.model_copy(update={"max_output_tokens": EXPLAIN_MAX_OUTPUT_TOKENS})
    broker = RateBroker.from_config(config.llm, stage_call_budgets={"explain": EXPLAIN_CALL_BUDGET})
    gateway = LLMGateway(explain_llm, rate_broker=broker, stage="explain")
    try:
        verdict = gateway.judge(
            _system_prompt(language),
            build_user_payload(meta, collect_log_tail(meta)),
            trace_id="explain",
        )
    except Exception as exc:  # offline / auth / transport — fail fast, honestly
        raise ExplainUnavailable(
            f"The LLM gateway did not answer ({type(exc).__name__}): explain needs the"
            " corp network (ADR-012). The raw telemetry is still available via"
            " `actionpulse diagnose` and the trace-*.meta.json file."
        ) from exc
    finally:
        gateway.close()

    steps = verdict.get("next_steps")
    if isinstance(steps, str):
        steps = [steps]
    return ExplainResult(
        likely_cause=str(verdict.get("likely_cause") or "").strip()
        or "The model returned no cause — telemetry may be inconclusive.",
        explanation=str(verdict.get("explanation") or "").strip(),
        next_steps=[str(step) for step in (steps or []) if str(step).strip()],
        model=explain_llm.model,
        trace_id=str(meta.get("trace_id", "")),
        digest_date=str(meta.get("digest_date", "")),
        status=str(meta.get("status", "")),
        log_file=meta.get("log_file"),
        raw=verdict,
    )
