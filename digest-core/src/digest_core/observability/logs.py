"""
Structured logging setup using structlog with run_id/trace_id support.
"""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import sys
from typing import Any, Dict, Optional
import uuid

import structlog

_CONFIGURED_LOG_FILE: Optional[Path] = None


def setup_logging(log_level: str = "INFO", log_file: str = None, console: bool = True) -> Path:
    """Setup structured logging with structlog.

    console=False keeps JSON logs in the file only — used when a progress
    renderer owns the terminal (TERMINAL_DESIGN.md P3: the live channel
    renders state, log lines never scroll through it).
    """
    global _CONFIGURED_LOG_FILE

    if _CONFIGURED_LOG_FILE is not None and logging.getLogger().handlers:
        return _CONFIGURED_LOG_FILE

    # Create log directory if it doesn't exist
    log_dir = _resolve_log_dir()

    # Generate log file name if not provided
    if log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = log_dir / f"run-{timestamp}.log"
    else:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)

    # Configure standard library logging with both console and file output
    handlers = [logging.StreamHandler(sys.stdout)] if console else []
    try:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError:
        fallback_dir = Path("/tmp/digest-logs")
        fallback_dir.mkdir(parents=True, exist_ok=True)
        log_file = fallback_dir / log_file.name
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=getattr(logging, log_level.upper()),
    )

    _CONFIGURED_LOG_FILE = Path(log_file)

    # Log the log file location
    print(f"Log file: {log_file}")

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            _redact_sensitive_data,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    return _CONFIGURED_LOG_FILE


def get_configured_log_file() -> Optional[Path]:
    """Return the active log file path, if logging has already been configured."""
    return _CONFIGURED_LOG_FILE


def _resolve_log_dir() -> Path:
    """Pick a writable directory for structured logs.

    The data home (U5: ``<data home>/var/logs``) is the default; the legacy
    locations remain as fallbacks for environments where it is unwritable.
    """
    from digest_core.paths import logs_dir

    candidates = [
        lambda: logs_dir(),
        lambda: Path.home() / ".digest-logs",
        lambda: Path("/tmp/digest-logs"),
    ]
    for make_candidate in candidates:
        try:
            candidate = make_candidate()
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    raise OSError("Unable to create a writable log directory")


def _redact_sensitive_data(logger, method_name, event_dict):
    """Redact sensitive data from log entries."""

    # Fields to redact
    sensitive_fields = [
        "password",
        "token",
        "secret",
        "key",
        "auth",
        "email",
        "phone",
        "ssn",
        "credit_card",
    ]

    # Patterns to redact
    sensitive_patterns = [
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Email
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit card
    ]

    # Redact sensitive fields
    for field in sensitive_fields:
        if field in event_dict:
            event_dict[field] = "[[REDACTED]]"

    # Redact sensitive patterns in string values
    for key, value in event_dict.items():
        if isinstance(value, str):
            for pattern in sensitive_patterns:
                import re

                if re.search(pattern, value):
                    event_dict[key] = re.sub(pattern, "[[REDACTED]]", value)

    return event_dict


def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a structured logger."""
    return structlog.get_logger(name)


def log_pipeline_stage(stage: str, run_id: str = None, trace_id: str = None, **kwargs) -> None:
    """Log a pipeline stage with context."""
    logger = get_logger()
    context = {"stage": stage}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info(f"Pipeline stage: {stage}", **context, **kwargs)


def log_error_with_context(
    error: Exception, run_id: str = None, trace_id: str = None, **kwargs
) -> None:
    """Log an error with context."""
    logger = get_logger()
    context = {"error": str(error)}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.error("Pipeline error", **context, exc_info=True, **kwargs)


def log_metrics(metrics: Dict[str, Any], run_id: str = None, trace_id: str = None) -> None:
    """Log metrics data."""
    logger = get_logger()
    context = {}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Pipeline metrics", **context, **metrics)


def log_llm_request(
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    run_id: str = None,
    trace_id: str = None,
    **kwargs,
) -> None:
    """Log LLM request details."""
    logger = get_logger()
    context = {
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "latency_ms": latency_ms,
    }

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("LLM request completed", **context, **kwargs)


def log_email_processing(
    count: int, status: str, run_id: str = None, trace_id: str = None, **kwargs
) -> None:
    """Log email processing details."""
    logger = get_logger()
    context = {"email_count": count, "status": status}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Email processing completed", **context, **kwargs)


def log_evidence_processing(
    count: int, stage: str, run_id: str = None, trace_id: str = None, **kwargs
) -> None:
    """Log evidence processing details."""
    logger = get_logger()
    context = {"evidence_count": count, "stage": stage}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Evidence processing completed", **context, **kwargs)


def log_digest_completion(
    sections_count: int,
    total_items: int,
    run_id: str = None,
    trace_id: str = None,
    **kwargs,
) -> None:
    """Log digest completion details."""
    logger = get_logger()
    context = {"sections_count": sections_count, "total_items": total_items}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Digest completed", **context, **kwargs)


def log_run_start(
    from_date: str,
    sources: list,
    out: str,
    model: str,
    run_id: str = None,
    trace_id: str = None,
) -> None:
    """Log run start details."""
    logger = get_logger()
    context = {
        "from_date": from_date,
        "sources": sources,
        "output_dir": out,
        "model": model,
    }

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Digest run started", **context)


def log_run_completion(
    status: str,
    duration_seconds: float,
    run_id: str = None,
    trace_id: str = None,
    **kwargs,
) -> None:
    """Log run completion details."""
    logger = get_logger()
    context = {"status": status, "duration_seconds": duration_seconds}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Digest run completed", **context, **kwargs)


def generate_run_id() -> str:
    """Generate a unique run ID."""
    return str(uuid.uuid4())


def generate_trace_id() -> str:
    """Generate a unique trace ID."""
    return str(uuid.uuid4())


def get_contextual_logger(run_id: str = None, trace_id: str = None) -> structlog.BoundLogger:
    """Get a logger with run_id and trace_id context."""
    logger = get_logger()

    context = {}
    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    return logger.bind(**context)


def log_configuration(
    config_dict: Dict[str, Any], run_id: str = None, trace_id: str = None
) -> None:
    """Log configuration details (with sensitive data redacted)."""
    logger = get_logger()

    # Redact sensitive configuration values
    redacted_config = {}
    for key, value in config_dict.items():
        if any(sensitive in key.lower() for sensitive in ["password", "token", "secret", "key"]):
            redacted_config[key] = "[[REDACTED]]"
        else:
            redacted_config[key] = value

    context = {"config": redacted_config}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Configuration loaded", **context)


def log_performance_metrics(
    metrics: Dict[str, Any], run_id: str = None, trace_id: str = None
) -> None:
    """Log performance metrics."""
    logger = get_logger()
    context = {"performance_metrics": metrics}

    if run_id:
        context["run_id"] = run_id
    if trace_id:
        context["trace_id"] = trace_id

    logger.info("Performance metrics", **context)
