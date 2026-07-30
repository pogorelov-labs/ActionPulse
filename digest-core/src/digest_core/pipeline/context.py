"""
The run context — the object every pipeline stage is handed.

Extracted from ``run.py`` (ACTPULSE-23 phase 3). It lives here rather than in
``run`` for a mechanical reason: **31 definitions reference it**, so any module
carved out of ``run`` needs it, and importing it back from ``run`` would make
every such module circular. Owning it here is what lets ``pipeline/`` grow.

``run.py`` re-exports ``RunContext`` under its historical name, so
``runner.RunContext(...)`` in the tests and ``from digest_core.run import
RunContext`` keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from digest_core.config import Config
from digest_core.llm.rate_broker import RateBroker
from digest_core.observability.metrics import MetricsCollector
from digest_core.progress import NullSink, ProgressSink


@dataclass
class RunContext:
    """Mutable context threaded through all pipeline stages."""

    trace_id: str
    config: Config
    metrics: MetricsCollector
    digest_date: str
    output_dir: Path
    json_path: Path
    md_path: Path
    metadata_path: Path
    dry_run: bool
    force: bool
    validate_citations: bool
    dump_ingest: str | None
    replay_ingest: str | None
    record_llm: str | None
    replay_llm: str | None
    rate_broker: Optional[RateBroker] = None
    log_file: Any = None
    run_meta: Dict[str, Any] = field(default_factory=dict)
    sink: ProgressSink = field(default_factory=NullSink)
    #: Source selector (``--sources``). Defaults to the single EWS source so the
    #: existing live path and tests that build a RunContext without specifying
    #: sources are unchanged. ``_stage_ingest`` builds the adapter list from this.
    sources: List[str] = field(default_factory=lambda: ["ews"])
    #: Incremental load: True for a normal "today" run (per-source watermarks
    #: narrow each source's fetch to "since last seen"); False for an explicit
    #: back-dated run, so a back-fill fetches the full requested window.
    incremental: bool = True
