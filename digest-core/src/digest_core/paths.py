"""One data home for everything regenerable (roadmap U5).

Before U5 runtime files scattered: digests to cwd-relative ``./out``, logs to
``~/.digest-logs``, the EWS sync watermark to cwd-relative ``.state`` (a real
bug — the incremental window silently reset whenever the user ran from a
different directory). Now everything regenerable lives under one **data
home**::

    <data home>/var/out     digests, trace meta, idempotency sidecars
    <data home>/var/logs    structured run logs
    <data home>/var/state   EWS sync watermark, dedup ledger, last_run.json

Resolution order: ``$ACTIONPULSE_HOME`` → the install checkout root (the
``~/ActionPulse`` a one-liner install creates) → ``~/.local/share/actionpulse``
(wheel installs must never write into site-packages). Explicit ``--out`` /
``--state`` / config values always win over these defaults.

Deliberate exceptions (documented in README/roadmap): the secrets env stays at
``~/.config/actionpulse/env`` — inside the checkout ``git clean -xdf`` would
delete the only copy of the user's tokens and an accidental ``git add -f``
could leak them; the CA chain stays in ``~/.ssl`` (TLS convention); the
launcher must stay on PATH.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

#: digest-core/ (the uv project dir; same anchor config.py uses).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_home() -> Path:
    """The single root for regenerable runtime files (no side effects)."""
    override = os.environ.get("ACTIONPULSE_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    repo_root = PROJECT_ROOT.parent
    # A real checkout (one-liner install / git clone): keep data next to the
    # code, where the owner expects it. Wheel installs lack these markers.
    if (PROJECT_ROOT / "pyproject.toml").exists() and (repo_root / "install.sh").exists():
        return repo_root
    return Path.home() / ".local" / "share" / "actionpulse"


def _var(sub: str, create: bool) -> Path:
    path = data_home() / "var" / sub
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def out_dir(create: bool = True) -> Path:
    """Default digest artifact directory (``--out`` overrides)."""
    return _var("out", create)


def logs_dir(create: bool = True) -> Path:
    """Default structured-log directory (``--log-file`` overrides)."""
    return _var("logs", create)


def state_dir(create: bool = True) -> Path:
    """Default state directory: sync watermark, dedup ledger, last_run.json."""
    return _var("state", create)


#: Display labels for describe() keys — shared by `actionpulse paths` and the
#: menu config view so the two surfaces never drift.
LABELS: Dict[str, str] = {
    "data_home": "Data home",
    "digests": "Digests",
    "logs": "Logs",
    "state": "State",
    "config": "Config",
    "secrets_env": "Secrets env",
    "launcher": "Launcher",
}


def describe() -> Dict[str, str]:
    """The full path map for `actionpulse paths` / the menu config view.

    Keys are stable identifiers; values are display paths. Includes the
    deliberate out-of-home exceptions so "where is everything" has one answer.
    """
    return {
        "data_home": str(data_home()),
        "digests": str(out_dir(create=False)),
        "logs": str(logs_dir(create=False)),
        "state": str(state_dir(create=False)),
        "config": str(PROJECT_ROOT / "configs" / "config.yaml"),
        "secrets_env": str(Path.home() / ".config" / "actionpulse" / "env"),
        "launcher": str(Path.home() / ".local" / "bin" / "actionpulse"),
    }
