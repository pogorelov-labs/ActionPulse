"""Retention enforcement (feat/retention-7d) + the D4 delivery privacy guard.

Covers:
  * ``maintenance.prune_artifacts`` deletes >keep_days artifacts, keeps ≤keep_days
    and the just-written run (mtime ~ now);
  * ``.state`` operational files survive a prune;
  * ``enabled=false`` and ``keep_days<1`` are no-ops;
  * a ``config.yaml`` with ``retention.keep_days: 3`` is honored (the
    ``_apply_yaml_config`` branch regression — that pattern is NOT universal);
  * the Mattermost privacy-unconfirmed warning fires iff ``acknowledged_private``
    is false.

All synthetic: tmp dirs + fabricated files only, never real corp data.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
import yaml

from digest_core import maintenance, paths
from digest_core.config import Config, MattermostDeliverConfig, RetentionConfig
from digest_core.deliver.mattermost import MattermostDeliverer
from digest_core.llm.schemas import Digest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_out(tmp_path, monkeypatch):
    """Point the data home at tmp and seed var/out + var/state synthetic files."""
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    out = tmp_path / "var" / "out"
    state = tmp_path / "var" / "state"
    out.mkdir(parents=True)
    state.mkdir(parents=True)
    return out, state


def _age(path, days):
    """Backdate a file's mtime by ``days`` (atime kept the same is fine)."""
    when = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (when, when))


def _cfg(enabled=True, keep_days=7):
    """A Config whose retention sub-config is set without touching disk YAML."""
    cfg = Config()
    cfg.retention = RetentionConfig(enabled=enabled, keep_days=keep_days)
    return cfg


# ---------------------------------------------------------------------------
# prune_artifacts: the deletion logic
# ---------------------------------------------------------------------------


def test_prune_deletes_old_keeps_recent_and_current_run(tmp_path, monkeypatch):
    out, _ = _seed_out(tmp_path, monkeypatch)

    # Old (> 7d) artifacts of every pruned kind.
    old_json = out / "digest-2026-01-01.json"
    old_md = out / "digest-2026-01-01.md"
    old_trace = out / "trace-old.meta.json"
    for p in (old_json, old_md, old_trace):
        p.write_text("{}")
        _age(p, 30)

    # A within-window artifact (5d) and the "current run" pair (mtime ~ now).
    recent_json = out / "digest-2026-02-01.json"
    recent_json.write_text("{}")
    _age(recent_json, 5)
    current_json = out / "digest-2026-06-17.json"
    current_md = out / "digest-2026-06-17.md"
    current_trace = out / "trace-current.meta.json"
    for p in (current_json, current_md, current_trace):
        p.write_text("{}")  # mtime ~ now

    counts = maintenance.prune_artifacts(_cfg(keep_days=7))

    assert counts["total"] == 3
    assert counts["digest_json"] == 1
    assert counts["digest_md"] == 1
    assert counts["trace_meta"] == 1
    assert counts["keep_days"] == 7

    assert not old_json.exists() and not old_md.exists() and not old_trace.exists()
    # Within-window + current-run files survive.
    assert recent_json.exists()
    assert current_json.exists() and current_md.exists() and current_trace.exists()


def test_prune_leaves_state_files_untouched(tmp_path, monkeypatch):
    out, state = _seed_out(tmp_path, monkeypatch)
    # Operational state — NOT PDn, must survive even when backdated far past window.
    syncstate = state / "ews.syncstate"
    last_run = state / "last_run.json"
    ledger = state / "delivered-items.jsonl"
    for p in (syncstate, last_run, ledger):
        p.write_text("x")
        _age(p, 365)

    old = out / "digest-2025-01-01.json"
    old.write_text("{}")
    _age(old, 365)

    counts = maintenance.prune_artifacts(_cfg(keep_days=7))

    assert counts["total"] == 1  # only the out/ artifact
    assert syncstate.exists() and last_run.exists() and ledger.exists()


def test_prune_disabled_check_is_callers_job_but_runs_when_enabled(tmp_path, monkeypatch):
    # prune_artifacts itself always prunes (the enabled gate lives in the run
    # wiring); this asserts it does prune when invoked with a normal window.
    out, _ = _seed_out(tmp_path, monkeypatch)
    old = out / "digest-2025-01-01.json"
    old.write_text("{}")
    _age(old, 100)
    counts = maintenance.prune_artifacts(_cfg(enabled=True, keep_days=7))
    assert counts["total"] == 1


def test_prune_keep_days_below_one_is_noop(tmp_path, monkeypatch):
    out, _ = _seed_out(tmp_path, monkeypatch)
    for days in (0, -5):
        old = out / f"digest-2025-01-0{abs(days)+1}.json"
        old.write_text("{}")
        _age(old, 365)
        counts = maintenance.prune_artifacts(_cfg(keep_days=days))
        assert counts["total"] == 0
        assert counts["keep_days"] == days
        assert old.exists()  # safety rail: nothing deleted


