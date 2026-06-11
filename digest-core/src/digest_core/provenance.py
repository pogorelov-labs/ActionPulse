"""Per-run provenance manifest (enhancement program EP-1, frontier-audit F10).

Lets a `trace-*.meta.json` answer "which code, prompt, and config produced this
digest" on its own — the system-provenance counterpart to the P2
evidence-provenance guarantee. Identifiers and hashes only; never payload data.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import Any, Dict

from digest_core.config import Config, PROJECT_ROOT


def resolve_code_sha() -> tuple[str, str]:
    """Best-effort code revision: git checkout → ACTIONPULSE_CODE_SHA env → unknown.

    Never raises — production may run from a Docker image or an exported tree
    where git is absent, and provenance must not be able to break the run.
    The env fallback is meant to be set by the image build (build arg).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
        )
        sha = result.stdout.strip()
        if result.returncode == 0 and len(sha) == 40:
            return sha, "git"
    except Exception:
        pass
    env_sha = os.getenv("ACTIONPULSE_CODE_SHA", "").strip()
    if env_sha:
        return env_sha, "env"
    return "unknown", "unknown"


def prompt_sha256(prompt_text: str) -> str:
    """Hash of the exact prompt text sent to the LLM (prompts are edited in-place)."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


def build_provenance(
    config: Config, *, config_sha256: str, pipeline_version: str
) -> Dict[str, Any]:
    """Assemble the per-run provenance manifest for ``run_meta``.

    ``prompt_id`` / ``prompt_sha256`` stay None here; the LLM stage fills them in
    once the model→prompt mapping is resolved (dry runs and empty-evidence runs
    legitimately never resolve a prompt).
    """
    code_sha, code_sha_source = resolve_code_sha()
    return {
        "code_sha": code_sha,
        "code_sha_source": code_sha_source,
        "pipeline_version": pipeline_version,
        "model_extractor": config.llm.model,
        "config_sha256": config_sha256,
        "flags": {
            "ranker_enabled": config.ranker.enabled,
            "degrade_enabled": config.degrade.enable,
            "mattermost_enabled": config.deliver.mattermost.enabled,
        },
        "prompt_id": None,
        "prompt_sha256": None,
    }
