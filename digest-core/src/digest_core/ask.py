"""Ask-your-inbox: retrieval-augmented Q&A over the encrypted message store (U8).

The store (ADR-014) made this buildable offline: retrieve with its hybrid search,
then ask the corp gateway ONE grounded, cited question on its own budget — the same
"collect → ask → print" shape as ``explain`` (no agent CLI, ADR-012). Extract-over-
Generate (P1): the model answers ONLY from the retrieved passages and cites message ids.

Privacy: DM bodies are redacted at rest and never chunked (guardrail #9), so they are
never retrievable here. The question + the retrieved passages go to the gateway exactly
as evidence does during extraction. Corp-only by nature; offline it fails fast.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from digest_core.config import Config
from digest_core.llm.gateway import LLMGateway
from digest_core.llm.rate_broker import RateBroker

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_VERSION = "ask_inbox.v1"

#: A grounded answer is short by contract.
ASK_MAX_OUTPUT_TOKENS = 600
#: One logical call + headroom for the gateway's internal transient retry.
ASK_CALL_BUDGET = 2
#: How many retrieved passages to ground the answer on.
DEFAULT_TOP_K = 8


class AskUnavailable(RuntimeError):
    """The answer could not be produced (store empty, or no LLM reachable)."""


@dataclass
class Citation:
    message_id: str
    quote: str


@dataclass
class AskResult:
    question: str
    answer: str
    answered: bool
    citations: List[Citation]
    passages: List[Dict[str, Any]]
    model: str
    raw: Dict[str, Any] = field(default_factory=dict)


def _system_prompt(language: str) -> str:
    prompt = (PROMPTS_DIR / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    if language == "ru":
        prompt += "\nAnswer in Russian (JSON keys stay English; values in Russian).\n"
    return prompt


def _user_payload(question: str, passages: List[Dict[str, Any]]) -> str:
    numbered = [{"n": i + 1, **p} for i, p in enumerate(passages)]
    return json.dumps({"question": question, "passages": numbered}, ensure_ascii=False, indent=1)


def _ask_passages(
    question: str,
    passages: List[Dict[str, Any]],
    *,
    config: Config,
    system_prompt: str,
) -> AskResult:
    """Ask the corp LLM ONE grounded, cited question over ready-made ``passages``.

    The shared gateway/budget/parse core behind both ``answer_question`` (passages
    from a search) and ``summarize_passages`` (passages from a thread). Raises
    ``AskUnavailable`` when the gateway can't answer (offline/auth).
    """
    if not passages:
        return AskResult(
            question=question,
            answer="I don't see anything about that in your messages.",
            answered=False,
            citations=[],
            passages=[],
            model=config.llm.model,
        )

    ask_llm = config.llm.model_copy(update={"max_output_tokens": ASK_MAX_OUTPUT_TOKENS})
    broker = RateBroker.from_config(config.llm, stage_call_budgets={"ask": ASK_CALL_BUDGET})
    gateway = LLMGateway(ask_llm, rate_broker=broker, stage="ask")
    try:
        verdict = gateway.judge(
            system_prompt,
            _user_payload(question, passages),
            trace_id="ask",
        )
    except Exception as exc:  # offline / auth / transport — fail fast, honestly
        raise AskUnavailable(
            f"The LLM gateway did not answer ({type(exc).__name__}): `ask` needs the corp "
            "network (ADR-012). Keyword search still works offline: `actionpulse search`."
        ) from exc
    finally:
        gateway.close()

    answer = str(verdict.get("answer") or "").strip()
    citations = [
        Citation(
            message_id=str(c.get("message_id", "")).strip(), quote=str(c.get("quote", "")).strip()
        )
        for c in (verdict.get("citations") or [])
        if isinstance(c, dict) and c.get("message_id")
    ]
    answered = bool(verdict.get("answered", bool(answer)))
    return AskResult(
        question=question,
        answer=answer or "I don't see anything about that in your messages.",
        answered=answered,
        citations=citations,
        passages=passages,
        model=ask_llm.model,
        raw=verdict,
    )


def answer_question(
    store: Any,
    question: str,
    *,
    backend: Any = None,
    config: Optional[Config] = None,
    mode: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    source: Optional[str] = None,
    since: Optional[str] = None,
) -> AskResult:
    """Retrieve from ``store`` and ask the corp LLM for a grounded, cited answer.

    ``backend`` is the embedding client for semantic/hybrid retrieval (keyword needs
    none). Raises ``AskUnavailable`` when the gateway can't answer (offline/auth).
    """
    config = config or Config()
    mode = mode or config.store.search_default_mode
    hits = store.search(
        question, mode=mode, backend=backend, source=source, since=since, limit=top_k
    )
    passages = store.context_passages(hits) if hits else []
    return _ask_passages(
        question, passages, config=config, system_prompt=_system_prompt(config.report.language)
    )


SUMMARIZE_PROMPT_VERSION = "summarize_thread.v1"
_SUMMARIZE_QUESTION = (
    "Summarize this thread: the key points, any decisions, and what (if anything) is "
    "awaited from me."
)


def _summarize_system_prompt(language: str) -> str:
    prompt = (PROMPTS_DIR / f"{SUMMARIZE_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    if language == "ru":
        prompt += "\nAnswer in Russian (JSON keys stay English; values in Russian).\n"
    return prompt


def summarize_passages(
    passages: List[Dict[str, Any]], *, config: Optional[Config] = None
) -> AskResult:
    """Summarize a thread's ready-made ``passages`` via the corp LLM (one grounded call)."""
    config = config or Config()
    return _ask_passages(
        _SUMMARIZE_QUESTION,
        passages,
        config=config,
        system_prompt=_summarize_system_prompt(config.report.language),
    )
