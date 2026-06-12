"""
Main digest pipeline runner.

The pipeline has 8 stages: INGEST → NORMALIZE → THREADS → EVIDENCE
→ SELECT → LLM → ASSEMBLE → DELIVER.

RunContext carries shared state between stages. Each _stage_* function
is a self-contained unit that reads from / writes to the context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence
import uuid

import structlog

from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.config import Config, RankerConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.eval.judge import LLMJudge
from digest_core.evidence.citation_gate import CitationGate, support_recall
from digest_core.llm.best_of_n import best_of_n_meta, select_best_candidate
from digest_core.evidence.citations import CitationBuilder, CitationValidator
from digest_core.evidence.repair import repair_weak_items
from digest_core.evidence.split import EvidenceChunk, EvidenceSplitter
from digest_core.ingest.ews import EWSIngest, NormalizedMessage
from digest_core.llm.fleet import RerankerClient
from digest_core.llm.gateway import LLMAuthError, LLMGateway
from digest_core.llm.prompt_registry import get_prompt_template_path
from digest_core.llm.rate_broker import RateBroker
from digest_core.llm.schemas import Digest, Section
from digest_core.select.ranker import DigestRanker
from digest_core.normalize.html import HTMLNormalizer
from digest_core.normalize.quotes import QuoteCleaner
from digest_core.observability.healthz import start_health_server
from digest_core.observability.logs import setup_logging
from digest_core.observability import tracing
from digest_core.assemble.labels import (
    DEFAULT_LANGUAGE,
    STATUS,
    UNCONFIRMED,
    normalize_section,
    report_strings,
    section_sort_weight,
    section_title,
    stage_banner,
)
from digest_core.progress import NullSink, ProgressSink
from digest_core.provenance import build_provenance, prompt_sha256
from digest_core.observability.metrics import MetricsCollector
from digest_core.select.context import ContextSelector
from digest_core.threads.build import ThreadBuilder

PIPELINE_VERSION = "1.1.0"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PACKAGE_ROOT / "prompts"
# Deprecated compat aliases (RU titles). Logic routes through assemble.labels
# canonical keys; these stay importable for existing tests/integrations.
SECTION_ORDER = {"Мои действия": 0, "Срочное": 1, "К сведению": 2, "Не подтверждено": 3}
QUARANTINE_SECTION = "Не подтверждено"
# A repair verdict is a tiny JSON object; no reason to let the judge model
# spend the extractor-sized output ceiling.
JUDGE_MAX_OUTPUT_TOKENS = 256

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Run result (CLI and programmatic callers)
# ---------------------------------------------------------------------------


@dataclass
class RunDigestResult:
    """Outcome of ``run_digest`` (full pipeline, non-dry-run)."""

    pipeline_succeeded: bool = True
    citation_validation_ok: bool = True
    # PR11: support-recall gate. citation_validation_ok = support_recall >= recall_floor.
    support_recall: float = 1.0
    recall_floor: float = 0.0
    items_weak: int = 0
    items_repaired: int = 0

    def __bool__(self) -> bool:
        """Truthiness matches pipeline success (use ``assert run_digest(...)``, not ``is True``)."""
        return self.pipeline_succeeded


# ---------------------------------------------------------------------------
# RunContext: carries shared state between pipeline stages
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    """Mutable context threaded through all pipeline stages."""

    trace_id: str
    config: Config
    metrics: MetricsCollector
    digest_date: str
    output_dir: Path
    json_path: Path
    md_path: Path
    metadata_path: Path
    dry_run: bool
    force: bool
    validate_citations: bool
    dump_ingest: str | None
    replay_ingest: str | None
    record_llm: str | None
    replay_llm: str | None
    rate_broker: Optional[RateBroker] = None
    log_file: Any = None
    run_meta: Dict[str, Any] = field(default_factory=dict)
    sink: ProgressSink = field(default_factory=NullSink)


# ---------------------------------------------------------------------------
# Public API (unchanged signatures)
# ---------------------------------------------------------------------------


def run_digest(
    from_date: str,
    sources: List[str],
    out: str,
    model: str,
    window: str,
    state: str | None,
    validate_citations: bool = False,
    force: bool = False,
    dump_ingest: str | None = None,
    replay_ingest: str | None = None,
    record_llm: str | None = None,
    replay_llm: str | None = None,
    sink: ProgressSink | None = None,
) -> RunDigestResult:
    """Run the complete digest pipeline."""
    return _run_pipeline(
        from_date=from_date,
        sources=sources,
        out=out,
        model=model,
        window=window,
        state=state,
        validate_citations=validate_citations,
        dry_run=False,
        force=force,
        dump_ingest=dump_ingest,
        replay_ingest=replay_ingest,
        record_llm=record_llm,
        replay_llm=replay_llm,
        sink=sink,
    )


def run_digest_dry_run(
    from_date: str,
    sources: List[str],
    out: str,
    model: str,
    window: str,
    state: str | None,
    validate_citations: bool = False,
    force: bool = False,
    dump_ingest: str | None = None,
    replay_ingest: str | None = None,
    record_llm: str | None = None,
    replay_llm: str | None = None,
    sink: ProgressSink | None = None,
) -> None:
    """Run the pipeline up to context selection without LLM or delivery."""
    _run_pipeline(
        from_date=from_date,
        sources=sources,
        out=out,
        model=model,
        window=window,
        state=state,
        validate_citations=validate_citations,
        dry_run=True,
        force=force,
        dump_ingest=dump_ingest,
        replay_ingest=replay_ingest,
        record_llm=record_llm,
        replay_llm=replay_llm,
        sink=sink,
    )


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _init_context(
    *,
    from_date: str,
    out: str,
    model: str,
    window: str,
    state: str | None,
    validate_citations: bool,
    dry_run: bool,
    force: bool,
    dump_ingest: str | None,
    replay_ingest: str | None,
    record_llm: str | None,
    replay_llm: str | None,
    sink: ProgressSink | None = None,
) -> RunContext:
    """Build RunContext with resolved config, paths, and initial metadata."""
    trace_id = str(uuid.uuid4())
    log_file = setup_logging()

    config = Config()
    if model:
        config.llm.model = model
    if window in ("calendar_day", "rolling_24h"):
        config.time.window = window
    if state:
        state_dir = Path(state).expanduser()
        state_dir.mkdir(parents=True, exist_ok=True)
        config.ews.sync_state_path = str(state_dir / Path(config.ews.sync_state_path).name)

    metrics = MetricsCollector(
        config.observability.prometheus_port,
        fail_on_exporter_error=config.observability.fail_on_exporter_error,
    )
    start_health_server(port=9109, llm_config=config.llm)

    digest_date = _resolve_digest_date(from_date)
    output_dir = Path(out).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"digest-{digest_date}.json"
    md_path = output_dir / f"digest-{digest_date}.md"
    metadata_path = output_dir / f"trace-{trace_id}.meta.json"

    run_meta: Dict[str, Any] = {
        "trace_id": trace_id,
        "pipeline_version": PIPELINE_VERSION,
        "digest_date": digest_date,
        "dry_run": dry_run,
        "validate_citations": validate_citations,
        "citation_validation_ok": True,
        "log_file": str(log_file) if log_file else None,
        "output_dir": str(output_dir),
        "artifact_paths": {"json": str(json_path), "md": str(md_path)},
        "stage_durations_ms": {},
        "pipeline_metrics": {},
        "evidence_summary": {},
        "ews_fetch_stats": {},
        "llm_request_trace": {},
        "metrics_exporter": _exporter_status_entry(metrics),
        "config_sanitized": _sanitize_config(config),
        "provenance": build_provenance(
            config, config_sha256=_config_sha256(config), pipeline_version=PIPELINE_VERSION
        ),
        "status": "started",
        "partial": False,
    }

    # OTel GenAI tracing (EP-8): off by default; structural attributes only.
    provenance_manifest = run_meta["provenance"]
    otel_active = tracing.configure_tracing(
        config.observability,
        {
            "service.version": PIPELINE_VERSION,
            "actionpulse.code_sha": provenance_manifest["code_sha"],
            "actionpulse.config_sha256": provenance_manifest["config_sha256"],
        },
    )
    if otel_active:
        tracing.start_run_span(trace_id, digest_date)
    run_meta["otel"] = {
        "enabled": otel_active,
        "export": (config.observability.otel_export_path or "console") if otel_active else None,
    }

    # One RateBroker per run, shared by the LLM gateway and (later) fleet clients (R1).
    rate_broker = RateBroker(
        fleet_rpm=config.llm.fleet_rpm,
        burst=config.llm.fleet_burst,
        default_rpm=config.llm.rate_limit_rpm,
        stage_call_budgets=config.llm.stage_call_budgets,
    )

    return RunContext(
        sink=sink or NullSink(),
        trace_id=trace_id,
        config=config,
        metrics=metrics,
        digest_date=digest_date,
        output_dir=output_dir,
        json_path=json_path,
        md_path=md_path,
        metadata_path=metadata_path,
        dry_run=dry_run,
        force=force,
        validate_citations=validate_citations,
        dump_ingest=dump_ingest,
        replay_ingest=replay_ingest,
        record_llm=record_llm,
        replay_llm=replay_llm,
        rate_broker=rate_broker,
        log_file=log_file,
        run_meta=run_meta,
    )


def _stage_ingest(ctx: RunContext) -> List[NormalizedMessage]:
    """Stage 1+2: INGEST (+ NORMALIZE for live mode).

    Replay mode returns already-normalized messages.
    Live mode fetches from EWS then normalizes.
    Also handles --dump-ingest snapshot writing.
    """
    if ctx.replay_ingest:
        replay_start = time.perf_counter()
        _emit(ctx, "on_stage_start", "ingest")
        messages = _load_ingest_snapshot(Path(ctx.replay_ingest).expanduser())
        _finish_stage(ctx, "ingest", replay_start, messages=len(messages))
        ctx.run_meta["ews_fetch_stats"] = {
            "source": "replay",
            "message_count": len(messages),
            "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
        }
    else:
        ingest_start = time.perf_counter()
        _emit(ctx, "on_stage_start", "ingest")
        ingest = EWSIngest(ctx.config.ews, time_config=ctx.config.time, metrics=ctx.metrics)
        messages = ingest.fetch_messages(ctx.digest_date, ctx.config.time)
        ctx.metrics.record_emails_total(len(messages), "fetched")
        _finish_stage(ctx, "ingest", ingest_start, messages=len(messages))
        ctx.run_meta["ews_fetch_stats"] = {
            "source": "ews",
            "message_count": len(messages),
            "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        normalize_start = time.perf_counter()
        _emit(ctx, "on_stage_start", "normalize")
        messages = _normalize_messages(messages, ctx.config)
        _finish_stage(ctx, "normalize", normalize_start, messages=len(messages))

    if ctx.dump_ingest:
        snapshot_path = Path(ctx.dump_ingest).expanduser()
        _dump_ingest_snapshot(snapshot_path, messages, ctx.digest_date)

    return messages


def _stage_threads(ctx: RunContext, messages: List[NormalizedMessage]) -> list:
    """Stage 3: THREADS — group messages into conversation threads."""
    threads_start = time.perf_counter()
    _emit(ctx, "on_stage_start", "threads")
    thread_builder = ThreadBuilder()
    threads = thread_builder.build_threads(messages)
    _finish_stage(ctx, "threads", threads_start, messages=len(messages), threads=len(threads))
    return threads


def _stage_evidence(ctx: RunContext, threads: list, total_emails: int) -> List[EvidenceChunk]:
    """Stage 4: EVIDENCE — split threads into budget-constrained chunks."""
    evidence_start = time.perf_counter()
    _emit(ctx, "on_stage_start", "evidence")
    evidence_splitter = EvidenceSplitter(
        user_aliases=ctx.config.ews.user_aliases,
        user_timezone=ctx.config.time.user_timezone,
        context_budget_config=ctx.config.context_budget,
        chunking_config=ctx.config.chunking,
        important_senders=ctx.config.ranker.important_senders,
    )
    evidence_chunks = evidence_splitter.split_evidence(
        threads,
        total_emails=total_emails,
        total_threads=len(threads),
    )
    _finish_stage(
        ctx, "evidence", evidence_start, threads=len(threads), chunks=len(evidence_chunks)
    )
    return evidence_chunks


def _stage_select(
    ctx: RunContext, evidence_chunks: List[EvidenceChunk]
) -> tuple[List[EvidenceChunk], Dict[str, Any]]:
    """Stage 5: SELECT — rank and filter evidence for the LLM context window."""
    select_start = time.perf_counter()
    _emit(ctx, "on_stage_start", "select")
    context_selector = ContextSelector(
        buckets_config=ctx.config.selection_buckets,
        weights_config=ctx.config.selection_weights,
        context_budget_config=ctx.config.context_budget,
        shrink_config=ctx.config.shrink,
    )
    selected_evidence = context_selector.select_context(evidence_chunks)
    selection_metrics = context_selector.get_metrics()
    _finish_stage(
        ctx, "select", select_start, selected=len(selected_evidence), of=len(evidence_chunks)
    )
    return selected_evidence, selection_metrics


def _stage_llm(
    ctx: RunContext,
    selected_evidence: List[EvidenceChunk],
    normalized_messages: Sequence[NormalizedMessage] = (),
) -> tuple[Digest, Optional[Exception]]:
    """Stage 6: LLM — extract actions from evidence via the LLM gateway.

    Returns (digest, llm_error). On LLM failure, returns a partial digest
    instead of raising. With ``extract.best_of_n > 1`` (default 1 = off) and a
    successful deterministic extraction, candidates 2..N are sampled and the
    citation gate selects the winner (EP-10).
    """
    llm_gateway = LLMGateway(
        ctx.config.llm,
        metrics=ctx.metrics,
        record_llm=ctx.record_llm,
        replay_llm=ctx.replay_llm,
        rate_broker=ctx.rate_broker,
    )
    llm_stage_start = time.perf_counter()
    _emit(ctx, "on_stage_start", "llm")

    if not selected_evidence:
        digest = _build_empty_digest(ctx.digest_date, ctx.trace_id, prompt_version="none")
        llm_error = None
    else:
        prompt_version, prompt_text = _load_extract_prompt(
            ctx.config.llm.model, ctx.config.report.language
        )
        provenance = ctx.run_meta.get("provenance")
        if isinstance(provenance, dict):
            provenance["prompt_id"] = prompt_version
            provenance["prompt_sha256"] = prompt_sha256(prompt_text)
        try:
            _emit(ctx, "on_llm_attempt", ctx.config.llm.model, 1, 2)
            llm_response = llm_gateway.extract_actions(
                evidence=selected_evidence,
                prompt_template=prompt_text,
                trace_id=ctx.trace_id,
            )
            digest = Digest(
                schema_version="1.0",
                prompt_version=prompt_version,
                digest_date=ctx.digest_date,
                trace_id=ctx.trace_id,
                sections=_sort_sections(llm_response.get("sections", [])),
            )
            llm_error = None
        except Exception as exc:
            auth_failure = isinstance(exc, LLMAuthError)
            ctx.metrics.record_degradation("llm_auth_failed" if auth_failure else "llm_failed")
            ctx.run_meta["partial"] = True
            ctx.run_meta["status"] = "partial"
            digest = _build_partial_digest(
                digest_date=ctx.digest_date,
                trace_id=ctx.trace_id,
                error_message=str(exc),
                language=ctx.config.report.language,
                title=(
                    report_strings(ctx.config.report.language)["banner_llm_auth"]
                    if auth_failure
                    else None
                ),
            )
            llm_error = exc
            logger.warning(
                "LLM stage failed after retries, writing partial digest",
                trace_id=ctx.trace_id,
                error=str(exc),
            )

    # EP-10: best-of-N sampling, only after a SUCCESSFUL deterministic
    # candidate — a partial digest is never "improved" by sampling, and every
    # sampling failure degrades back to the deterministic candidate.
    if llm_error is None and selected_evidence and ctx.config.extract.best_of_n > 1:
        digest = _sample_and_select(
            ctx, digest, selected_evidence, prompt_version, prompt_text, normalized_messages
        )

    _finish_stage(
        ctx,
        "llm",
        llm_stage_start,
        sections=len(digest.sections),
        items=_count_digest_items(digest),
    )

    # Record LLM trace metadata
    llm_meta = llm_gateway.get_request_stats()
    llm_trace = dict(getattr(llm_gateway, "last_request_meta", {}))
    llm_trace.update(
        {
            "model": llm_meta.get("model"),
            "latency_ms": llm_meta.get("last_latency_ms", 0),
            "timeout_s": llm_meta.get("timeout_s"),
        }
    )
    if llm_error is not None:
        llm_trace["error"] = str(llm_error)
    ctx.run_meta["llm_request_trace"] = llm_trace

    # ADR-008 v2 visibility clause (D6): an invisible budget is not a budget.
    budget = _llm_budget_summary(llm_trace, ctx.config.llm)
    ctx.run_meta["llm_budget"] = budget
    logger.info("LLM budget", trace_id=ctx.trace_id, **budget)
    try:
        ctx.metrics.record_llm_latency(llm_meta.get("last_latency_ms", 0) or 0)
        ctx.metrics.record_llm_tokens(
            int(llm_trace.get("tokens_in", 0)), int(llm_trace.get("tokens_out", 0))
        )
    except Exception:
        pass

    return digest, llm_error


def _sample_and_select(
    ctx: RunContext,
    primary_digest: Digest,
    selected_evidence: List[EvidenceChunk],
    prompt_version: str,
    prompt_text: str,
    normalized_messages: Sequence[NormalizedMessage],
) -> Digest:
    """EP-10: sample candidates 2..N and let the citation gate pick the winner.

    Candidate 1 is the deterministic extraction already in hand; samples run at
    ``extract.sample_temperature`` on the extractor's own RPM bucket and stage
    budget (ADR-008 v2: raise ``llm.stage_call_budgets.extractor`` alongside the
    flag — with the default budget, sampling degrades back to N=1). Disabled
    under ``--replay-llm`` (recordings carry no sample channel — EP-14 design
    item, same precedent as the fleet sidecar/judge channels). Any sampling
    failure stops the loop and selection proceeds over the candidates gathered;
    ties prefer the deterministic candidate.
    """
    if ctx.replay_llm:
        logger.warning(
            "Best-of-N sampling disabled under --replay-llm: no sample channel in recordings",
            trace_id=ctx.trace_id,
        )
        return primary_digest

    candidates = [primary_digest]
    sample_llm = ctx.config.llm.model_copy(
        update={"temperature": ctx.config.extract.sample_temperature}
    )
    sample_gateway = LLMGateway(
        sample_llm,
        metrics=ctx.metrics,
        record_llm=ctx.record_llm,
        rate_broker=ctx.rate_broker,
        stage="extractor",
    )
    try:
        for candidate_no in range(2, ctx.config.extract.best_of_n + 1):
            try:
                response = sample_gateway.extract_actions(
                    evidence=selected_evidence,
                    prompt_template=prompt_text,
                    trace_id=ctx.trace_id,
                )
                candidates.append(
                    Digest(
                        schema_version="1.0",
                        prompt_version=prompt_version,
                        digest_date=ctx.digest_date,
                        trace_id=ctx.trace_id,
                        sections=_sort_sections(response.get("sections", [])),
                    )
                )
            except Exception as exc:
                # Budget exhausted / transport failure: stop sampling, keep
                # what we have (degrade-not-drop — never worse than N=1).
                logger.warning(
                    "Best-of-N sampling stopped; selecting among gathered candidates",
                    candidate=candidate_no,
                    error_type=type(exc).__name__,
                    trace_id=ctx.trace_id,
                )
                break
    finally:
        sample_gateway.close()

    if len(candidates) == 1:
        return primary_digest

    msg_map = {m.msg_id: m.text_body for m in normalized_messages if m.msg_id}
    selected, scores = select_best_candidate(candidates, msg_map)
    summary = best_of_n_meta(scores, selected)
    ctx.run_meta["best_of_n"] = summary
    logger.info("Best-of-N selection", trace_id=ctx.trace_id, **summary)
    return candidates[selected]


def _ranker_weights_from_config(ranker_cfg: RankerConfig) -> Dict[str, float]:
    return {
        "user_in_to": ranker_cfg.weight_user_in_to,
        "user_in_cc": ranker_cfg.weight_user_in_cc,
        "has_action": ranker_cfg.weight_has_action,
        "has_mention": ranker_cfg.weight_has_mention,
        "has_due_date": ranker_cfg.weight_has_due_date,
        "sender_importance": ranker_cfg.weight_sender_importance,
        "thread_length": ranker_cfg.weight_thread_length,
        "recency": ranker_cfg.weight_recency,
        "has_attachments": ranker_cfg.weight_has_attachments,
        "has_project_tag": ranker_cfg.weight_has_project_tag,
    }


def _ranker_user_aliases(config: Config) -> List[str]:
    aliases = [a for a in (config.ews.user_aliases or []) if a]
    upn = (config.ews.user_upn or "").strip()
    if upn and upn not in aliases:
        aliases.append(upn)
    return aliases


def _maybe_rank_digest(
    ctx: RunContext, digest: Digest, selected_evidence: List[EvidenceChunk]
) -> Digest:
    if not ctx.config.ranker.enabled:
        return digest
    rc = ctx.config.ranker
    ranker = DigestRanker(
        weights=_ranker_weights_from_config(rc),
        user_aliases=_ranker_user_aliases(ctx.config),
        important_senders=rc.important_senders,
    )
    new_sections: List[Section] = []
    for section in digest.sections:
        ranked = ranker.rank_items(list(section.items), selected_evidence)
        new_sections.append(Section(title=section.title, items=ranked))
        if rc.log_positions:
            logger.info(
                "Ranked digest section",
                trace_id=ctx.trace_id,
                section_title=section.title,
                order=[getattr(i, "evidence_id", None) for i in ranked],
            )
    return digest.model_copy(update={"sections": new_sections})


def _apply_citation_validation(
    digest: Digest,
    normalized_messages: Sequence[NormalizedMessage],
    selected_evidence: Sequence[EvidenceChunk],
) -> tuple[Digest, bool]:
    """Rebuild citations from selected evidence and validate offsets (strict)."""
    msg_map = {m.msg_id: m.text_body for m in normalized_messages if m.msg_id}
    builder = CitationBuilder(msg_map)
    validator = CitationValidator(msg_map)

    needs_citations = False
    for section in digest.sections:
        for item in section.items:
            if item.evidence_id != "system":
                needs_citations = True
                break
        if needs_citations:
            break

    if not needs_citations:
        return digest, True

    all_ok = True
    new_sections: List[Section] = []
    for section in digest.sections:
        new_items = []
        for item in section.items:
            if item.evidence_id == "system":
                new_items.append(item)
                continue
            chunks = [c for c in selected_evidence if c.evidence_id == item.evidence_id]
            if not chunks:
                all_ok = False
                new_items.append(item)
                continue
            citations = []
            for ch in chunks:
                cit = builder.build_citation(ch)
                if cit:
                    citations.append(cit)
            if not citations:
                all_ok = False
                new_items.append(item)
                continue
            if not validator.validate_citations(citations, strict=False):
                all_ok = False
            new_items.append(item.model_copy(update={"citations": citations}))
        new_sections.append(Section(title=section.title, items=new_items))

    return digest.model_copy(update={"sections": new_sections}), all_ok


def _post_llm_digest_enrichment(
    ctx: RunContext,
    digest: Digest,
    normalized_messages: List[NormalizedMessage],
    selected_evidence: List[EvidenceChunk],
) -> tuple[Digest, bool, float, float, int, int]:
    """Build citations, rank, run the P2 gate, repair weak items, apply the floor.

    Returns (digest, citation_ok, support_recall, recall_floor, items_weak,
    items_repaired). PR11: a single bad offset no longer flips the gate — exit 2
    fires only when support_recall < recall_floor (default 0.0 -> never).
    """
    if ctx.validate_citations:
        digest, offsets_ok = _apply_citation_validation(
            digest, normalized_messages, selected_evidence
        )
        if not offsets_ok:
            ctx.metrics.record_citation_validation_failure("post_llm_offsets")

    digest = _maybe_rank_digest(ctx, digest, selected_evidence)
    digest = _apply_shadow_citation_gate(ctx, digest, normalized_messages)

    # PR11 + EP-12 part 2: non-generative repair. With judge.enabled (default
    # OFF) a cross-model judge approves re-selected verbatim spans — D1's
    # quarantine gets its rescue path. Judge off/unavailable -> no-op; weak
    # items keep their badge and are delivered (degrade-not-drop), never dropped.
    msg_map = {m.msg_id: m.text_body for m in normalized_messages if m.msg_id}
    gate = CitationGate(msg_map, config=ctx.config.reranker)
    judge, judge_gateway = _build_repair_judge(ctx)
    try:
        outcome = repair_weak_items(
            digest,
            msg_map,
            gate=gate,
            tau_repair=ctx.config.reranker.tau_repair,
            judge=judge,
            proposer_model=ctx.config.llm.model,
            judge_model=ctx.config.judge.model,
        )
    finally:
        if judge_gateway is not None:
            if ctx.rate_broker is not None:
                ctx.run_meta["fleet_judge_calls"] = ctx.rate_broker.calls_made("judge")
            judge_gateway.close()

    support_recall, items_weak = _support_recall(digest)
    recall_floor = ctx.config.reranker.recall_floor
    citation_ok = (support_recall >= recall_floor) if ctx.validate_citations else True
    return digest, citation_ok, support_recall, recall_floor, items_weak, outcome.items_repaired


def _build_repair_judge(ctx: RunContext) -> tuple[Optional[LLMJudge], Optional[LLMGateway]]:
    """Cross-model LLMJudge for citation repair when ``judge.enabled`` (EP-12, D4).

    The judge rides the existing LLMGateway with a model override (R1) on its
    own RPM bucket and stage call budget (judge<=8). Conditions that would
    violate the cross-model contract (R4) or replay determinism disable the
    judge for the run — never fail it:

    * ``judge.model == llm.model`` -> disabled (the repair contract requires a
      DIFFERENT model from the proposer);
    * ``--replay-llm`` -> disabled (LLM recordings carry no judge channel yet;
      a judge request would positionally consume extractor entries — the judge
      record/replay channel is an EP-14 design item).

    Returns (judge, gateway); the caller owns ``gateway.close()``.
    """
    cfg = ctx.config.judge
    if not cfg.enabled:
        return None, None
    if cfg.model == ctx.config.llm.model:
        logger.warning(
            "Repair judge disabled: judge.model must differ from the extractor model (R4)",
            judge_model=cfg.model,
            trace_id=ctx.trace_id,
        )
        return None, None
    if ctx.replay_llm:
        logger.warning(
            "Repair judge disabled under --replay-llm: no judge channel in LLM recordings",
            trace_id=ctx.trace_id,
        )
        return None, None
    judge_llm = ctx.config.llm.model_copy(
        update={"model": cfg.model, "max_output_tokens": JUDGE_MAX_OUTPUT_TOKENS}
    )
    gateway = LLMGateway(
        judge_llm,
        metrics=ctx.metrics,
        rate_broker=ctx.rate_broker,
        stage="judge",
    )
    return LLMJudge(gateway), gateway


def _support_recall(digest: Digest) -> tuple[float, int]:
    """Fraction of evidence-backed items that are offset-verifiable; plus weak count.

    Thin alias over :func:`digest_core.evidence.citation_gate.support_recall`
    (the metric moved there so the EP-10 selector shares it; existing callers
    and tests keep this name).
    """
    return support_recall(digest)


def _build_reranker(ctx: RunContext) -> Optional[RerankerClient]:
    """RerankerClient for the P2 gate when ``reranker.enabled`` (EP-12, D4).

    Replay runs stay deterministic: LLM recordings and fleet recordings live in
    separate channels, so the reranker only runs under ``--replay-llm`` when a
    ``<recording>.fleet.json`` sidecar exists — otherwise it is disabled for the
    run (fidelity-only gate, logged). ``--record-llm`` records fleet calls into
    the same sidecar for later replay.
    """
    cfg = ctx.config.reranker
    if not cfg.enabled:
        return None
    replay = None
    if ctx.replay_llm:
        sidecar = Path(f"{ctx.replay_llm}.fleet.json")
        if not sidecar.exists():
            logger.warning(
                "Reranker disabled for this replay run: no fleet sidecar recording",
                sidecar=str(sidecar),
                trace_id=ctx.trace_id,
            )
            return None
        replay = str(sidecar)
    return RerankerClient(
        ctx.config.llm,
        model=cfg.model,
        endpoint_path=cfg.endpoint_path,
        rate_broker=ctx.rate_broker,
        record=f"{ctx.record_llm}.fleet.json" if ctx.record_llm else None,
        replay=replay,
        stage="reranker",
    )


def _apply_shadow_citation_gate(
    ctx: RunContext, digest: Digest, normalized_messages: Sequence[NormalizedMessage]
) -> Digest:
    """P2 gate in SHADOW mode (PR8): annotate offset-fidelity + weak_evidence always.

    Default-on but shadow — it never changes citation_validation_ok, exit codes, or
    delivery (those stay as-is until PR11). With ``reranker.enabled`` off (the
    default until EP-14 corp validation) the gate is offset-only and makes zero
    network calls; with it on, low-confidence items get a budgeted support score.
    """
    msg_map = {m.msg_id: m.text_body for m in normalized_messages if m.msg_id}
    reranker = _build_reranker(ctx)
    gate = CitationGate(msg_map, reranker=reranker, config=ctx.config.reranker)
    try:
        return gate.annotate(digest, metrics=ctx.metrics)
    finally:
        if reranker is not None:
            ctx.run_meta["fleet_reranker_calls"] = gate.reranker_calls
            reranker.close()


def _stage_assemble(ctx: RunContext, digest: Digest) -> None:
    """Stage 7: ASSEMBLE — write JSON and Markdown artifacts."""
    assemble_start = time.perf_counter()
    _emit(ctx, "on_stage_start", "assemble")
    _write_json(ctx.json_path, digest.model_dump(exclude_none=True))
    MarkdownAssembler(language=ctx.config.report.language).write_digest(digest, ctx.md_path)
    _finish_stage(ctx, "assemble", assemble_start, items=_count_digest_items(digest))


def _llm_budget_summary(llm_trace: Dict[str, Any], llm_config) -> Dict[str, Any]:
    """Per-run call/token spend vs budget (ADR-008 v2 visibility clause, D6)."""
    tokens_used = int(llm_trace.get("run_tokens_used") or 0)
    token_budget = int(llm_config.max_tokens_per_run or 0)
    return {
        "calls_made": int(llm_trace.get("run_calls_made") or 0),
        "extractor_call_budget": int((llm_config.stage_call_budgets or {}).get("extractor", 0)),
        "tokens_used": tokens_used,
        "max_tokens_per_run": token_budget,
        "tokens_pct": round(100.0 * tokens_used / token_budget, 1) if token_budget else None,
    }


def _stage_deliver(ctx: RunContext, digest: Digest) -> Dict[str, Any]:
    """Stage 8: DELIVER — send digest to Mattermost if enabled."""
    delivery_receipt: Dict[str, Any] = {}
    if ctx.config.deliver.mattermost.enabled:
        deliver_start = time.perf_counter()
        _emit(ctx, "on_stage_start", "deliver")
        try:
            delivery_receipt = MattermostDeliverer(
                ctx.config.deliver.mattermost, language=ctx.config.report.language
            ).deliver_digest(
                digest,
                json_path=str(ctx.json_path),
                llm_budget=ctx.run_meta.get("llm_budget"),
            )
        except Exception as exc:
            delivery_receipt = {"status": "warning", "error": str(exc)}
            logger.warning("Mattermost delivery failed", trace_id=ctx.trace_id, error=str(exc))
        _finish_stage(ctx, "deliver", deliver_start)
        _emit(
            ctx,
            "on_delivery",
            "mattermost",
            delivery_receipt.get("status") not in (None, "warning"),
            delivery_receipt.get("error"),
        )
    return delivery_receipt


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def _run_pipeline(
    *,
    from_date: str,
    sources: Sequence[str],
    out: str,
    model: str,
    window: str,
    state: str | None,
    validate_citations: bool,
    dry_run: bool,
    force: bool,
    dump_ingest: str | None,
    replay_ingest: str | None,
    record_llm: str | None = None,
    replay_llm: str | None = None,
    sink: ProgressSink | None = None,
) -> RunDigestResult:
    """Run the digest pipeline with shared setup for normal and dry-run modes."""
    ctx = _init_context(
        from_date=from_date,
        out=out,
        model=model,
        window=window,
        state=state,
        validate_citations=validate_citations,
        dry_run=dry_run,
        force=force,
        dump_ingest=dump_ingest,
        replay_ingest=replay_ingest,
        record_llm=record_llm,
        replay_llm=replay_llm,
        sink=sink,
    )

    # The root span covers every exit path (ok / partial / skipped / failed);
    # no-op unless observability.otel_enabled configured tracing in _init_context.
    try:
        return _run_pipeline_traced(ctx, sources)
    finally:
        tracing.end_run_span(ctx.run_meta.get("status"))


def _run_pipeline_traced(ctx: RunContext, sources: Sequence[str]) -> RunDigestResult:
    config_sha = _config_sha256(ctx.config)
    idem_sidecar = _read_idem_sidecar(ctx.json_path)

    if not ctx.force and _idem_pre_ingest_skip(
        ctx.json_path, ctx.md_path, idem_sidecar, config_sha
    ):
        artifact_age_hours = _artifact_age_hours(ctx.json_path)
        logger.info(
            "Fresh artifacts with matching config, skipping rebuild (pre-ingest)",
            digest_date=ctx.digest_date,
            artifact_age_hours=artifact_age_hours,
            trace_id=ctx.trace_id,
        )
        ctx.metrics.record_run_total("ok")
        ctx.run_meta["status"] = "skipped"
        ctx.run_meta["skip_reason"] = "config_mtime_match"
        ctx.run_meta["pipeline_metrics"] = {"artifact_age_hours": artifact_age_hours}
        _write_json(ctx.metadata_path, ctx.run_meta)
        return RunDigestResult(True, True)

    logger.info(
        "Starting digest run",
        trace_id=ctx.trace_id,
        digest_date=ctx.digest_date,
        dry_run=ctx.dry_run,
        sources=list(sources),
        replay_ingest=ctx.replay_ingest,
        dump_ingest=ctx.dump_ingest,
        force=ctx.force,
    )

    try:
        try:
            # Stages 1-2: INGEST + NORMALIZE
            normalized_messages = _guard(ctx, "ingest", lambda: _stage_ingest(ctx))

            # Post-ingest idempotency: if the ingested content is byte-identical to the
            # last successful build (same config + pipeline version), skip the expensive
            # LLM/assemble/deliver rather than re-running. --force / dry-run bypass.
            content_sha = _content_sha256(normalized_messages)
            if (
                not ctx.force
                and not ctx.dry_run
                and _idem_content_skip(
                    ctx.json_path, ctx.md_path, idem_sidecar, config_sha, content_sha
                )
            ):
                logger.info(
                    "Ingested content unchanged since last build, skipping rebuild (post-ingest)",
                    digest_date=ctx.digest_date,
                    trace_id=ctx.trace_id,
                    emails_processed=len(normalized_messages),
                )
                ctx.metrics.record_run_total("ok")
                ctx.run_meta["status"] = "skipped"
                ctx.run_meta["skip_reason"] = "content_match"
                ctx.run_meta["pipeline_metrics"] = {"emails_processed": len(normalized_messages)}
                _write_json(ctx.metadata_path, ctx.run_meta)
                return RunDigestResult(True, True)

            # Stage 3: THREADS
            threads = _guard(ctx, "threads", lambda: _stage_threads(ctx, normalized_messages))

            # Stage 4: EVIDENCE
            evidence_chunks = _guard(
                ctx,
                "evidence",
                lambda: _stage_evidence(ctx, threads, total_emails=len(normalized_messages)),
            )

            # Stage 5: SELECT
            selected_evidence, selection_metrics = _guard(
                ctx, "select", lambda: _stage_select(ctx, evidence_chunks)
            )
        except _StageDegraded as degraded:
            return _finish_degraded(ctx, degraded.digest)

        ctx.run_meta["evidence_summary"] = _build_evidence_summary(
            threads=threads,
            evidence_chunks=evidence_chunks,
            selected_evidence=selected_evidence,
            selection_metrics=selection_metrics,
        )

        if ctx.dry_run:
            ctx.metrics.record_run_total("ok")
            ctx.metrics.record_digest_build_time()
            ctx.run_meta["status"] = "dry_run"
            ctx.run_meta["pipeline_metrics"] = {
                "emails_processed": len(normalized_messages),
                "threads_created": len(threads),
                "evidence_chunks": len(evidence_chunks),
                "selected_evidence": len(selected_evidence),
            }
            _write_json(ctx.metadata_path, ctx.run_meta)
            logger.info(
                "Digest dry-run completed successfully",
                trace_id=ctx.trace_id,
                digest_date=ctx.digest_date,
                emails_processed=len(normalized_messages),
                threads_created=len(threads),
                evidence_chunks=len(evidence_chunks),
                selected_evidence=len(selected_evidence),
            )
            return RunDigestResult(True, True)

        # Stage 6: LLM
        digest, llm_error = _stage_llm(ctx, selected_evidence, normalized_messages)

        (
            digest,
            citation_ok,
            support_recall,
            recall_floor,
            items_weak,
            items_repaired,
        ) = _post_llm_digest_enrichment(ctx, digest, normalized_messages, selected_evidence)
        ctx.run_meta["citation_validation_ok"] = citation_ok
        ctx.run_meta["support_recall"] = round(support_recall, 4)
        ctx.run_meta["items_weak"] = items_weak
        ctx.run_meta["items_repaired"] = items_repaired

        # D1: withhold weak items from the main sections (quarantine, never drop).
        # Only meaningful when citation validation ran — without it spans are never
        # resolved and *every* item looks weak (quarantining all would be a drop).
        if ctx.validate_citations and ctx.config.reranker.quarantine_weak:
            ctx.run_meta["items_quarantined"] = _quarantine_weak_items(
                digest, ctx.config.report.language
            )

        # Cross-run dedup annotation (EP-7; no-op unless memory.dedup_ledger)
        _apply_dedup_ledger(ctx, digest)

        # Stage 7: ASSEMBLE
        _stage_assemble(ctx, digest)

        # Stage 8: DELIVER
        delivery_receipt = _stage_deliver(ctx, digest)
        ctx.run_meta["delivery_receipt"] = delivery_receipt

        ctx.metrics.record_run_total("ok")
        ctx.metrics.record_digest_build_time()
        ctx.run_meta["status"] = "ok" if not ctx.run_meta["partial"] else "partial"
        ctx.run_meta["pipeline_metrics"] = {
            "total_items": _count_digest_items(digest),
            "emails_processed": len(normalized_messages),
            "threads_created": len(threads),
            "evidence_chunks": len(evidence_chunks),
            "selected_evidence": len(selected_evidence),
        }
        _write_idem_sidecar(ctx.json_path, config_sha=config_sha, content_sha=content_sha)
        _write_json(ctx.metadata_path, ctx.run_meta)

        logger.info(
            "Digest run completed",
            trace_id=ctx.trace_id,
            digest_date=ctx.digest_date,
            total_items=_count_digest_items(digest),
            partial=ctx.run_meta["partial"],
        )
        return RunDigestResult(
            True, citation_ok, support_recall, recall_floor, items_weak, items_repaired
        )
    except Exception as exc:
        ctx.metrics.record_run_total("failed")
        ctx.run_meta["status"] = "failed"
        ctx.run_meta["error"] = str(exc)
        _write_json(ctx.metadata_path, ctx.run_meta)
        logger.error("Digest run failed", trace_id=ctx.trace_id, error=str(exc), exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Helpers (unchanged — preserving names for test compatibility)
# ---------------------------------------------------------------------------


def _resolve_digest_date(from_date: str) -> str:
    if from_date == "today":
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    datetime.strptime(from_date, "%Y-%m-%d")
    return from_date


def _artifact_age_hours(path: Path) -> float:
    return (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 3600


def _should_skip_existing_artifacts(json_path: Path, md_path: Path) -> bool:
    if not json_path.exists() or not md_path.exists():
        return False
    return _artifact_age_hours(json_path) < 48


# ---------------------------------------------------------------------------
# Idempotency sidecar (PR1): digest-{date}.idem.json carries
# {config_sha256, content_sha256, pipeline_version}. A skip never fires when the
# config or pipeline version changed. Two complementary skip paths:
#   - pre-ingest  : fresh artifacts (T-48h) + matching config — cheap, no EWS;
#                   the freshness window stands in for content stability.
#   - post-ingest : config + content + version all unchanged — verifies content
#                   exactly and protects the scarce extractor LLM call.
# --force bypasses both.
# ---------------------------------------------------------------------------


def _idem_sidecar_path(json_path: Path) -> Path:
    """Sidecar path next to the JSON artifact: digest-{date}.idem.json."""
    return json_path.with_suffix(".idem.json")


def _config_sha256(config: Config) -> str:
    """Stable hash of the secret-free effective config (reuses _sanitize_config)."""
    canonical = json.dumps(
        _sanitize_config(config), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _content_sha256(messages: Sequence[NormalizedMessage]) -> str:
    """Order-independent hash of the ingested message set (msg_id|subject|body)."""
    projection = sorted(
        "\x01".join(
            [
                m.msg_id or "",
                getattr(m, "subject", "") or "",
                getattr(m, "text_body", "") or "",
            ]
        )
        for m in messages
    )
    return hashlib.sha256("\x02".join(projection).encode("utf-8")).hexdigest()


def _read_idem_sidecar(json_path: Path) -> Optional[Dict[str, Any]]:
    """Load the idempotency sidecar, or None if missing/unreadable."""
    path = _idem_sidecar_path(json_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_idem_sidecar(json_path: Path, *, config_sha: str, content_sha: str) -> None:
    """Persist {config_sha256, content_sha256, pipeline_version} next to artifacts."""
    path = _idem_sidecar_path(json_path)
    path.write_text(
        json.dumps(
            {
                "config_sha256": config_sha,
                "content_sha256": content_sha,
                "pipeline_version": PIPELINE_VERSION,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _idem_pre_ingest_skip(
    json_path: Path, md_path: Path, sidecar: Optional[Dict[str, Any]], config_sha: str
) -> bool:
    """Cheap pre-ingest skip: fresh artifacts (T-48h) + matching config + version."""
    if not _should_skip_existing_artifacts(json_path, md_path):
        return False
    if not sidecar:
        return False
    return (
        sidecar.get("pipeline_version") == PIPELINE_VERSION
        and sidecar.get("config_sha256") == config_sha
    )


def _idem_content_skip(
    json_path: Path,
    md_path: Path,
    sidecar: Optional[Dict[str, Any]],
    config_sha: str,
    content_sha: str,
) -> bool:
    """Post-ingest skip: artifacts exist + config + version + content all unchanged.

    Independent of the T-48h window — its job is to avoid re-running the scarce
    extractor LLM when nothing has actually changed since the last build.
    """
    if not (json_path.exists() and md_path.exists()):
        return False
    if not sidecar:
        return False
    return (
        sidecar.get("pipeline_version") == PIPELINE_VERSION
        and sidecar.get("config_sha256") == config_sha
        and sidecar.get("content_sha256") == content_sha
    )


def _normalize_messages(
    messages: Sequence[NormalizedMessage], config: Config
) -> List[NormalizedMessage]:
    normalizer = HTMLNormalizer()
    quote_cleaner = QuoteCleaner(
        keep_top_quote_head=config.email_cleaner.keep_top_quote_head,
        config=config.email_cleaner,
    )

    normalized_messages = []
    for msg in messages:
        text_body, _ = normalizer.html_to_text(msg.text_body)
        text_body = normalizer.truncate_text(text_body, max_bytes=200000)
        if config.email_cleaner.enabled:
            cleaned_body, _ = quote_cleaner.clean_email_body(
                text_body, lang="auto", policy="standard"
            )
        else:
            cleaned_body = text_body

        normalized_messages.append(
            NormalizedMessage(
                msg_id=msg.msg_id,
                conversation_id=msg.conversation_id,
                datetime_received=msg.datetime_received,
                sender_email=msg.sender_email,
                subject=msg.subject,
                text_body=cleaned_body,
                to_recipients=msg.to_recipients,
                cc_recipients=msg.cc_recipients,
                importance=msg.importance,
                is_flagged=msg.is_flagged,
                has_attachments=msg.has_attachments,
                attachment_types=msg.attachment_types,
                from_email=msg.from_email,
                from_name=msg.from_name,
                to_emails=msg.to_emails,
                cc_emails=msg.cc_emails,
                message_id=msg.message_id,
                body_norm=cleaned_body,
                received_at=msg.received_at,
            )
        )
    return normalized_messages


# Instruction-language prompt per model. Output is RU in BOTH prompts (enforced by
# the prompt rules); only the *instruction* language differs. This explicit map
# replaces a fragile `"qwen" in name` substring and covers the fleet reasoners,
# while preserving the current default (qwen35-397b-a17b -> EN instructions).
_EXTRACT_PROMPT_BY_MODEL = {
    "qwen35-397b-a17b": "extract_actions.en.v1",
    "qwen3-next-80b-a3b": "extract_actions.en.v1",
    "qwen35-35b-a3b": "extract_actions.en.v1",
    "glm-4.7-flash": "extract_actions.v1",
}
_DEFAULT_EXTRACT_PROMPT = "extract_actions.v1"


def _load_extract_prompt(model_name: str, language: str = DEFAULT_LANGUAGE) -> tuple[str, str]:
    """Prompt variant by (model, report language).

    EN reports use the v2 prompt (EN instructions + EN output) for every
    model; RU reports keep the per-model instruction-language map below
    (output RU in both of those prompts).
    """
    if language == "en":
        prompt_version = "extract_actions.en.v2"
    else:
        prompt_version = _EXTRACT_PROMPT_BY_MODEL.get(model_name or "", _DEFAULT_EXTRACT_PROMPT)
    template_path = get_prompt_template_path(prompt_version)
    prompt_path = PROMPTS_DIR / template_path
    return prompt_version, prompt_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-stage graceful degradation (PR4): a failed early stage degrades to an
# empty/partial digest instead of crashing the run. degradation_policy is pure
# and unit-testable. The LLM and deliver stages already self-degrade.
# ---------------------------------------------------------------------------

STAGE_BANNERS_RU = {
    "threads": "Сбой при группировке писем. Дайджест неполный.",
    "evidence": "Сбой при подготовке доказательств. Дайджест неполный.",
    "select": "Сбой при отборе контекста. Дайджест неполный.",
}

_DEGRADE_ACTIONS = {
    "ingest": "empty",
    "normalize": "empty",
    "threads": "partial",
    "evidence": "partial",
    "select": "partial",
    "assemble": "crash",
}


class _StageDegraded(Exception):
    """Internal signal carrying a degraded digest from a failed early stage."""

    def __init__(self, digest: Digest):
        super().__init__("stage degraded")
        self.digest = digest


def _is_operational_error(exc: Exception, *, replay: bool = False) -> bool:
    """Operational (degradable) vs config/precondition failure.

    Network errors always degrade. A missing/invalid file (OSError, e.g.
    FileNotFoundError) degrades only in replay mode (a missing snapshot); in live
    mode it is a configuration error (e.g. a bad ``verify_ca`` path) that must
    fail loud rather than silently produce an empty digest.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if replay and isinstance(exc, OSError):
        return True
    return False


