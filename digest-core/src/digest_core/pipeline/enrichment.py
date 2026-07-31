"""
Post-LLM enrichment — everything that happens to a digest *after* the model
has spoken and *before* it is assembled.

Extracted from ``run.py`` (ACTPULSE-23 phase 3). This is the third and largest
carve-out, and it is a coherent one: every function here takes an already-built
``Digest`` and improves it — citations and the P2 gate, weak-item repair and
quarantine, cross-run dedup, sender/subject backfill, the calendar Meetings
section, and the store-derived Open-loops carryover.

Two properties make the seam safe:

* **The extraction is already on disk** before any of this runs (``run.py``
  writes the ``.raw.json`` sidecar first), so each pass is allowed to fail
  without costing the scarce LLM call. ``_enrich_guard`` is that posture —
  *skip* the pass, record it, keep going.
* **Nothing here calls back into the orchestrator.** The only shared type is
  ``RunContext``, which is why it now lives in ``pipeline/context.py``.

``run.py`` re-exports every name below under its historical ``_``-prefixed
spelling, so the tests that reach for ``runner._enrich_digest_from_store`` and
friends keep working unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from digest_core.assemble.labels import (
    DEFAULT_LANGUAGE,
    UNCONFIRMED,
    normalize_section,
    section_title,
)
from digest_core.config import Config, RankerConfig
from digest_core.eval.judge import LLMJudge
from digest_core.evidence.citation_gate import CitationGate, support_recall
from digest_core.evidence.citations import CitationBuilder, CitationValidator
from digest_core.evidence.repair import repair_weak_items
from digest_core.evidence.split import EvidenceChunk
from digest_core.ingest.ews import NormalizedMessage
from digest_core.llm.fleet import RerankerClient
from digest_core.llm.gateway import LLMGateway
from digest_core.llm.schemas import Digest, Section
from digest_core.pipeline.context import RunContext
from digest_core.select.ranker import DigestRanker

logger = structlog.get_logger()

# A repair verdict is a tiny JSON object; no reason to let the judge model
# spend the extractor-sized output ceiling.
JUDGE_MAX_OUTPUT_TOKENS = 256


def _enrich_guard(ctx: RunContext, name: str, thunk, *, default=None):
    """Run a post-LLM enrichment pass; on failure **skip it and keep going**.

    The missing posture in this pipeline. Pre-LLM stages degrade (they can be
    re-run cheaply); ASSEMBLE crashes by policy. But an enrichment pass sits
    between a paid extraction and the report, and neither posture fits: aborting
    throws away the call, and degrading throws away the items. Skipping the pass
    keeps both — the digest is simply less enriched.

    Every skip is recorded in ``run_meta["enrichment_skipped"]`` and surfaces in
    the run's ``.meta.json``. No silent caps: a section quietly missing from a
    digest is exactly the kind of loss that has to be visible.
    """
    try:
        return thunk()
    except Exception as exc:  # noqa: BLE001 - a bad pass must not cost the call
        logger.warning(
            "Post-LLM enrichment pass failed; skipping it",
            trace_id=ctx.trace_id,
            enrichment=name,
            error=f"{type(exc).__name__}: {exc}",
        )
        ctx.run_meta.setdefault("enrichment_skipped", []).append(
            {"pass": name, "error": f"{type(exc).__name__}: {exc}"}
        )
        return default


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


def _support_recall(digest: Digest) -> tuple[float, int]:
    """Fraction of evidence-backed items that are offset-verifiable; plus weak count.

    Thin alias over :func:`digest_core.evidence.citation_gate.support_recall`
    (the metric moved there so the EP-10 selector shares it; existing callers
    and tests keep this name).
    """
    return support_recall(digest)


def _ranker_user_aliases(config: Config) -> List[str]:
    # Single source of truth lives on Config (shared with the InboxAPI insight verbs).
    return config.user_aliases()


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
        sink=ctx.sink,
    )


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
        sink=ctx.sink,
    )
    return LLMJudge(gateway), gateway


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


def _item_msg_id(item: Any) -> str:
    """The message id an item is anchored to: source_ref first, then spans/citations."""
    msg_id = (getattr(item, "source_ref", None) or {}).get("msg_id", "")
    if msg_id:
        return msg_id
    for span in getattr(item, "evidence_spans", None) or []:
        if span.msg_id:
            return span.msg_id
    for citation in getattr(item, "citations", None) or []:
        if citation.msg_id:
            return citation.msg_id
    return ""


def _sender_display(message: NormalizedMessage) -> str:
    """Human sender line: 'Name <email>' when both exist, else whichever does."""
    name = (message.from_name or "").strip()
    email = (message.sender_email or message.from_email or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return email or name


def _enrich_items_from_messages(digest: Digest, messages: Sequence[NormalizedMessage]) -> None:
    """Populate ``source_subject``/``source_from`` from the message behind each
    item's msg_id (U4): the reader shows topics and authors without joining an
    ingest snapshot. P2-aligned (msg_id is the evidence anchor already);
    subjects/senders are report-class data — on disk and screen, never in logs.
    Existing values are kept (the artifact stays append-only in spirit).
    """
    by_id = {m.msg_id: m for m in messages if m.msg_id}
    if not by_id:
        return
    for section in digest.sections:
        for item in section.items:
            if item.evidence_id == "system":
                continue  # status banners carry no source message
            message = by_id.get(_item_msg_id(item))
            if message is None:
                continue
            if not item.source_subject and message.subject:
                item.source_subject = message.subject
            if not item.source_from:
                sender = _sender_display(message)
                if sender:
                    item.source_from = sender


def _enrich_digest_with_meetings(
    ctx: RunContext, digest: Digest, normalized_messages: List[NormalizedMessage]
) -> None:
    """Append a deterministic "Meetings" section from the run's calendar events (E2).

    Calendar events (``source='calendar'``, ingested via ``--sources calendar``) also flow
    through the LLM extractor (in-body agenda actions land in the normal sections); this adds
    a separate, no-LLM "Meetings" section so today's meetings surface *reliably* — even an
    agenda-less meeting. Events are sorted by start time and capped; overlapping meetings are
    flagged (E3 collision detection). Non-fatal (degrade-not-drop): any failure logs a warning
    and leaves the digest unchanged."""
    import hashlib

    events = [m for m in normalized_messages if getattr(m, "source", "") == "calendar"]
    if not events:
        return
    try:
        from digest_core.assemble.labels import MEETINGS, report_strings, section_title
        from digest_core.llm.schemas import Item, Section

        try:
            tz = ZoneInfo(ctx.config.time.user_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            tz = timezone.utc
        language = ctx.config.report.language
        overlap_label = report_strings(language)["meeting_overlap"]
        cap = max(1, int(getattr(ctx.config.ews, "calendar_max_events", 100)))
        events_sorted = sorted(events, key=lambda m: m.datetime_received)
        if len(events_sorted) > cap:
            ctx.run_meta["meeting_events_dropped"] = len(events_sorted) - cap
            logger.warning(
                "digest_meetings_capped",
                total=len(events_sorted),
                kept=cap,
                dropped=len(events_sorted) - cap,
                trace_id=ctx.trace_id,
            )
        events = events_sorted[:cap]

        # Collision detection (E3): which events' time ranges overlap (half-open
        # intersection; end falls back to start so a malformed/instant event never collides).
        # n is a day's meetings → O(n²) is fine.
        def _interval(m: NormalizedMessage):
            s = m.datetime_received
            return s, (getattr(m, "event_end", None) or s)

        overlaps: Dict[int, List[int]] = {i: [] for i in range(len(events))}
        for i in range(len(events)):
            si, ei = _interval(events[i])
            for j in range(i + 1, len(events)):
                sj, ej = _interval(events[j])
                if max(si, sj) < min(ei, ej):
                    overlaps[i].append(j)
                    overlaps[j].append(i)

        items: List[Item] = []
        collisions = 0
        for i, m in enumerate(events):
            start = m.datetime_received
            when = (start.astimezone(tz) if start.tzinfo else start).strftime("%H:%M")
            subject = m.subject or "(no title)"
            title = f"{subject} ({when})"
            ref: Dict[str, Any] = {
                "type": "meeting",
                "msg_id": m.msg_id,
                "conversation_id": m.conversation_id,
                "source": "calendar",
                "start": start.isoformat(),
            }
            if overlaps[i]:
                ref["overlaps"] = [events[k].subject or "(no title)" for k in overlaps[i]]
                title = f"{title} {overlap_label}"
                collisions += 1
            items.append(
                Item(
                    title=title,
                    evidence_id="meeting:"
                    + hashlib.sha256(m.msg_id.encode("utf-8")).hexdigest()[:16],
                    confidence=0.7,  # display threshold → renderers suppress the badge
                    source_ref=ref,
                    source_subject=subject,
                    source_from=m.from_name or m.sender_email or None,
                )
            )
        digest.sections.append(Section(title=section_title(MEETINGS, language), items=items))
        ctx.run_meta["meeting_items"] = len(items)
        if collisions:
            ctx.run_meta["meeting_collisions"] = collisions
        logger.info(
            "digest_meetings_added", count=len(items), collisions=collisions, trace_id=ctx.trace_id
        )
    except Exception as exc:  # noqa: BLE001 - degrade-not-drop: never fail the digest
        logger.warning("digest_meetings_failed", error=str(exc), trace_id=ctx.trace_id)


def _enrich_digest_from_store(ctx: RunContext, digest: Digest) -> None:
    """Append store-derived cross-day sections to the digest (P3 memory pillar).

    Two opt-in, non-fatal signals from the 30-day store:
    * ``store.pending`` → "Awaiting your reply": prior-day messages that asked YOU
      something (question/approval/request) and you have not answered since.
    * ``store.carryover`` → "Open loops": threads you were in that have gone quiet.

    A thread surfaced as Pending is removed from Open loops (Pending is the more
    specific, more actionable signal). The synthetic items carry no real evidence
    chunk, so this runs AFTER citation validation (they skip the gate) and before
    ASSEMBLE (so they render + sort in). Any failure logs a warning and leaves the
    digest unchanged (degrade-not-drop).
    """
    import hashlib

    cfg = getattr(ctx.config, "store", None)
    if cfg is None or not cfg.enabled or not (cfg.carryover or cfg.pending):
        return
    try:
        from digest_core.assemble.labels import (
            OPEN_LOOPS,
            PENDING,
            report_strings,
            section_title,
        )
        from digest_core.llm.schemas import Item, Section
        from digest_core.api import InboxAPI

        # Reference instant = end of the digest's *local* calendar day, so the cross-day
        # verbs reckon "today" against the same day the digest window uses (not the UTC day).
        try:
            ref_tz = ZoneInfo(ctx.config.time.user_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            ref_tz = timezone.utc
        try:
            ref = datetime.fromisoformat(ctx.digest_date).replace(
                hour=23, minute=59, second=59, tzinfo=ref_tz
            )
        except ValueError:
            ref = datetime.now(ref_tz)
        language = ctx.config.report.language
        strings = report_strings(language)

        # Read both cross-day insights through the single InboxAPI surface (it resolves
        # the owner aliases from Config — the same source the ranker uses).
        pending: list = []
        loops: list = []
        with InboxAPI.open(ctx.config, create=False) as api:
            if cfg.pending:
                pending = api.pending(
                    now=ref,
                    lookback_days=cfg.pending_lookback_days,
                    max_items=cfg.pending_max_items,
                )
            if cfg.carryover:
                loops = api.open_loops(
                    now=ref,
                    lookback_days=cfg.carryover_lookback_days,
                    stale_days=cfg.carryover_stale_days,
                    max_items=cfg.carryover_max_items,
                )
        # Dedup: a thread already surfaced as Pending is not also an Open loop.
        pending_threads = {p.thread_id for p in pending}
        loops = [loop for loop in loops if loop.thread_id not in pending_threads]

        def _evidence_id(prefix: str, key: str) -> str:
            return prefix + ":" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

        # confidence at the display threshold (CONFIDENCE_DISPLAY_MAX) so renderers
        # suppress the badge — these are strict-gated heuristics, not extraction
        # confidences, and the age is already in each title.
        if pending:
            template = strings["pending_item"]
            items = [
                Item(
                    title=template.format(days=p.age_days, subject=p.subject or "—"),
                    evidence_id=_evidence_id("pending", p.thread_id),
                    confidence=0.7,
                    source_ref={
                        "type": "pending",
                        "msg_id": p.asked_msg_id,
                        "conversation_id": p.thread_id,
                        "source": p.source,
                        "age_days": p.age_days,
                        "kind": p.kind,
                    },
                    source_subject=p.subject or None,
                    source_from=p.author or None,
                )
                for p in pending
            ]
            digest.sections.append(Section(title=section_title(PENDING, language), items=items))
            ctx.run_meta["pending_items"] = len(items)
            logger.info("digest_pending_added", count=len(items), trace_id=ctx.trace_id)

        if loops:
            template = strings["carryover_item"]
            items = [
                Item(
                    title=template.format(days=loop.age_days, subject=loop.subject or "—"),
                    evidence_id=_evidence_id("carryover", loop.thread_id),
                    confidence=0.7,
                    source_ref={
                        "type": "carryover",
                        "msg_id": loop.last_msg_id,
                        "conversation_id": loop.thread_id,
                        "source": loop.source,
                        "age_days": loop.age_days,
                        "msg_count": loop.msg_count,
                    },
                    source_subject=loop.subject or None,
                    source_from=loop.author or None,
                )
                for loop in loops
            ]
            digest.sections.append(Section(title=section_title(OPEN_LOOPS, language), items=items))
            ctx.run_meta["carryover_items"] = len(items)
            logger.info("digest_carryover_added", count=len(items), trace_id=ctx.trace_id)
    except Exception as exc:  # noqa: BLE001 - degrade-not-drop: never fail the digest
        logger.warning("digest_store_enrich_failed", error=str(exc), trace_id=ctx.trace_id)


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

    ledger_path = (
        Path(ctx.config.ews.resolved_sync_state_path()).expanduser().parent
        / "delivered-items.jsonl"
    )
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
