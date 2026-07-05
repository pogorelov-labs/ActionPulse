"""DaemonConfig — defaults, env overrides, and source parsing."""

from __future__ import annotations

from digest_core.config import Config, DaemonConfig


def test_defaults():
    d = DaemonConfig()
    assert d.enabled is False
    assert d.interval_minutes == 30
    assert d.sources == "mm,ews"
    assert d.embed is False
    assert d.source_list() == ["mm", "ews"]


def test_source_list_strips_and_drops_blanks():
    assert DaemonConfig(sources=" mm , , ews ").source_list() == ["mm", "ews"]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DIGEST_DAEMON_ENABLED", "1")
    monkeypatch.setenv("DIGEST_DAEMON_INTERVAL_MINUTES", "15")
    monkeypatch.setenv("DIGEST_DAEMON_SOURCES", "mm")
    monkeypatch.setenv("DIGEST_DAEMON_EMBED", "true")
    d = DaemonConfig()
    assert d.enabled is True
    assert d.interval_minutes == 15
    assert d.source_list() == ["mm"]
    assert d.embed is True


def test_explicit_kwargs_win_over_env(monkeypatch):
    monkeypatch.setenv("DIGEST_DAEMON_INTERVAL_MINUTES", "15")
    assert DaemonConfig(interval_minutes=45).interval_minutes == 45


def test_non_numeric_interval_env_is_ignored(monkeypatch):
    monkeypatch.setenv("DIGEST_DAEMON_INTERVAL_MINUTES", "not-a-number")
    assert DaemonConfig().interval_minutes == 30  # default kept, no crash


def test_config_carries_daemon_defaults():
    cfg = Config()
    assert cfg.daemon.interval_minutes == 30
    assert cfg.daemon.enabled is False
