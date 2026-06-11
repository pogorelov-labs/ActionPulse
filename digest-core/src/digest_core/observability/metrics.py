"""
Prometheus metrics collection and export for digest pipeline.
"""

import time
from typing import Dict, Any
from prometheus_client import (
    Counter,
    Histogram,
    Summary,
    Gauge,
    start_http_server,
    CollectorRegistry,
)
import structlog

logger = structlog.get_logger()


class MetricsCollector:
    """Collect and export Prometheus metrics for digest pipeline."""

    _active_ports = set()

    def __init__(self, port: int = 9108, fail_on_exporter_error: bool = False):
        self.port = port
        self.start_time = time.time()
        self._server_started = False
        self.exporter_error: str | None = None
        self._fail_on_exporter_error = fail_on_exporter_error

        # Create custom registry
        self.registry = CollectorRegistry()

        # Rate-limiting for repeated warnings (per batch)
        self._warning_cache = set()

        # Initialize metrics
        self._init_metrics()

        self.start_server(port)

    def _init_metrics(self):
        """Initialize Prometheus metrics."""

        # LLM metrics
        self.llm_latency_ms = Histogram(
            "llm_latency_ms",
            "LLM request latency in milliseconds",
            buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000],
            registry=self.registry,
        )

        self.llm_tokens_in_total = Counter(
            "llm_tokens_in_total",
            "Total input tokens sent to LLM",
            registry=self.registry,
        )

        self.llm_tokens_out_total = Counter(
            "llm_tokens_out_total",
            "Total output tokens from LLM",
            registry=self.registry,
        )

        self.llm_request_context_total = Counter(
            "llm_request_context_total",
            "Low-cardinality LLM request context",
            ["model", "operation"],
            registry=self.registry,
        )

        # Email metrics
        self.emails_total = Counter(
            "emails_total",
            "Total number of emails processed",
            ["status"],  # fetched, filtered, normalized, failed
            registry=self.registry,
        )

        # Pipeline metrics
        self.digest_build_seconds = Summary(
            "digest_build_seconds", "Time spent building digest", registry=self.registry
        )

        self.runs_total = Counter(
            "runs_total",
            "Total digest runs",
            ["status"],  # ok, retry, failed
            registry=self.registry,
        )

        # Evidence metrics
        self.evidence_chunks_total = Counter(
            "evidence_chunks_total",
            "Total evidence chunks processed",
            ["stage"],  # created, selected, processed
            registry=self.registry,
        )

        # Thread metrics
        self.threads_total = Counter(
            "threads_total",
            "Total conversation threads processed",
            ["status"],  # created, filtered, prioritized
            registry=self.registry,
        )

        # System metrics
        self.system_uptime_seconds = Gauge(
            "system_uptime_seconds", "System uptime in seconds", registry=self.registry
        )

        self.memory_usage_bytes = Gauge(
            "memory_usage_bytes", "Memory usage in bytes", registry=self.registry
        )

        # Pipeline stage metrics
        self.pipeline_stage_duration = Histogram(
            "pipeline_stage_duration_seconds",
            "Duration of pipeline stages",
            ["stage"],  # ingest, normalize, threads, evidence, select, llm, assemble
            registry=self.registry,
        )

        # Error metrics
        self.errors_total = Counter(
            "errors_total",
            "Total errors by type",
            ["error_type", "stage"],  # validation_error, llm_error, etc.
            registry=self.registry,
        )

        # Email cleaner metrics
        self.email_cleaner_removed_chars_total = Counter(
            "email_cleaner_removed_chars_total",
            "Total characters removed by email cleaner",
            ["removal_type"],  # quoted, signature, disclaimer, autoresponse
            registry=self.registry,
        )

        self.email_cleaner_removed_blocks_total = Counter(
            "email_cleaner_removed_blocks_total",
            "Total text blocks removed by email cleaner",
            ["removal_type"],  # quoted, signature, disclaimer, autoresponse
            registry=self.registry,
        )

        self.cleaner_errors_total = Counter(
            "cleaner_errors_total",
            "Total email cleaner errors",
            ["error_type"],  # regex_error, parse_error, etc.
            registry=self.registry,
        )

        # Citation metrics
        self.citations_per_item_histogram = Histogram(
            "citations_per_item_histogram",
            "Number of citations per digest item",
            buckets=[0, 1, 2, 3, 5, 10],
            registry=self.registry,
        )

        self.citation_validation_failures_total = Counter(
            "citation_validation_failures_total",
            "Total citation validation failures",
            ["failure_type"],  # offset_invalid, checksum_mismatch, not_found, etc.
            registry=self.registry,
        )

        # P2 shadow gate metrics (PR8)
        self.citation_support_score_histogram = Histogram(
            "citation_support_score_histogram",
            "Reranker support score per gated digest item",
            buckets=[0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0],
            registry=self.registry,
        )
        self.citation_weak_evidence_total = Counter(
            "citation_weak_evidence_total",
            "Total items annotated weak_evidence by the P2 gate",
            registry=self.registry,
        )
        self.reranker_calls_total = Counter(
            "reranker_calls_total",
            "Total reranker (/rerank) calls made by the P2 gate",
            registry=self.registry,
        )

        # Action/mention extraction metrics
        self.actions_found_total = Counter(
            "actions_found_total",
            "Total actions found by type",
            ["action_type"],  # action, question, mention
            registry=self.registry,
        )

        self.mentions_found_total = Counter(
            "mentions_found_total", "Total user mentions found", registry=self.registry
        )

        self.actions_confidence_histogram = Histogram(
            "actions_confidence_histogram",
            "Confidence score distribution for extracted actions",
            buckets=[0.0, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0],
            registry=self.registry,
        )

        self.actions_sender_missing_total = Counter(
            "actions_sender_missing_total",
            "Total number of actions extracted with missing sender",
            registry=self.registry,
        )

        # Threading metrics
        self.threads_merged_total = Counter(
            "threads_merged_total",
            "Total threads merged by method",
            ["merge_method"],  # by_id, by_subject, by_semantic
            registry=self.registry,
        )

        self.subject_normalized_total = Counter(
            "subject_normalized_total",
            "Total email subjects normalized",
            registry=self.registry,
        )

        self.redundancy_index = Gauge(
            "redundancy_index",
            "Message redundancy reduction ratio (0.0-1.0)",
            registry=self.registry,
        )

        self.duplicates_found_total = Counter(
            "duplicates_found_total",
            "Total duplicate messages found by checksum",
            registry=self.registry,
        )

        # Ranking metrics
        self.rank_score_histogram = Histogram(
            "rank_score_histogram",
            "Distribution of ranking scores for digest items",
            buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            registry=self.registry,
        )

        self.top10_actions_share = Gauge(
            "top10_actions_share",
            "Share of actionable items in top 10 positions (0.0-1.0)",
            registry=self.registry,
        )

        self.ranking_enabled = Gauge(
            "ranking_enabled",
            "Whether ranking is enabled (1=enabled, 0=disabled)",
            registry=self.registry,
        )

        # Hierarchical orchestration metrics
        self.hierarchical_runs_total = Counter(
            "hierarchical_runs_total",
            "Total hierarchical digest runs",
            ["trigger_reason"],  # auto_threads, auto_emails, manual
            registry=self.registry,
        )

        self.avg_subsummary_chunks = Gauge(
            "avg_subsummary_chunks",
            "Average number of chunks per thread subsummary",
            registry=self.registry,
        )

        self.saved_tokens_total = Counter(
            "saved_tokens_total",
            "Total tokens saved by skipping LLM calls",
            ["skip_reason"],  # no_evidence, empty_selection
            registry=self.registry,
        )

        self.must_include_chunks_total = Counter(
            "must_include_chunks_total",
            "Total must-include chunks added",
            ["chunk_type"],  # mentions, last_update
            registry=self.registry,
        )

        # HTML normalization metrics
        self.html_parse_errors_total = Counter(
            "html_parse_errors_total",
            "Total HTML parsing errors",
            ["error_type"],  # bs4_error, malformed_html, fallback_used
            registry=self.registry,
        )

        self.html_hidden_removed_total = Counter(
            "html_hidden_removed_total",
            "Total hidden elements removed from HTML",
            ["element_type"],  # tracking_pixel, display_none, visibility_hidden, style_script_svg
            registry=self.registry,
        )

        # New metrics for robustness features
        self.llm_json_error_total = Counter(
            "llm_json_error_total",
            "Total LLM JSON parsing errors",
            registry=self.registry,
        )

        self.llm_repair_fail_total = Counter(
            "llm_repair_fail_total",
            "Total LLM JSON repair failures",
            registry=self.registry,
        )

        self.tz_naive_total = Counter(
            "tz_naive_total", "Total naive datetime encounters", registry=self.registry
        )

        self.degradations_total = Counter(
            "degradations_total",
            "Total degradation activations",
            ["reason"],  # llm_failed, json_invalid, etc.
            registry=self.registry,
        )

        self.validation_error_total = Counter(
            "validation_error_total",
            "Total validation errors",
            ["type"],  # schema, format, required_field, etc.
            registry=self.registry,
        )

    def start_server(self, port: int | None = None):
        """Start the Prometheus endpoint if it is not already active.

        A bind failure is recorded in ``exporter_error`` and logged at **error**
        (it used to be a swallowed warning — the root cause of the April
        "metrics not available" incident, frontier-audit F6). The run keeps
        going by default (oneshot batch, degrade-not-drop); set
        ``observability.fail_on_exporter_error`` to crash instead.
        """
        port = port or self.port
        self.port = port
        if port in self.__class__._active_ports or self._server_started:
            return
        try:
            start_http_server(port, registry=self.registry)
            self.__class__._active_ports.add(port)
            self._server_started = True
            self.exporter_error = None
            logger.info("Prometheus metrics server started", port=port)
        except Exception as e:
            self.exporter_error = str(e)
            logger.error(
                "Failed to start metrics exporter — run continues but is unobservable "
                "via Prometheus; see observability.fail_on_exporter_error",
                port=port,
                error=str(e),
            )
            if self._fail_on_exporter_error:
                raise

    def exporter_status(self) -> dict:
        """Exporter state for the run trace (``run_meta['metrics_exporter']``)."""
        if self._server_started or self.port in self.__class__._active_ports:
            status = "ok"
        elif self.exporter_error:
            status = "failed"
        else:
            status = "not_started"
        return {"status": status, "port": self.port, "error": self.exporter_error}

    def stop_server(self):
        """Compatibility no-op for tests and older callers."""
        logger.debug("Metrics stop_server is a no-op", port=self.port)

    def record_llm_latency(
        self,
        latency_ms: float,
        model: str = "unknown",
        operation: str = "unknown",
    ):
        """Record LLM request latency."""
        self.llm_latency_ms.observe(max(latency_ms, 0))
        self.llm_request_context_total.labels(model=model, operation=operation).inc()
        logger.debug(
            "Recorded LLM latency",
            latency_ms=latency_ms,
            model=model,
            operation=operation,
        )

    def record_llm_tokens(self, tokens_in: int, tokens_out: int, model: str = "unknown"):
        """Record LLM token usage."""
        self.llm_tokens_in_total.inc(max(tokens_in, 0))
        self.llm_tokens_out_total.inc(max(tokens_out, 0))
        logger.debug(
            "Recorded LLM tokens",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
        )

    def record_emails_total(self, count: int, status: str = "processed"):
        """Record email processing metrics."""
        self.emails_total.labels(status=status).inc(max(count, 0))
        logger.debug("Recorded email metrics", count=count, status=status)

    def record_digest_build_time(self, duration_seconds: float | None = None):
        """Record digest build time."""
        build_time = (
            max(duration_seconds, 0)
            if duration_seconds is not None
            else time.time() - self.start_time
        )
        self.digest_build_seconds.observe(build_time)
        logger.debug("Recorded digest build time", build_time=build_time)

    def record_run_total(self, status: str):
        """Record run status."""
        self.runs_total.labels(status=status).inc()
        logger.debug("Recorded run status", status=status)

    def record_evidence_chunks(self, count: int, stage: str):
        """Record evidence chunk metrics."""
        self.evidence_chunks_total.labels(stage=stage).inc(count)
        logger.debug("Recorded evidence chunks", count=count, stage=stage)

    def record_threads(self, count: int, status: str):
        """Record thread metrics."""
        self.threads_total.labels(status=status).inc(count)
        logger.debug("Recorded threads", count=count, status=status)

    def record_pipeline_stage_duration(self, stage: str, duration_seconds: float):
        """Record pipeline stage duration."""
        self.pipeline_stage_duration.labels(stage=stage).observe(duration_seconds)
        logger.debug("Recorded pipeline stage duration", stage=stage, duration=duration_seconds)

    def record_error(self, error_type: str, stage: str):
        """Record error occurrence."""
        self.errors_total.labels(error_type=error_type, stage=stage).inc()
        logger.debug("Recorded error", error_type=error_type, stage=stage)

    def record_cleaner_removed_chars(self, char_count: int, removal_type: str):
        """Record characters removed by email cleaner."""
        self.email_cleaner_removed_chars_total.labels(removal_type=removal_type).inc(char_count)
        logger.debug(
            "Recorded cleaner removed chars",
            char_count=char_count,
            removal_type=removal_type,
        )

    def record_cleaner_removed_blocks(self, block_count: int, removal_type: str):
        """Record blocks removed by email cleaner."""
        self.email_cleaner_removed_blocks_total.labels(removal_type=removal_type).inc(block_count)
        logger.debug(
            "Recorded cleaner removed blocks",
            block_count=block_count,
            removal_type=removal_type,
        )

    def record_cleaner_error(self, error_type: str):
        """Record email cleaner error."""
        self.cleaner_errors_total.labels(error_type=error_type).inc()
        logger.debug("Recorded cleaner error", error_type=error_type)

    def record_citations_per_item(self, citation_count: int):
        """Record number of citations per digest item."""
        self.citations_per_item_histogram.observe(citation_count)
        logger.debug("Recorded citations per item", citation_count=citation_count)

    def record_citation_validation_failure(self, failure_type: str):
        """Record citation validation failure."""
        self.citation_validation_failures_total.labels(failure_type=failure_type).inc()
        logger.debug("Recorded citation validation failure", failure_type=failure_type)

    def record_citation_support_score(self, score: float):
        """Record a P2-gate reranker support score."""
        self.citation_support_score_histogram.observe(score)

    def record_citation_weak_evidence(self):
        """Record an item annotated weak_evidence by the P2 gate."""
        self.citation_weak_evidence_total.inc()

    def record_reranker_call(self):
        """Record a reranker (/v1/score) call from the P2 gate."""
        self.reranker_calls_total.inc()

    def record_action_found(self, action_type: str):
        """Record action found by type (action/question/mention)."""
        self.actions_found_total.labels(action_type=action_type).inc()
        logger.debug("Recorded action found", action_type=action_type)

    def record_mention_found(self):
        """Record user mention found."""
        self.mentions_found_total.inc()
        logger.debug("Recorded mention found")

    def record_action_confidence(self, confidence: float):
        """Record action confidence score."""
        self.actions_confidence_histogram.observe(confidence)
        logger.debug("Recorded action confidence", confidence=confidence)

    def record_action_sender_missing(self):
        """Record action extracted with missing sender."""
        self.actions_sender_missing_total.inc()
        logger.debug("Recorded action sender missing")

    def record_thread_merged(self, merge_method: str):
        """Record thread merge by method (by_id/by_subject/by_semantic)."""
        self.threads_merged_total.labels(merge_method=merge_method).inc()
        logger.debug("Recorded thread merge", merge_method=merge_method)

    def record_subject_normalized(self, count: int = 1):
        """Record subject normalization."""
        self.subject_normalized_total.inc(count)
        logger.debug("Recorded subject normalization", count=count)

    def update_redundancy_index(self, redundancy: float):
        """Update redundancy index gauge."""
        self.redundancy_index.set(redundancy)
        logger.debug("Updated redundancy index", redundancy=redundancy)

    def record_duplicate_found(self, count: int = 1):
        """Record duplicate message found."""
        self.duplicates_found_total.inc(count)
        logger.debug("Recorded duplicate found", count=count)

    def record_rank_score(self, score: float):
        """Record ranking score."""
        self.rank_score_histogram.observe(score)
        logger.debug("Recorded rank score", score=score)

    def update_top10_actions_share(self, share: float):
        """Update share of actions in top 10."""
        self.top10_actions_share.set(share)
        logger.debug("Updated top10 actions share", share=share)

    def set_ranking_enabled(self, enabled: bool):
        """Set ranking enabled status."""
        self.ranking_enabled.set(1.0 if enabled else 0.0)
        logger.debug("Set ranking enabled", enabled=enabled)

    def record_hierarchical_run(self, trigger_reason: str):
        """Record hierarchical digest run."""
        self.hierarchical_runs_total.labels(trigger_reason=trigger_reason).inc()
        logger.debug("Recorded hierarchical run", trigger_reason=trigger_reason)

    def update_avg_subsummary_chunks(self, avg_chunks: float):
        """Update average subsummary chunks gauge."""
        self.avg_subsummary_chunks.set(avg_chunks)
        logger.debug("Updated avg subsummary chunks", avg_chunks=avg_chunks)

    def record_saved_tokens(self, count: int, skip_reason: str):
        """Record tokens saved by optimization."""
        self.saved_tokens_total.labels(skip_reason=skip_reason).inc(count)
        logger.debug("Recorded saved tokens", count=count, skip_reason=skip_reason)

    def record_must_include_chunk(self, chunk_type: str, count: int = 1):
        """Record must-include chunk added."""
        self.must_include_chunks_total.labels(chunk_type=chunk_type).inc(count)
        logger.debug("Recorded must-include chunk", chunk_type=chunk_type, count=count)

    def record_html_parse_error(self, error_type: str):
        """Record HTML parsing error."""
        self.html_parse_errors_total.labels(error_type=error_type).inc()
        logger.debug("Recorded HTML parse error", error_type=error_type)

    def record_html_hidden_removed(self, element_type: str, count: int = 1):
        """Record hidden HTML element removed."""
        self.html_hidden_removed_total.labels(element_type=element_type).inc(count)
        logger.debug("Recorded hidden element removed", element_type=element_type, count=count)

    def update_system_metrics(self):
        """Update system metrics."""
        uptime = time.time() - self.start_time
        self.system_uptime_seconds.set(uptime)

        # Memory usage (simplified)
        try:
            import psutil

            memory_usage = psutil.Process().memory_info().rss
            self.memory_usage_bytes.set(memory_usage)
        except ImportError:
            # psutil not available, skip memory metrics
            pass

        logger.debug("Updated system metrics", uptime=uptime)

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get a summary of current metrics."""
        return {
            "uptime_seconds": time.time() - self.start_time,
            "port": self.port,
            "metrics_available": [
                "llm_latency_ms",
                "llm_tokens_in_total",
                "llm_tokens_out_total",
                "emails_total",
                "digest_build_seconds",
                "runs_total",
                "evidence_chunks_total",
                "threads_total",
                "pipeline_stage_duration",
                "errors_total",
                "email_cleaner_removed_chars_total",
                "email_cleaner_removed_blocks_total",
                "cleaner_errors_total",
                "citations_per_item_histogram",
                "citation_validation_failures_total",
                "actions_found_total",
                "mentions_found_total",
                "actions_confidence_histogram",
                "actions_sender_missing_total",
                "threads_merged_total",
                "subject_normalized_total",
                "redundancy_index",
                "duplicates_found_total",
                "rank_score_histogram",
                "top10_actions_share",
                "ranking_enabled",
                "hierarchical_runs_total",
                "avg_subsummary_chunks",
                "saved_tokens_total",
                "must_include_chunks_total",
                "html_parse_errors_total",
                "html_hidden_removed_total",
            ],
        }

    def health_check(self) -> Dict[str, Any]:
        """Perform health check."""
        try:
            # Check if metrics server is running
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(("localhost", self.port))
            sock.close()

            is_healthy = result == 0

            return {
                "status": "healthy" if is_healthy else "unhealthy",
                "metrics_port": self.port,
                "uptime_seconds": time.time() - self.start_time,
                "timestamp": time.time(),
            }

        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return {"status": "unhealthy", "error": str(e), "timestamp": time.time()}

    def readiness_check(self) -> Dict[str, Any]:
        """Perform readiness check."""
        try:
            # Check if all required components are ready
            uptime = time.time() - self.start_time
            is_ready = uptime > 5  # Require at least 5 seconds uptime

            return {
                "status": "ready" if is_ready else "not_ready",
                "uptime_seconds": uptime,
                "timestamp": time.time(),
            }

        except Exception as e:
            logger.error("Readiness check failed", error=str(e))
            return {"status": "not_ready", "error": str(e), "timestamp": time.time()}

    def record_llm_json_error(self):
        """Record LLM JSON parsing error."""
        self.llm_json_error_total.inc()
        logger.debug("Recorded LLM JSON error")

    def record_llm_repair_failure(self):
        """Record LLM JSON repair failure."""
        self.llm_repair_fail_total.inc()
        logger.debug("Recorded LLM JSON repair failure")

    def record_tz_naive(self):
        """Record naive datetime encounter."""
        self.tz_naive_total.inc()
        logger.debug("Recorded naive datetime encounter")

    def record_degradation(self, reason: str):
        """Record degradation activation."""
        self.degradations_total.labels(reason=reason).inc()
        logger.debug("Recorded degradation", reason=reason)

    def record_validation_error(self, error_type: str):
        """Record validation error."""
        self.validation_error_total.labels(type=error_type).inc()
        logger.debug("Recorded validation error", error_type=error_type)

    def reset_warning_cache(self):
        """Reset warning cache at the start of each batch."""
        self._warning_cache.clear()
        logger.debug("Reset warning cache for new batch")

    def should_warn(self, warning_key: str) -> bool:
        """
        Check if a warning should be shown (rate-limited to 1 per batch).

        Args:
            warning_key: Unique identifier for the warning

        Returns:
            True if warning should be shown, False if it should be suppressed
        """
        if warning_key in self._warning_cache:
            return False
        self._warning_cache.add(warning_key)
        return True

    def get_metric_values(self) -> Dict[str, Any]:
        """Get current metric values for debugging."""
        try:
            from prometheus_client import generate_latest
            from prometheus_client.parser import text_string_to_metric_families

            # Get metrics in text format
            metrics_text = generate_latest(self.registry).decode("utf-8")

            # Parse metrics
            metrics = {}
            for family in text_string_to_metric_families(metrics_text):
                metrics[family.name] = {
                    "type": family.type,
                    "help": family.documentation,
                    "samples": [
                        {
                            "name": sample.name,
                            "labels": sample.labels,
                            "value": sample.value,
                        }
                        for sample in family.samples
                    ],
                }

            return metrics

        except Exception as e:
            logger.error("Failed to get metric values", error=str(e))
            return {}
