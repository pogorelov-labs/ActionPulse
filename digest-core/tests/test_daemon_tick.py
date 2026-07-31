"""daemon tick: store gate, corp-aware source split, single-writer flock, status write.

The store/network are faked so these run offline and never open SQLCipher or hit EWS.
"""

from __future__ import annotations

import fcntl
import os

import pytest

from digest_core.config import Config
from digest_core.daemon import status, tick


@pytest.fixture(autouse=True)
def _store_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))  # status + lock live in tmp
    monkeypatch.setenv("DIGEST_STORE_ENABLED", "1")
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    # Both sources fully configured. This file tests ROUTING (MM every tick, EWS gated
    # by reachability) with `_run_ingest` mocked, so it needs sources that pass the
    # is-it-configured gate added for ACTPULSE-101 — otherwise every case here would
    # exercise the skip path instead of the behaviour it names.
    #
    # Set explicitly rather than inherited: `configs/config.yaml` is gitignored, so a
    # real machine supplies ews.endpoint and CI does not. Leaving it ambient would make
    # these tests mean different things in the two places (the #233 lesson).
    monkeypatch.setenv("EWS_ENDPOINT", "https://ews.corp/EWS/Exchange.asmx")
    monkeypatch.setenv("EWS_USER_UPN", "me@corp")
    monkeypatch.setenv("EWS_PASSWORD", "pw")
    monkeypatch.setenv("MM_BASE_URL", "https://mm.corp")
    monkeypatch.setenv("MM_PAT", "tok")
    yield


def _counts(monkeypatch, *pairs):
    """Feed successive (total, by_source) returns to tick._store_counts (before, after…)."""
    it = iter(pairs)
    monkeypatch.setattr(tick, "_store_counts", lambda cfg: next(it))


def test_store_disabled_raises(monkeypatch):
    """A tick with the store off must refuse, not half-run.

    Sets ``enabled = False`` on the config it passes rather than deleting the env
    override and trusting the default: `configs/config.yaml` is gitignored, so CI
    never has one and a real machine always does. Once a developer follows the
    documented setup (`store.enabled: true`), an ambient-default premise silently
    inverts and this test starts exercising the *enabled* path instead.
    """
    monkeypatch.delenv("DIGEST_STORE_ENABLED", raising=False)
    config = Config()
    config.store.enabled = False
    assert not config.store.enabled, "the premise this test asserts on"
    with pytest.raises(tick.DaemonError):
        tick.ingest_once(config)


def test_mm_ingests_and_writes_status(monkeypatch):
    _counts(monkeypatch, (0, {}), (5, {"mm": 5}))
    called = {}
    monkeypatch.setattr(tick, "_run_ingest", lambda cfg, srcs, fd: called.setdefault("srcs", srcs))

    r = tick.ingest_once(Config(), sources=["mm"])

    assert r.ok and r.skipped is None
    assert called["srcs"] == ["mm"]
    assert r.sources_ingested == ["mm"]
    assert r.ews_reachable is None  # no corp source requested
    assert r.messages_added == 5 and r.messages_total == 5
    saved = status.load()
    assert saved and saved["last_run"] and saved["messages_added"] == 5


def test_offcorp_skips_ews_quietly(monkeypatch):
    monkeypatch.setenv("EWS_ENDPOINT", "https://ews.corp/EWS/Exchange.asmx")
    monkeypatch.setattr(tick, "_corp_reachable", lambda host: False)
    _counts(monkeypatch, (3, {"mm": 3}), (3, {"mm": 3}))
    ran = {"called": False}
    monkeypatch.setattr(tick, "_run_ingest", lambda *a: ran.update(called=True))

    r = tick.ingest_once(Config(), sources=["ews"])

    assert r.ok and r.skipped is None
    assert r.ews_reachable is False
    assert r.sources_ingested == []  # EWS-only, off-corp → nothing to fetch
    assert ran["called"] is False  # never invoked the pipeline
    assert r.messages_added == 0


def test_oncorp_includes_ews(monkeypatch):
    monkeypatch.setenv("EWS_ENDPOINT", "https://ews.corp/EWS/Exchange.asmx")
    monkeypatch.setattr(tick, "_corp_reachable", lambda host: True)
    _counts(monkeypatch, (0, {}), (7, {"mm": 4, "email": 3}))
    seen = {}
    monkeypatch.setattr(tick, "_run_ingest", lambda cfg, srcs, fd: seen.setdefault("srcs", srcs))

    r = tick.ingest_once(Config(), sources=["mm", "ews"])

    assert r.ews_reachable is True
    assert set(seen["srcs"]) == {"mm", "ews"}
    assert r.messages_added == 7


def test_single_writer_lock_skips(monkeypatch):
    from digest_core import paths

    monkeypatch.setattr(tick, "_run_ingest", lambda *a: pytest.fail("should not run while locked"))
    lock = paths.state_dir() / "daemon.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # hold the lock like a concurrent tick
    try:
        r = tick.ingest_once(Config(), sources=["mm"])
        assert r.skipped == "locked" and r.ok
    finally:
        os.close(fd)


def test_db_lock_contention_is_transient_busy(monkeypatch):
    _counts(monkeypatch, (0, {}))

    def _boom(*a):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(tick, "_run_ingest", _boom)
    r = tick.ingest_once(Config(), sources=["mm"])
    assert r.skipped == "busy" and r.ok  # transient contention, not a failure


def test_network_error_degrades_not_raises(monkeypatch):
    monkeypatch.setenv("EWS_ENDPOINT", "https://ews.corp/EWS/Exchange.asmx")
    monkeypatch.setattr(tick, "_corp_reachable", lambda host: True)
    _counts(monkeypatch, (0, {}), (0, {}))

    def _neterr(*a):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(tick, "_run_ingest", _neterr)
    r = tick.ingest_once(Config(), sources=["ews"])
    assert r.ok and r.ews_reachable is False  # degraded, exit-0-worthy


def test_real_error_records_and_reraises(monkeypatch):
    _counts(monkeypatch, (0, {}))

    def _bug(*a):
        raise ValueError("Mattermost source selected but no base URL is set")

    monkeypatch.setattr(tick, "_run_ingest", _bug)
    with pytest.raises(ValueError):
        tick.ingest_once(Config(), sources=["mm"])
    saved = status.load()
    assert saved and saved["ok"] is False and "ValueError" in (saved["error"] or "")