def test_prune_only_touches_the_three_globs(tmp_path, monkeypatch):
    out, _ = _seed_out(tmp_path, monkeypatch)
    # An unrelated old file in out/ must NOT be deleted (glob safety rail).
    stray = out / "notes-2025-01-01.txt"
    stray.write_text("keep me")
    _age(stray, 365)
    # And a nested dir is never recursed into.
    nested = out / "sub"
    nested.mkdir()
    nested_old = nested / "digest-2025-01-01.json"
    nested_old.write_text("{}")
    _age(nested_old, 365)

    counts = maintenance.prune_artifacts(_cfg(keep_days=7))

    assert counts["total"] == 0
    assert stray.exists()
    assert nested_old.exists()  # no recursion outside the top-level out/


def test_prune_missing_out_dir_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))  # no var/out created
    assert not paths.out_dir(create=False).exists()
    counts = maintenance.prune_artifacts(_cfg(keep_days=7))
    assert counts["total"] == 0


# ---------------------------------------------------------------------------
# clean_digests default now sourced from config
# ---------------------------------------------------------------------------


def test_clean_digests_bare_default_reads_config_keep_days(tmp_path, monkeypatch):
    """The bare ``clean_digests()`` sources its window from config, not a 14 hardcode.

    ``clean_digests`` dates digest-* artifacts by the YYYY-MM-DD in their NAME.
    With a config window of 3 days, a name from ~10 days ago is pruned while
    today's survives — proving the default is config-sourced (a 14-day hardcode
    would still prune the 10-day-old file, so we add a 5-day-old name that a
    3-day window prunes but a 14-day window would keep).
    """
    out, _ = _seed_out(tmp_path, monkeypatch)
    custom = tmp_path / "custom_config.yaml"
    custom.write_text("retention:\n  keep_days: 3\n", encoding="utf-8")
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(custom))
    monkeypatch.delenv("DIGEST_RETENTION_KEEP_DAYS", raising=False)

    today = datetime.now(timezone.utc)
    five_days = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    now_name = today.strftime("%Y-%m-%d")
    # 5-day-old by name: pruned under keep_days=3, kept under the old 14.
    aged = out / f"digest-{five_days}.json"
    aged.write_text("{}")
    keep = out / f"digest-{now_name}.json"
    keep.write_text("{}")

    removed, _freed = maintenance.clean_digests()  # bare -> config keep_days (3)

    assert removed == 1
    assert not aged.exists()
    assert keep.exists()


# ---------------------------------------------------------------------------
# Config wiring: defaults, ENV, and the YAML-branch regression
# ---------------------------------------------------------------------------


def test_retention_defaults():
    rc = RetentionConfig()
    assert rc.enabled is True
    assert rc.keep_days == 7


def test_dedup_ttl_default_aligned_to_seven():
    assert Config().memory.dedup_ttl_days == 7


def test_retention_env_overrides(monkeypatch):
    monkeypatch.setenv("DIGEST_RETENTION_ENABLED", "false")
    monkeypatch.setenv("DIGEST_RETENTION_KEEP_DAYS", "21")
    rc = RetentionConfig()
    assert rc.enabled is False
    assert rc.keep_days == 21


def test_yaml_retention_keep_days_is_honored(tmp_path, monkeypatch):
    """Regression: the `retention:` YAML branch in _apply_yaml_config exists.

    `threading` ships WITHOUT such a branch, so we cannot assume the pattern is
    universal — assert retention's branch is wired by loading a real YAML.
    """
    # Ensure no ENV override masks the YAML value.
    monkeypatch.delenv("DIGEST_RETENTION_KEEP_DAYS", raising=False)
    monkeypatch.delenv("DIGEST_RETENTION_ENABLED", raising=False)

    custom = tmp_path / "custom_config.yaml"
    custom.write_text("retention:\n  keep_days: 3\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(custom))

    cfg = Config()
    assert cfg.retention.keep_days == 3
    assert cfg.retention.enabled is False


def test_yaml_retention_env_still_wins(tmp_path, monkeypatch):
    custom = tmp_path / "custom_config.yaml"
    custom.write_text("retention:\n  keep_days: 3\n", encoding="utf-8")
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(custom))
    monkeypatch.setenv("DIGEST_RETENTION_KEEP_DAYS", "9")
    cfg = Config()
    assert cfg.retention.keep_days == 9  # ENV beats YAML