def degradation_policy(stage: str, exc: Exception, config: Config, *, replay: bool = False) -> str:
    """Pure policy: how a failed `stage` degrades -> 'crash' | 'partial' | 'empty'."""
    if not config.degrade.enable:
        return "crash"
    action = _DEGRADE_ACTIONS.get(stage, "crash")
    # Ingest/normalize is the source boundary: a config/precondition error
    # (missing credentials, bad verify_ca path, bad endpoint) must fail fast
    # rather than silently produce an empty digest. Only operational failures
    # (EWS unreachable; a missing replay snapshot in replay mode) degrade.
    if action == "empty" and not _is_operational_error(exc, replay=replay):
        return "crash"
    return action


def _degrade_stage(ctx: RunContext, stage: str, exc: Exception) -> Digest:
    """Apply the degradation policy for a failed stage; return a digest or re-raise."""
    _emit(ctx, "on_stage_failed", stage, str(exc))
    action = degradation_policy(stage, exc, ctx.config, replay=bool(ctx.replay_ingest))
    logger.error(
        "Pipeline stage failed",
        stage=stage,
        action=action,
        trace_id=ctx.trace_id,
        error=str(exc),
        exc_info=True,
    )
    if action == "crash":
        raise exc
    ctx.metrics.record_degradation(f"{stage}_failed")
    ctx.run_meta["partial"] = True
    ctx.run_meta["status"] = "partial"
    ctx.run_meta.setdefault("degraded_stages", []).append(stage)
    if action == "empty":
        return _build_empty_digest(ctx.digest_date, ctx.trace_id, prompt_version="degraded")
    banner = stage_banner(stage, ctx.config.report.language)
    return _build_partial_digest(
        ctx.digest_date, ctx.trace_id, str(exc), title=banner, language=ctx.config.report.language
    )


