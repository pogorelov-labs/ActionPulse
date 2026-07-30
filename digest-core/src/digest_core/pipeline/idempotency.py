"""Idempotency: decide whether a run can skip work it has already done.

Extracted from ``run.py`` (ACTPULSE-23, phase 1). STATUS deferred this for months
on a recorded "real circular-import risk". There is none, for a narrower reason
than "nothing imports run": four modules do (``cli`` at import time; ``daemon/tick``,
``eval/corpus`` and ``eval/best_of_n_harness`` inside functions). The point is that
this module's own dependencies — ``Config`` and ``NormalizedMessage`` — do not import
``run``, so a leaf here closes no loop. Keeping ``pipeline/`` free of ``run`` imports
is what preserves that, and a test enforces it.

The one genuine entanglement was ``PIPELINE_VERSION`` living in ``run``; it lives
here now, beside its only reader, and ``run`` re-exports it.

Two skip paths, both bypassed by ``run --force``:

* **pre-ingest** — artifacts younger than 48h AND a matching config+pipeline
  version. Cheap: avoids even talking to EWS.
* **post-ingest** — config, *content* and pipeline version all match, so the
  scarce extractor call is spared even past 48h.

A config or pipeline-version change always forces a rebuild, which is why
``PIPELINE_VERSION`` belongs beside this logic rather than three screens away
from it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import structlog

from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage

logger = structlog.get_logger()

#: Bumped when a pipeline change makes previous artifacts non-comparable; a
#: mismatch forces a rebuild. Lives here because idempotency is its only reader.
PIPELINE_VERSION = "1.2.0"


def _artifact_age_hours(path: Path) -> float:
    """Hours since *path* was last written (``inf`` when it does not exist)."""
    import time

    if not path.exists():
        return float("inf")
    return (time.time() - path.stat().st_mtime) / 3600.0


def _should_skip_existing_artifacts(json_path: Path, md_path: Path) -> bool:
    """True when both artifacts exist and are younger than the 48h freshness window."""
    return (
        json_path.exists()
        and md_path.exists()
        and _artifact_age_hours(json_path) < 48
        and _artifact_age_hours(md_path) < 48
    )


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