# ---------------------------------------------------------------------------
# D4 delivery privacy guard
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal httpx.Client stand-in: records POSTs, returns 200."""

    posts: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json):
        type(self).posts.append({"url": url, "json": json})
        return httpx.Response(200, request=httpx.Request("POST", url))


def _digest():
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-06-17",
        trace_id="trace-priv",
        sections=[{"title": "Мои действия", "items": []}],
    )


EVENT = "mattermost_target_privacy_unconfirmed"


def _deliver_capturing(config, monkeypatch, caplog):
    """Deliver once and return the captured stdlib log records.

    The project routes structlog through stdlib (JSONRenderer over
    stdlib.LoggerFactory), so once `_configure_structlog` has run in the
    session `structlog.testing.capture_logs` is unreliable — `caplog` reads the
    real handler chain and works regardless of global config.
    """
    monkeypatch.setenv("MM_WEBHOOK_URL", "https://mm.example/hooks/opaque")
    monkeypatch.setattr("digest_core.deliver.mattermost.httpx.Client", _FakeClient)
    _FakeClient.posts = []
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="digest_core.deliver.mattermost"):
        MattermostDeliverer(config).deliver_digest(_digest())
    return caplog.records


def test_privacy_warning_fires_when_unconfirmed(monkeypatch, caplog):
    records = _deliver_capturing(
        MattermostDeliverConfig(enabled=True, acknowledged_private=False), monkeypatch, caplog
    )
    assert any(EVENT in r.getMessage() for r in records)
    # Delivery still happened (guard + warn, never block).
    assert _FakeClient.posts, "delivery must proceed despite the warning"


def test_privacy_warning_silent_when_acknowledged(monkeypatch, caplog):
    records = _deliver_capturing(
        MattermostDeliverConfig(enabled=True, acknowledged_private=True), monkeypatch, caplog
    )
    assert not any(EVENT in r.getMessage() for r in records)
    assert _FakeClient.posts


def test_privacy_warning_carries_no_payload(monkeypatch, caplog):
    records = _deliver_capturing(
        MattermostDeliverConfig(enabled=True, acknowledged_private=False), monkeypatch, caplog
    )
    warning = next(r for r in records if EVENT in r.getMessage())
    # No webhook URL, no message body in the structured event.
    message = warning.getMessage()
    assert "hooks/opaque" not in message
    assert "https://mm.example" not in message


def test_wizard_persists_acknowledged_private(tmp_path, monkeypatch):
    """The setup wizard writes deliver.mattermost.acknowledged_private to config."""
    from digest_core.setup_wizard import _derive_from_email, _write_config_yaml

    example = tmp_path / "config.example.yaml"
    yaml.safe_dump({"ews": {}, "llm": {}}, example.open("w"))
    user_config = tmp_path / "config.yaml"
    monkeypatch.setattr("digest_core.setup_wizard.CONFIG_EXAMPLE", example)
    monkeypatch.setattr("digest_core.setup_wizard.CONFIG_USER", user_config)

    _write_config_yaml(
        user_upn="u@corp.ru",
        ews_endpoint="https://ews",
        llm_endpoint="https://llm",
        derived=_derive_from_email("u@corp.ru"),
        verify_ca=None,
        acknowledged_private=True,
    )
    written = yaml.safe_load(user_config.read_text())
    assert written["deliver"]["mattermost"]["acknowledged_private"] is True


# ---------------------------------------------------------------------------
# Run wiring: _enforce_retention decision logic
# ---------------------------------------------------------------------------


def _ctx(config, *, replay_ingest=None, dump_ingest=None):
    """A bare RunContext carrying only the fields _enforce_retention reads."""
    from types import SimpleNamespace

    return SimpleNamespace(
        config=config,
        replay_ingest=replay_ingest,
        dump_ingest=dump_ingest,
        trace_id="trace-wire",
    )


def test_enforce_retention_prunes_on_real_run(tmp_path, monkeypatch):
    from digest_core import run as runner

    out, _ = _seed_out(tmp_path, monkeypatch)
    old = out / "digest-2025-01-01.json"
    old.write_text("{}")
    _age(old, 365)

    block = runner._enforce_retention(_ctx(_cfg(enabled=True, keep_days=7)))

    assert block["enabled"] is True
    assert block["keep_days"] == 7
    assert block["pruned_counts"]["total"] == 1
    assert not old.exists()


def test_enforce_retention_disabled_is_noop(tmp_path, monkeypatch):
    from digest_core import run as runner

    out, _ = _seed_out(tmp_path, monkeypatch)
    old = out / "digest-2025-01-01.json"
    old.write_text("{}")
    _age(old, 365)

    block = runner._enforce_retention(_ctx(_cfg(enabled=False, keep_days=7)))

    assert block["enabled"] is False
    assert block["pruned_counts"] is None
    assert old.exists()  # nothing pruned


def test_enforce_retention_skipped_for_replay_and_dump(tmp_path, monkeypatch):
    from digest_core import run as runner

    out, _ = _seed_out(tmp_path, monkeypatch)
    old = out / "digest-2025-01-01.json"
    old.write_text("{}")
    _age(old, 365)

    for kwargs in ({"replay_ingest": "snap.json"}, {"dump_ingest": "snap.json"}):
        block = runner._enforce_retention(_ctx(_cfg(enabled=True, keep_days=7), **kwargs))
        assert block["pruned_counts"] is None
    assert old.exists()  # dev replay/dump never deletes real artifacts


def test_enforce_retention_swallows_failures(tmp_path, monkeypatch):
    from digest_core import run as runner

    _seed_out(tmp_path, monkeypatch)

    def boom(_config):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("digest_core.maintenance.prune_artifacts", boom)
    block = runner._enforce_retention(_ctx(_cfg(enabled=True, keep_days=7)))
    # The run continues; failure is recorded, not raised.
    assert block["pruned_counts"] is None
    assert block["error"] == "RuntimeError"
