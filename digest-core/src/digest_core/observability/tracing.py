"""OpenTelemetry GenAI tracing (EP-8, frontier-audit F6).

Two-level span structure per the GenAI semconv: a root run span parents
per-stage spans, and the LLM-call span carries the ``gen_ai.*`` attributes —
so a trace explains a whole run with **no payload ever recorded**.

Guardrails (override the spec where they conflict):

- **Decisions, never content.** The semconv's optional content capture
  (``gen_ai.input.messages``/``gen_ai.output.messages``) stays OFF — only
  structural attributes (model, token counts, finish reasons, stage status).
- **Identifiers sanitized:** ``gen_ai.system`` is a stable gateway id, never a
  URL; resource attributes carry hashes/versions from the provenance manifest.
- **Zero overhead when off:** ``observability.otel_enabled`` defaults to false;
  the OTel SDK is imported lazily and only then (the ``otel`` extra is optional).
- The provider is module-held (not OTel's set-once global) so tests and oneshot
  runs can configure/reset deterministically.

Offline export: ``otel_export_path`` writes spans as JSON lines (one span per
ConsoleSpanExporter record) — verifiable at home. Shipping spans to a corp
collector is a W3 decision and requires corp validation.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import structlog

logger = structlog.get_logger()

GEN_AI_SYSTEM = "cib-llm-gateway"  # stable id, deliberately not a URL/hostname

_provider = None
_tracer = None
_run_span = None
_export_handle = None


def configure_tracing(observability_config, resource_attributes: Dict[str, Any]) -> bool:
    """Set up the module tracer. Returns True when tracing is active.

    No-op (False) when the flag is off or the ``otel`` extra is not installed —
    a missing optional dependency must never break the digest run.
    """
    global _provider, _tracer, _export_handle
    if not getattr(observability_config, "otel_enabled", False):
        return False
    if _tracer is not None:
        return True  # already configured for this process
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    except ImportError:
        logger.warning(
            "observability.otel_enabled is set but the 'otel' extra is not installed; "
            "tracing disabled (uv sync --extra otel)"
        )
        return False

    resource = Resource.create(
        {"service.name": "actionpulse-digest", **{k: v for k, v in resource_attributes.items()}}
    )
    export_path = getattr(observability_config, "otel_export_path", None)
    if export_path:
        _export_handle = open(export_path, "a", encoding="utf-8")  # noqa: SIM115 — lifetime = run
        exporter = ConsoleSpanExporter(out=_export_handle)
    else:
        exporter = ConsoleSpanExporter()
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _provider = provider
    _tracer = provider.get_tracer("digest_core")
    logger.info("OTel tracing configured", export=export_path or "console")
    return True


def reset_tracing() -> None:
    """Tear down the tracer (tests / end of process)."""
    global _provider, _tracer, _run_span, _export_handle
    if _run_span is not None:
        try:
            _run_span.end()
        except Exception:  # pragma: no cover — best effort on teardown
            pass
        _run_span = None
    if _provider is not None:
        _provider.force_flush()
        _provider.shutdown()
        _provider = None
    _tracer = None
    if _export_handle is not None:
        _export_handle.close()
        _export_handle = None


def start_run_span(trace_id: str, digest_date: str) -> None:
    """Open the root span for this run (ids only — never content)."""
    global _run_span
    if _tracer is None:
        return
    _run_span = _tracer.start_span(
        "digest.run",
        attributes={"actionpulse.trace_id": trace_id, "actionpulse.digest_date": digest_date},
    )


def end_run_span(status: Optional[str]) -> None:
    global _run_span
    if _run_span is None:
        return
    if status:
        _run_span.set_attribute("actionpulse.run_status", status)
    _run_span.end()
    _run_span = None
    if _provider is not None:
        _provider.force_flush()


def record_stage_span(stage: str, duration_seconds: float) -> None:
    """Emit a per-stage span, parented under the run span.

    Stages report their duration at completion, so the span is created
    retroactively with explicit timestamps (wall-clock end minus duration).
    """
    if _tracer is None:
        return
    from opentelemetry import trace as otel_trace

    end_ns = time.time_ns()
    start_ns = end_ns - int(duration_seconds * 1e9)
    context = otel_trace.set_span_in_context(_run_span) if _run_span is not None else None
    span = _tracer.start_span(f"stage.{stage}", context=context, start_time=start_ns)
    span.end(end_time=end_ns)


@contextmanager
def llm_call_span(model: str) -> Iterator[Optional[Any]]:
    """Span around one real LLM HTTP call (``chat {model}`` per the semconv).

    Yields the live span (or None when tracing is off) so the caller can attach
    ``gen_ai.usage.*`` / ``gen_ai.response.finish_reasons`` once known. On an
    exception the span records ``error.type`` and the exception re-raises.
    """
    if _tracer is None:
        yield None
        return
    from opentelemetry import trace as otel_trace

    context = otel_trace.set_span_in_context(_run_span) if _run_span is not None else None
    span = _tracer.start_span(
        f"chat {model}",
        context=context,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": GEN_AI_SYSTEM,
            "gen_ai.request.model": model,
        },
    )
    try:
        yield span
    except Exception as exc:
        span.set_attribute("error.type", type(exc).__name__)
        raise
    finally:
        span.end()
