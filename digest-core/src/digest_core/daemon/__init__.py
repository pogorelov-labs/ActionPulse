"""Background ingestion daemon — keep the encrypted store fresh without a session.

A macOS launchd LaunchAgent runs ``actionpulse daemon tick`` on an interval; each tick
runs the no-LLM fetch+persist path (the same engine as ``run --dry-run``): Mattermost
every tick, Exchange only when the corp network is reachable (a DNS probe of the EWS
host). A small JSON status file (``<data home>/var/state/daemon.json``) is the single
read surface the CLI, the launcher menu, and the MCP ``daemon_status`` tool all share.

Submodules are imported lazily by callers so a light consumer (e.g. the MCP
``daemon_status`` tool) never pulls the heavy ``run`` pipeline that ``tick`` needs:

* :mod:`digest_core.daemon.status`  — read/write + summarize the status file (light).
* :mod:`digest_core.daemon.tick`    — one ingestion tick (imports ``run``).
* :mod:`digest_core.daemon.launchd` — render + (un)install the LaunchAgent (macOS).

See ``docs/ARCHITECTURE.md`` (ADR — background ingestion daemon).
"""

from __future__ import annotations

__all__ = ["status", "tick", "launchd"]