def _guard(ctx: RunContext, stage: str, thunk):
    """Run a stage thunk; on a degradable failure raise _StageDegraded(digest)."""
    try:
        return thunk()
    except Exception as exc:
        raise _StageDegraded(_degrade_stage(ctx, stage, exc)) from exc


def _finish_degraded(ctx: RunContext, digest: Digest) -> RunDigestResult:
    """Deliver a degraded digest (full run) or just record it (dry-run)."""
    if ctx.dry_run:
        ctx.metrics.record_run_total("ok")
        ctx.run_meta["status"] = "partial"
        ctx.run_meta["pipeline_metrics"] = {"partial": True}
        _write_json(ctx.metadata_path, ctx.run_meta)
        return RunDigestResult(True, True)

    # A degraded digest has no verifiable citations: fail the gate only when the
    # caller asked for it (--validate-citations), so default runs stay exit 0.
    citation_ok = not ctx.validate_citations
    ctx.run_meta["citation_validation_ok"] = citation_ok
    _stage_assemble(ctx, digest)  # assemble failure -> crash (per policy)
    ctx.run_meta["delivery_receipt"] = _stage_deliver(ctx, digest)
    ctx.metrics.record_run_total("ok")
    ctx.metrics.record_digest_build_time()
    ctx.run_meta["pipeline_metrics"] = {
        "total_items": _count_digest_items(digest),
        "partial": True,
    }
    _write_json(ctx.metadata_path, ctx.run_meta)
    logger.warning(
        "Digest delivered in degraded mode",
        trace_id=ctx.trace_id,
        degraded_stages=ctx.run_meta.get("degraded_stages"),
    )
    return RunDigestResult(True, citation_ok)


