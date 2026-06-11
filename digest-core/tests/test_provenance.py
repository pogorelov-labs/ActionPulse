"""Per-run provenance manifest (enhancement program EP-1, frontier-audit F10)."""

import hashlib
import json
import re
from pathlib import Path

import digest_core.provenance as provenance_mod
from digest_core import run as runner
from digest_core.config import Config
from digest_core.provenance import build_provenance, prompt_sha256, resolve_code_sha
from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


def _failing_run(*args, **kwargs):
    raise FileNotFoundError("git not available")


def test_resolve_code_sha_prefers_git_when_available():
    sha, source = resolve_code_sha()
    if source == "git":
        assert re.fullmatch(r"[0-9a-f]{40}", sha)
    else:  # degraded environment (no git) — fallback contract still holds
        assert source in ("env", "unknown")


def test_resolve_code_sha_env_fallback(monkeypatch):
    monkeypatch.setattr(provenance_mod.subprocess, "run", _failing_run)
    monkeypatch.setenv("ACTIONPULSE_CODE_SHA", "deadbeefcafe")
    assert resolve_code_sha() == ("deadbeefcafe", "env")


def test_resolve_code_sha_unknown_never_raises(monkeypatch):
    monkeypatch.setattr(provenance_mod.subprocess, "run", _failing_run)
    monkeypatch.delenv("ACTIONPULSE_CODE_SHA", raising=False)
    assert resolve_code_sha() == ("unknown", "unknown")


def test_prompt_sha256_is_sha256_of_text():
    assert prompt_sha256("abc") == hashlib.sha256(b"abc").hexdigest()


def test_build_provenance_shape():
    config = Config()
    manifest = build_provenance(config, config_sha256="c" * 64, pipeline_version="9.9.9")
    assert manifest["pipeline_version"] == "9.9.9"
    assert manifest["config_sha256"] == "c" * 64
    assert manifest["model_extractor"] == config.llm.model
    assert manifest["code_sha_source"] in ("git", "env", "unknown")
    assert set(manifest["flags"]) == {"ranker_enabled", "degrade_enabled", "mattermost_enabled"}
    # prompt fields are resolved later, at the LLM stage
    assert manifest["prompt_id"] is None
    assert manifest["prompt_sha256"] is None


def test_trace_meta_carries_provenance(monkeypatch, tmp_path):
    """E2E (replay): the written trace-*.meta.json contains the full manifest."""
    snapshot_path = tmp_path / "snapshot.json"
    out_dir = tmp_path / "out"
    runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")

    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", FakeGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

    result = runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(out_dir),
        model="qwen35-397b-a17b",
        window="calendar_day",
        state=str(tmp_path / "state"),
        force=True,
        replay_ingest=str(snapshot_path),
    )
    assert result

    meta = json.loads(next(out_dir.glob("trace-*.meta.json")).read_text(encoding="utf-8"))
    manifest = meta["provenance"]
    assert manifest["code_sha_source"] in ("git", "env", "unknown")
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["config_sha256"])
    assert manifest["model_extractor"] == "qwen35-397b-a17b"
    # LLM stage ran → prompt identity is pinned to the exact bytes used
    assert manifest["prompt_id"] == "extract_actions.en.v1"
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["prompt_sha256"])
