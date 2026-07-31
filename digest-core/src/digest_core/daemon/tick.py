"""One background ingestion tick: fetch → persist, single-writer, corp-aware.

Runs the no-LLM fetch+persist path (the ``run --dry-run`` engine): Mattermost every tick;
Exchange only when the corp network is reachable (a DNS probe of the EWS host), so an
off-corp tick skips EWS quietly (exit 0) instead of tripping the strict EWS source. A
non-blocking ``flock`` guarantees a single writer — a tick overlapping a prior tick skips
rather than piling writers on the SQLite store (a manual ``run`` serializes via the store's
WAL + ``busy_timeout``). The daemon keeps its **own** sync watermark (a separate ``state``
dir) so a tick never advances — and thus never starves — the daily digest's window.

Only counts and timestamps are logged or persisted here — never message bodies (P-Golden).
"""

from __future__ import annotations

import fcntl
import os
import socket
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import structlog

from digest_core import paths
from digest_core.config import Config
from digest_core.ingest.source_adapter import MM_SOURCE_NAMES, canonical_source

logger = structlog.get_logger(__name__)

#: Unused in dry-run (no LLM call) — kept equal to the CLI's default for clarity.
_DEFAULT_MODEL = "qwen35-397b-a17b"

#: Transport failures we read as "off-corp / unreachable" → degrade quietly (exit 0),
#: rather than a real fault. The DNS probe gates EWS up front, so this is the rare
#: "resolved but half-up VPN" residue.
_NETWORK_ERRORS: Tuple[type, ...] = (ConnectionError, TimeoutError, socket.gaierror)
try:  # requests-based transports (exchangelib/httpx) subclass RequestException, not builtins
    import requests

    _NETWORK_ERRORS = _NETWORK_ERRORS + (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )
except Exception:  # noqa: BLE001 - import shape must never drive control flow
    pass


class DaemonError(RuntimeError):
    """A misconfiguration that must surface (e.g. the store is disabled)."""


@dataclass
class TickResult:
    ok: bool = True
    skipped: Optional[str] = None  # "locked" | "busy" — another writer held the store
    sources_attempted: List[str] = field(default_factory=list)
    sources_ingested: List[str] = field(default_factory=list)
    #: source -> why it could not run (unconfigured). Recorded, never silent: a source
    #: quietly missing from a "successful" tick is how a store goes stale unnoticed.
    sources_skipped: Dict[str, str] = field(default_factory=dict)
    ews_reachable: Optional[bool] = None  # None → no EWS/calendar source requested
    messages_total: Optional[int] = None
    messages_added: Optional[int] = None
    by_source: Dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    interval_minutes: Optional[int] = None

    def as_status(self) -> Dict[str, object]:
        """The curated dict persisted to the status file (counts + timestamps only)."""
        return {
            "last_run": self.last_run,
            "next_run": self.next_run,
            "interval_minutes": self.interval_minutes,
            "ok": self.ok,
            "skipped": self.skipped,
            "sources_attempted": self.sources_attempted,
            "sources_ingested": self.sources_ingested,
            "sources_skipped": self.sources_skipped,
            "ews_reachable": self.ews_reachable,
            "messages_total": self.messages_total,
            "messages_added": self.messages_added,
            "by_source": self.by_source,
            "error": self.error,
        }


def source_config_gaps(config: Config, source: str) -> List[str]:
    """Why ``source`` cannot run here — empty list means it is usable.

    Delegates to the config objects that own each source's settings, so this never
    becomes a second, drifting copy of "is it set up?" (see ``EWSConfig.config_gaps``).
    An unknown source is reported as usable: ``canonical_source`` already rejects typos
    loudly, and inventing a gap here would silently swallow a source we simply do not
    know how to validate.
    """
    # include_secrets=True: the daemon is asking "will a tick actually do work?", which
    # a complete YAML config with no exported password would answer wrongly.
    if source in MM_SOURCE_NAMES:
        return config.mm_source.config_gaps(include_secrets=True)
    # ews + calendar both ride the EWS identity
    return config.ews.config_gaps(include_secrets=True)