def _build_empty_digest(digest_date: str, trace_id: str, prompt_version: str) -> Digest:
    return Digest(
        schema_version="1.0",
        prompt_version=prompt_version,
        digest_date=digest_date,
        trace_id=trace_id,
        sections=[],
    )


def _build_partial_digest(
    digest_date: str,
    trace_id: str,
    error_message: str,
    title: str | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> Digest:
    strings = report_strings(language)
    if title is None:
        title = strings["banner_llm_unavailable"]
        if "timed out" in error_message.lower() or "timeout" in error_message.lower():
            title = strings["banner_llm_timeout"]
    return Digest(
        schema_version="1.0",
        prompt_version="none",
        digest_date=digest_date,
        trace_id=trace_id,
        sections=[
            {
                "title": section_title(STATUS, language),
                "items": [
                    {
                        "title": title,
                        "due": None,
                        "evidence_id": "system",
                        "confidence": 0.0,
                        "source_ref": {"type": "system", "error": error_message},
                    }
                ],
            }
        ],
    )


def _sort_sections(sections: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized_sections = []
    for section in sections:
        items = section.get("items", [])
        if not items:
            continue
        normalized_sections.append({"title": section.get("title", ""), "items": items})
    return sorted(
        normalized_sections,
        key=lambda section: (section_sort_weight(section["title"]), section["title"]),
    )


def _serialize_message(message: NormalizedMessage) -> Dict[str, Any]:
    payload = asdict(message)
    for key in ("datetime_received", "received_at"):
        value = payload.get(key)
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    return payload


def _deserialize_message(payload: Dict[str, Any]) -> NormalizedMessage:
    message_payload = dict(payload)
    for key in ("datetime_received", "received_at"):
        value = message_payload.get(key)
        if isinstance(value, str):
            message_payload[key] = datetime.fromisoformat(value)
    return NormalizedMessage(**message_payload)


def _dump_ingest_snapshot(
    path: Path, messages: Sequence[NormalizedMessage], digest_date: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "source": "ews",
            "digest_date": digest_date,
            "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
            "count": len(messages),
        },
        "messages": [_serialize_message(message) for message in messages],
    }
    _write_json(path, payload)


def _load_ingest_snapshot(path: Path) -> List[NormalizedMessage]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        messages = payload
    else:
        messages = payload.get("messages", [])
    return [_deserialize_message(message) for message in messages]


def _build_evidence_summary(
    *,
    threads: Sequence[Any],
    evidence_chunks: Sequence[EvidenceChunk],
    selected_evidence: Sequence[EvidenceChunk],
    selection_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    chunk_counts: Dict[str, int] = {}
    for chunk in evidence_chunks:
        chunk_counts[chunk.conversation_id] = chunk_counts.get(chunk.conversation_id, 0) + 1

    return {
        "chunk_count": len(evidence_chunks),
        "total_tokens": sum(chunk.token_count for chunk in evidence_chunks),
        "top_scores": [chunk.priority_score for chunk in list(evidence_chunks)[:5]],
        "filtered_service_count": 0,
        "selected_count": len(selected_evidence),
        "selection_metrics": selection_metrics,
        "per_thread": [
            {
                "conversation_id": thread.conversation_id,
                "message_count": getattr(
                    thread, "message_count", len(getattr(thread, "messages", []))
                ),
                "chunk_count": chunk_counts.get(thread.conversation_id, 0),
            }
            for thread in threads
        ],
    }


def _quarantine_weak_items(digest: Digest, language: str = DEFAULT_LANGUAGE) -> int:
    """Move ``weak_evidence`` items into a trailing «Не подтверждено» section (D1).

    Containment without loss: items the gate could not offset-verify leave the
    main sections but stay in the digest with their ⚠ badge — never dropped (R3).
    Returns the number of items moved. Sections emptied by the move are removed;
    with no weak items the digest is unchanged (no quarantine section appears).
    """
    quarantined = []
    surviving_sections = []
    for section in digest.sections:
        if normalize_section(section.title) == UNCONFIRMED:
            quarantined.extend(section.items)
            continue
        kept = [item for item in section.items if not getattr(item, "weak_evidence", False)]
        moved = [item for item in section.items if getattr(item, "weak_evidence", False)]
        quarantined.extend(moved)
        if kept:
            section.items = kept
            surviving_sections.append(section)
    if quarantined:
        surviving_sections.append(
            Section(title=section_title(UNCONFIRMED, language), items=quarantined)
        )
    digest.sections = surviving_sections
    return len(quarantined)


def _apply_dedup_ledger(ctx: RunContext, digest: Digest) -> None:
    """Annotate items whose evidence already backed a delivered item (EP-7, F8).

    Annotate-only by design: suppression/down-ranking and the default flip are
    owner decision D3. With ``memory.dedup_ledger`` off (the default) this is a
    pure no-op — nothing is read or written, preserving privacy-via-not-storing.
    The ledger persists hashed fingerprints only (see memory/ledger.py).
    """
    if not ctx.config.memory.dedup_ledger:
        return
    from digest_core.memory.ledger import DedupLedger, item_fingerprint

    ledger_path = Path(ctx.config.ews.sync_state_path).expanduser().parent / "delivered-items.jsonl"
    ledger = DedupLedger(ledger_path, ttl_days=ctx.config.memory.dedup_ttl_days)
    # Two phases: "seen" means a PREVIOUS run delivered this evidence. Several
    # items legitimately share one email within a single run (multiple actions
    # per message) — that must not count as a repeat.
    items_seen_before = 0
    run_fingerprints = []
    for section in digest.sections:
        for item in section.items:
            msg_id = (item.source_ref or {}).get("msg_id", "")
            if not msg_id or item.evidence_id == "system":
                continue  # status banners and system items carry no evidence identity
            fingerprint = item_fingerprint(item.evidence_id, msg_id)
            run_fingerprints.append(fingerprint)
            if ledger.seen(fingerprint):
                item.seen_before = True
                items_seen_before += 1
    for fingerprint in run_fingerprints:
        ledger.record(fingerprint)
    ledger.save()
    ctx.run_meta["dedup_ledger"] = {
        "items_seen_before": items_seen_before,
        "entries": len(ledger),
        "path": str(ledger_path),
    }
    logger.info(
        "Dedup ledger applied",
        items_seen_before=items_seen_before,
        entries=len(ledger),
        trace_id=ctx.trace_id,
    )


def _exporter_status_entry(metrics: Any) -> Dict[str, Any]:
    """Exporter state for run_meta, tolerant of test doubles (mocks/dummies)."""
    status_fn = getattr(metrics, "exporter_status", None)
    if callable(status_fn):
        candidate = status_fn()
        if isinstance(candidate, dict):
            return candidate
    return {"status": "unknown", "port": None, "error": None}


def _sanitize_config(config: Config) -> Dict[str, Any]:
    def sanitize(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {
                child_key: sanitize(child_value, child_key)
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [sanitize(item, key) for item in value]
        if isinstance(value, str) and key.lower() in {
            "authorization",
            "token",
            "password",
            "secret",
        }:
            return "[[REDACTED]]"
        return value

    payload = config.model_dump(exclude_none=True)
    if payload.get("llm", {}).get("headers", {}).get("Authorization"):
        payload["llm"]["headers"]["Authorization"] = "[[REDACTED]]"
    return sanitize(payload)


def _record_stage_duration(
    run_meta: Dict[str, Any],
    metrics: MetricsCollector,
    stage: str,
    started_at: float,
) -> None:
    duration_seconds = time.perf_counter() - started_at
    run_meta["stage_durations_ms"][stage] = int(duration_seconds * 1000)
    metrics.record_pipeline_stage_duration(stage, duration_seconds)
    tracing.record_stage_span(stage, duration_seconds)


def _emit(ctx: RunContext, method: str, *args, **kwargs) -> None:
    """Fire a sink event; a broken renderer must never break the pipeline."""
    try:
        getattr(ctx.sink, method)(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - sink errors are non-fatal by contract
        logger.warning("Progress sink failed", method=method, error=str(exc))


def _finish_stage(ctx: RunContext, stage: str, started_at: float, **counts: Any) -> None:
    """Record the stage duration and emit on_stage_end with the funnel counts."""
    _record_stage_duration(ctx.run_meta, ctx.metrics, stage, started_at)
    _emit(ctx, "on_stage_end", stage, counts, ctx.run_meta["stage_durations_ms"].get(stage, 0))


def _count_digest_items(digest: Digest) -> int:
    return sum(len(section.items) for section in digest.sections)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
