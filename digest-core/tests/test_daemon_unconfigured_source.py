"""A source that was never set up must be SKIPPED, not crash the tick (ACTPULSE-101).

Found by installing the daemon on a real machine: the default `daemon.sources` is
"mm,ews", MM was not configured, and every 30-minute tick exited 1 with a ~178-line
Rich traceback — forever, with no backoff and no rotation.

The posture was inverted. The daemon already skips EWS gracefully (exit 0) when the
corp network is *unreachable*; a source that is merely *not configured yet* is the
more benign condition, yet it was the only one that hard-crashed.

These tests build their own Config and set the fields they assert on, rather than
inheriting from `configs/config.yaml` — that file is gitignored, so CI never has one
and a real machine always does (the #233 lesson).
"""

from __future__ import annotations

import pytest

from digest_core.config import Config
from digest_core.daemon import status, tick


@pytest.fixture(autouse=True)
def _store_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    monkeypatch.setenv("DIGEST_STORE_ENABLED", "1")
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    # No source credentials by default — that is the state under test.
    for var in ("EWS_PASSWORD", "MM_BASE_URL", "MM_PAT"):
        monkeypatch.delenv(var, raising=False)
    yield


def _config(monkeypatch, *, ews: bool = False, mm: bool = False) -> Config:
    """A Config with exactly the sources we say are configured, and nothing implicit."""
    cfg = Config()
    cfg.ews.endpoint = "https://ews.example.invalid/EWS/Exchange.asmx"
    cfg.ews.user_upn = "me@example.invalid"
    if ews:
        monkeypatch.setenv(cfg.ews.password_env, "pw")
    if mm:
        monkeypatch.setenv(cfg.mm_source.base_url_env, "https://mm.example.invalid")
        monkeypatch.setenv(cfg.mm_source.token_env, "tok")
    return cfg


def test_unconfigured_source_is_skipped_not_raised(monkeypatch):
    """MM unconfigured + EWS configured → the tick runs EWS and records the skip."""
    cfg = _config(monkeypatch, ews=True, mm=False)
    monkeypatch.setattr(tick, "_store_counts", lambda c: (0, {}))
    monkeypatch.setattr(tick, "_corp_reachable", lambda host: True)
    ingested = {}
    monkeypatch.setattr(
        tick, "_run_ingest", lambda c, srcs, fd: ingested.setdefault("sources", srcs)
    )

    result = tick.ingest_once(cfg, sources=["mm", "ews"])

    assert result.ok is True
    assert ingested["sources"] == ["ews"], "the unconfigured source must not reach ingest"
    assert "mm" in result.sources_skipped
    assert "MM_PAT" in result.sources_skipped["mm"] or "base_url" in result.sources_skipped["mm"]
    # No silent caps: what was dropped is still reported as attempted.
    assert result.sources_attempted == ["mm", "ews"]


def test_every_source_unconfigured_fails_once_with_an_actionable_message(monkeypatch):
    """Nothing can run → DaemonError (one line), not a ValueError traceback.

    DaemonError specifically: the CLI catches it and prints a single line, which is the
    whole point when stderr is a log file that a LaunchAgent appends to every 30 min.
    """
    cfg = _config(monkeypatch, ews=False, mm=False)
    monkeypatch.setattr(tick, "_store_counts", lambda c: (0, {}))

    def _must_not_run(*a, **k):
        raise AssertionError("ingest must not be attempted when no source is configured")

    monkeypatch.setattr(tick, "_run_ingest", _must_not_run)

    with pytest.raises(tick.DaemonError) as excinfo:
        tick.ingest_once(cfg, sources=["mm", "ews"])

    message = str(excinfo.value)
    assert "no configured source" in message
    # It must name what to fix, per source — not just "misconfigured".
    assert "mm" in message and "ews" in message
    assert "EWS_PASSWORD" in message
    assert len(message.splitlines()) == 1, "a background process logs one line, not a traceback"


def test_the_skip_is_recorded_in_the_status_file(monkeypatch):
    """`daemon status` and the MCP health tool read this file — a skip must reach them."""
    cfg = _config(monkeypatch, ews=True, mm=False)
    monkeypatch.setattr(tick, "_store_counts", lambda c: (0, {}))
    monkeypatch.setattr(tick, "_corp_reachable", lambda host: True)
    monkeypatch.setattr(tick, "_run_ingest", lambda c, srcs, fd: None)

    tick.ingest_once(cfg, sources=["mm", "ews"])

    saved = status.load()
    assert "mm" in (saved.get("sources_skipped") or {})


def test_failure_state_is_persisted_before_raising(monkeypatch):
    """The unrunnable state must be visible to `daemon status`, not only to whoever
    read the exit code of a background process nobody watches."""
    cfg = _config(monkeypatch, ews=False, mm=False)
    monkeypatch.setattr(tick, "_store_counts", lambda c: (0, {}))

    with pytest.raises(tick.DaemonError):
        tick.ingest_once(cfg, sources=["ews"])

    saved = status.load()
    assert saved.get("ok") is False
    assert "no configured source" in (saved.get("error") or "")


def test_unreachable_is_still_distinct_from_unconfigured(monkeypatch):
    """The pre-existing off-corp behaviour must survive: EWS *configured* but
    unreachable still skips quietly and succeeds (exit 0), and is NOT reported as
    a configuration gap — the two states have different fixes."""
    cfg = _config(monkeypatch, ews=True, mm=False)
    monkeypatch.setattr(tick, "_store_counts", lambda c: (0, {}))
    monkeypatch.setattr(tick, "_corp_reachable", lambda host: False)
    monkeypatch.setattr(tick, "_run_ingest", lambda c, srcs, fd: None)

    result = tick.ingest_once(cfg, sources=["ews"])

    assert result.ok is True
    assert result.ews_reachable is False
    assert "ews" not in result.sources_skipped, "unreachable is not a config gap"


class TestInstallRefusesAnUnrunnableUnit:
    """Scheduling a unit that can only fail is how you train someone to ignore a log.

    This repo has already paid for that lesson once — nine consecutive red nightly CI
    runs that nobody read. The cheapest fix is to not create the noise.
    """

    def test_install_refuses_when_no_source_is_configured(self, monkeypatch, tmp_path):
        import sys

        from typer.testing import CliRunner

        from digest_core.cli import app

        if sys.platform != "darwin":
            pytest.skip("`daemon install` is macOS-only; the guard runs after that check")

        monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
        monkeypatch.setenv("DIGEST_STORE_ENABLED", "1")
        monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
        for var in ("EWS_PASSWORD", "MM_BASE_URL", "MM_PAT"):
            monkeypatch.delenv(var, raising=False)
        # An env file on the developer's machine would re-supply the secrets the CLI
        # callback loads, so point that loader at nothing (the #233 late-binding fix
        # is what makes this redirection actually work).
        monkeypatch.setattr("digest_core.ui.menu.ENV_PATH", tmp_path / "no-env")

        def _must_not_write(*a, **k):
            raise AssertionError("the LaunchAgent must not be written when nothing can run")

        monkeypatch.setattr("digest_core.daemon.launchd.install", _must_not_write)

        result = CliRunner().invoke(app, ["daemon", "install", "--yes"])

        assert result.exit_code == 1
        assert "no configured source" in result.output


class TestSharedConfigPolicy:
    """`config_gaps` is the single source of truth, deliberately.

    The daemon needs the same answer as `EWSIngest._check_configured`; two copies of
    "is this configured?" is how they drift, and a drifted daemon either crashes on a
    usable source or silently skips a fixable one.
    """

    def test_ews_gaps_name_every_missing_setting_at_once(self, monkeypatch):
        cfg = Config()
        cfg.ews.endpoint = ""
        cfg.ews.user_upn = ""
        cfg.ews.user_login = ""
        cfg.ews.user_domain = ""
        monkeypatch.delenv(cfg.ews.password_env, raising=False)
        gaps = cfg.ews.config_gaps()
        assert len(gaps) == 2, gaps  # endpoint + identity, in ONE report
        assert any("endpoint" in g for g in gaps)
        assert any("user_upn" in g for g in gaps)

    def test_secrets_are_a_separate_question_from_settings(self, monkeypatch):
        """`include_secrets` is the difference between the two callers, and it matters.

        A machine with complete YAML but no exported password is *correctly configured*
        — `_check_configured` must still pass, because the password has its own check
        with its own message at point of use. The daemon asks the broader question
        ("will a tick actually do work?"), so only it opts in.
        """
        cfg = Config()
        cfg.ews.endpoint = "https://ews.example.invalid/EWS/Exchange.asmx"
        cfg.ews.user_upn = "me@example.invalid"
        monkeypatch.delenv(cfg.ews.password_env, raising=False)

        assert cfg.ews.config_gaps() == [], "settings are complete"
        with_secret = cfg.ews.config_gaps(include_secrets=True)
        assert len(with_secret) == 1 and cfg.ews.password_env in with_secret[0]
        # ...and the daemon sees the broader answer, so it skips rather than crashes.
        assert tick.source_config_gaps(cfg, "ews") == with_secret

    def test_ews_ingest_uses_the_same_policy(self, monkeypatch):
        """Proves the shared path is wired, not just present.

        Built with ``__new__`` rather than ``EWSIngest(...)`` on purpose: the real
        constructor sets up a TLS context, and on a machine without
        ``configs/config.yaml`` (i.e. CI) ``verify_ca`` defaults to
        ``/etc/ssl/corp-ca.pem``, which does not exist — so constructing one raises
        FileNotFoundError before reaching the method under test. This test is about
        one thing: that ``_check_configured`` delegates to ``config_gaps``.
        """
        from digest_core.ingest.ews import EWSIngest

        cfg = Config()
        cfg.ews.endpoint = ""
        cfg.ews.user_upn = ""
        cfg.ews.user_login = ""
        cfg.ews.user_domain = ""
        monkeypatch.delenv(cfg.ews.password_env, raising=False)

        ingest = EWSIngest.__new__(EWSIngest)
        ingest.config = cfg.ews
        with pytest.raises(ValueError, match="EWS is not configured"):
            ingest._check_configured()

        # ...and it is genuinely delegating, not carrying its own copy.
        calls = []
        monkeypatch.setattr(
            type(cfg.ews), "config_gaps", lambda self, **kw: calls.append(kw) or []
        )
        ingest._check_configured()  # no gaps reported -> must now pass
        assert calls, "_check_configured must go through config_gaps"

    def test_configured_source_reports_no_gaps(self, monkeypatch):
        cfg = _config(monkeypatch, ews=True, mm=True)
        assert cfg.ews.config_gaps() == []
        assert cfg.mm_source.config_gaps() == []
        assert tick.source_config_gaps(cfg, "ews") == []
        assert tick.source_config_gaps(cfg, "mm") == []