def _partition_configured(config: Config, requested: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """Split requested sources into (usable, {source: reason-it-was-skipped})."""
    usable: List[str] = []
    skipped: Dict[str, str] = {}
    for source in requested:
        gaps = source_config_gaps(config, source)
        if gaps:
            skipped[source] = "not configured: " + "; ".join(gaps)
        else:
            usable.append(source)
    return usable, skipped


def _unconfigured_message(skipped: Dict[str, str]) -> str:
    """One actionable line for 'no source can run', naming each source's gaps."""
    parts = [f"{source} — {reason}" for source, reason in sorted(skipped.items())]
    return (
        "no configured source to ingest from. " + " | ".join(parts) + ". "
        "Run `actionpulse setup`, or narrow daemon.sources to the source you use."
    )


def _ews_host(config: Config) -> Optional[str]:
    endpoint = (config.ews.endpoint or "").strip()
    return urllib.parse.urlparse(endpoint).hostname if endpoint else None


def _corp_reachable(host: str) -> bool:
    from digest_core.setup_autodetect import _dns_resolves

    return _dns_resolves(host)


@contextmanager
def _single_writer():
    """Non-blocking exclusive lock on ``var/state/daemon.lock``. Yields True when acquired,
    False when another daemon tick holds it (closing the fd releases the flock)."""
    lock_path = paths.state_dir() / "daemon.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


def _store_counts(config: Config) -> Tuple[Optional[int], Dict[str, int]]:
    """(total_messages, by_source) from the store; (None, {}) if it can't be read.
    Best-effort telemetry — never fails a tick."""
    from digest_core.api import InboxAPI

    try:
        with InboxAPI.open(config, create=False) as api:
            s = api.stats()
        return int(s.get("messages", 0)), dict(s.get("by_source", {}))
    except Exception:  # noqa: BLE001 - counts are advisory; a read failure must not fail the tick
        return None, {}


def _run_ingest(config: Config, sources: List[str], from_date: str) -> None:
    """Invoke the no-LLM fetch+persist pipeline for ``sources``.

    Uses a **dedicated** daemon state dir so the sync watermark is independent of the daily
    digest's. Honors ``daemon.embed`` (corp-only) by toggling the store's embed-on-ingest
    env for the duration of the run (the run reads it via ``StoreConfig``)."""
    from digest_core.progress import NullSink
    from digest_core.run import run_digest_dry_run

    prior_embed = os.environ.get("DIGEST_STORE_EMBED_ON_INGEST")
    if config.daemon.embed:
        os.environ["DIGEST_STORE_EMBED_ON_INGEST"] = "1"
    try:
        run_digest_dry_run(
            from_date,
            sources,
            str(paths.out_dir()),
            _DEFAULT_MODEL,
            "calendar_day",
            str(paths.state_dir() / "daemon"),  # daemon's own watermark — never the digest's
            validate_citations=False,
            sink=NullSink(),  # silent: no terminal, no stdout
        )
    finally:
        if config.daemon.embed:
            if prior_embed is None:
                os.environ.pop("DIGEST_STORE_EMBED_ON_INGEST", None)
            else:
                os.environ["DIGEST_STORE_EMBED_ON_INGEST"] = prior_embed


def _finalize(result: TickResult, *, write: bool = True) -> TickResult:
    """Stamp last/next run and persist the status file (unless a locked skip)."""
    now = datetime.now(timezone.utc)
    if result.skipped is None:
        result.last_run = now.isoformat()
    if result.interval_minutes:
        result.next_run = (now + timedelta(minutes=result.interval_minutes)).isoformat()
    if write:
        from digest_core.daemon import status

        status.save(result.as_status())
    return result


def ingest_once(
    config: Optional[Config] = None,
    sources: Optional[List[str]] = None,
    *,
    from_date: str = "today",
) -> TickResult:
    """Run one ingestion tick and persist the daemon status file.

    Mattermost is ingested every tick; Exchange/calendar only when the EWS host resolves
    (off-corp ticks skip it, non-fatal). Raises :class:`DaemonError` if the store is off
    (nothing to persist). Transient store-lock contention with a manual run is reported as
    ``skipped="busy"`` (not a failure); a genuine fault is recorded and re-raised."""
    config = config or Config()
    if not config.store.enabled:
        raise DaemonError(
            "store is disabled — the daemon has nothing to persist. Run `actionpulse store "
            "init`, enable the store (store.enabled: true / DIGEST_STORE_ENABLED=1), then retry."
        )

    requested = [
        c for c in (canonical_source(s) for s in (sources or config.daemon.source_list())) if c
    ]
    interval = config.daemon.interval_minutes

    with _single_writer() as acquired:
        if not acquired:
            logger.info("daemon_tick_skipped_locked")
            return _finalize(
                TickResult(ok=True, skipped="locked", interval_minutes=interval), write=False
            )

        # Drop sources that are not configured at all. This is deliberately checked
        # BEFORE reachability: "you never set this up" is a more basic condition than
        # "the network is not there", and until ACTPULSE-101 it was the only one that
        # hard-crashed — the tick raised out of _build_mm_adapter / EWS identity and
        # launchd logged a ~178-line traceback every interval, forever.
        usable, skipped = _partition_configured(config, requested)
        if skipped:
            logger.info("daemon_tick_skip_unconfigured", skipped=sorted(skipped))

        # MM every tick; corp sources (ews/calendar) only when the EWS host resolves.
        mm = [s for s in usable if s in MM_SOURCE_NAMES]
        corp = [s for s in usable if s not in MM_SOURCE_NAMES]
        ews_reachable: Optional[bool] = None
        effective = list(mm)
        if corp:
            host = _ews_host(config)
            ews_reachable = bool(host) and _corp_reachable(host)
            if ews_reachable:
                effective += corp
            else:
                logger.info("daemon_tick_offcorp_skip_ews", host=host or "", skipped=corp)

        result = TickResult(
            sources_attempted=requested,
            sources_ingested=effective,
            sources_skipped=skipped,
            ews_reachable=ews_reachable,
            interval_minutes=interval,
        )

        # Nothing is configured — the tick can never do work in this state, so say so
        # once, clearly, instead of pretending success. DaemonError (not a raw
        # ValueError) so the CLI renders one line rather than a traceback.
        if requested and not usable:
            result.ok = False
            result.error = _unconfigured_message(skipped)
            _finalize(result)
            raise DaemonError(result.error)

        before, _ = _store_counts(config)
        if effective:
            try:
                _run_ingest(config, effective, from_date)
            except _NETWORK_ERRORS as exc:
                logger.info("daemon_tick_degraded_network", error=type(exc).__name__)
                result.ews_reachable = False
                result.sources_ingested = mm  # conservative: EWS half fetched at best
            except Exception as exc:  # noqa: BLE001
                if _is_lock_contention(exc):
                    logger.info("daemon_tick_skipped_busy")
                    result.skipped = "busy"
                    return _finalize(result, write=False)
                result.ok = False
                result.error = f"{type(exc).__name__}: {exc}"
                _finalize(result)
                raise

        total, by_source = _store_counts(config)
        result.messages_total = total
        result.by_source = by_source
        result.messages_added = (
            total - before if (total is not None and before is not None) else None
        )
        logger.info(
            "daemon_tick_done",
            sources=effective,
            ews_reachable=ews_reachable,
            messages_total=total,
            messages_added=result.messages_added,
        )
        return _finalize(result)


def _is_lock_contention(exc: Exception) -> bool:
    """A SQLite/SQLCipher 'database is locked/busy' error (a manual run held the write lock
    longer than busy_timeout) — transient, so the tick skips instead of failing."""
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg
