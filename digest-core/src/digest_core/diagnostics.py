"""Diagnostic bundle export helpers."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
from typing import Any, Dict, Optional

import httpx
import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def export_diagnostics(
    *,
    trace_id: Optional[str],
    out_dir: Path,
    date: Optional[str] = None,
    send_mm: bool = False,
) -> Path:
    """Export a redacted diagnostic bundle for a pipeline run."""
    metadata_path = _find_metadata(trace_id=trace_id, date=date)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    bundle_trace_id = metadata["trace_id"]
    bundle_date = metadata["digest_date"]
    archive_path = out_dir.expanduser() / f"diagnostic-{bundle_trace_id}-{bundle_date}.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_dir = Path(tmpdir) / f"diagnostic-{bundle_trace_id}-{bundle_date}"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        _copy_if_exists(Path(metadata.get("log_file") or ""), bundle_dir / "run.log")
        _copy_if_exists(
            Path(metadata["artifact_paths"].get("json", "")),
            bundle_dir / f"digest-{bundle_date}.json",
        )
        _copy_if_exists(
            Path(metadata["artifact_paths"].get("md", "")),
            bundle_dir / f"digest-{bundle_date}.md",
        )

        (bundle_dir / "pipeline-metrics.json").write_text(
            json.dumps(
                {
                    **metadata.get("pipeline_metrics", {}),
                    "stage_durations_ms": metadata.get("stage_durations_ms", {}),
                    "status": metadata.get("status"),
                    "partial": metadata.get("partial", False),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (bundle_dir / "evidence-summary.json").write_text(
            json.dumps(metadata.get("evidence_summary", {}), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (bundle_dir / "ews-fetch-stats.json").write_text(
            json.dumps(metadata.get("ews_fetch_stats", {}), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (bundle_dir / "llm-request-trace.json").write_text(
            json.dumps(metadata.get("llm_request_trace", {}), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (bundle_dir / "config-sanitized.yaml").write_text(
            yaml.safe_dump(
                metadata.get("config_sanitized", {}),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (bundle_dir / "env-info.txt").write_text(_build_env_info(), encoding="utf-8")

        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(bundle_dir, arcname=bundle_dir.name)

    if send_mm:
        _notify_mattermost(archive_path, metadata)

    return archive_path


def _find_metadata(*, trace_id: Optional[str], date: Optional[str]) -> Path:
    matches = []
    for root in _iter_search_roots():
        if not root.exists():
            continue
        if trace_id:
            matches.extend(root.rglob(f"trace-{trace_id}.meta.json"))
            continue
        matches.extend(root.rglob("trace-*.meta.json"))

    if date:
        matches = [
            path
            for path in matches
            if json.loads(path.read_text(encoding="utf-8")).get("digest_date") == date
        ]

    if not matches:
        identifier = trace_id or date or "latest run"
        raise FileNotFoundError(f"No diagnostic metadata found for {identifier}")

    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def _iter_search_roots() -> tuple[Path, ...]:
    """Compute search roots at call time so cwd-sensitive commands work."""
    from digest_core.paths import out_dir

    return (out_dir(create=False), Path.cwd(), PACKAGE_ROOT, PACKAGE_ROOT.parent)


def _copy_if_exists(source: Path, target: Path) -> None:
    if source.exists():
        shutil.copy2(source, target)


def _build_env_info() -> str:
    packages = []
    for package_name in (
        "httpx",
        "pydantic",
        "structlog",
        "tenacity",
        "beautifulsoup4",
    ):
        try:
            version = importlib.metadata.version(package_name)
            packages.append(f"{package_name}: {version}")
        except importlib.metadata.PackageNotFoundError:
            packages.append(f"{package_name}: not installed")

    lines = [
        f"python: {sys.version}",
        f"platform: {platform.platform()}",
        *packages,
    ]
    return "\n".join(lines) + "\n"


def _notify_mattermost(archive_path: Path, metadata: Dict[str, Any]) -> None:
    """Send a webhook notification about the created bundle."""
    webhook_url = (
        metadata.get("config_sanitized", {})
        .get("deliver", {})
        .get("mattermost", {})
        .get("webhook_url_env")
    )
    env_var_name = webhook_url or "MM_WEBHOOK_URL"
    target = Path(archive_path).expanduser()

    webhook = None
    if env_var_name:
        webhook = __import__("os").environ.get(env_var_name)
    if not webhook:
        return

    text = f"Диагностический bundle готов для trace `{metadata['trace_id']}`.\n" f"Путь: `{target}`"
    httpx.post(webhook, json={"text": text}, timeout=httpx.Timeout(20.0)).raise_for_status()
