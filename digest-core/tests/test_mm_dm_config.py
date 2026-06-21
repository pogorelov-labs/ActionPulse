"""DM-scope config schema + consent-gate validator (P3, design §2.2/§6).

Two layers:
  * model-level — ``MattermostSourceConfig`` construction enforces the consent
    gate (selected/all require ``dm_consent_acknowledged``; off/own_posts_only
    never do);
  * load-path — a YAML config merged through ``Config`` is RE-VALIDATED, so a
    hand-edited ``config.yaml`` cannot smuggle a counterparty-exposing scope past
    the gate (``_merge_model`` setattrs onto the instance, which would otherwise
    skip the ``mode="after"`` validator).
"""

import pytest
from pydantic import ValidationError

from digest_core.config import Config, MattermostSourceConfig

# -- model-level defaults + validator -------------------------------------


def test_dm_defaults_are_hard_off():
    cfg = MattermostSourceConfig()
    assert cfg.dm_scope == "off"
    assert cfg.dm_allowlist == []
    assert cfg.dm_consent_acknowledged is False
    assert cfg.dm_consent_acknowledged_at is None


def test_dm_off_needs_no_consent():
    cfg = MattermostSourceConfig(dm_scope="off")
    assert cfg.dm_scope == "off"


def test_dm_own_posts_only_needs_no_consent():
    # The crux of the design: own-posts-only strips all counterparty text before
    # the LLM, so there is no third party to consent for. It must load freely.
    cfg = MattermostSourceConfig(dm_scope="own_posts_only")
    assert cfg.dm_scope == "own_posts_only"
    assert cfg.dm_consent_acknowledged is False


@pytest.mark.parametrize("scope", ["selected", "all"])
def test_dm_counterparty_scope_without_consent_raises(scope):
    with pytest.raises(ValidationError, match="dm_consent_acknowledged"):
        MattermostSourceConfig(dm_scope=scope)


@pytest.mark.parametrize("scope", ["selected", "all"])
def test_dm_counterparty_scope_with_consent_ok(scope):
    cfg = MattermostSourceConfig(
        dm_scope=scope,
        dm_consent_acknowledged=True,
        dm_consent_acknowledged_at="2026-06-18T00:00:00+00:00",
    )
    assert cfg.dm_scope == scope
    assert cfg.dm_consent_acknowledged is True


def test_dm_selected_empty_allowlist_with_consent_is_not_an_error():
    # Empty allowlist under 'selected' is effective-OFF (graceful), handled at the
    # adapter — NOT a config error (emptying the list is a legitimate transient).
    cfg = MattermostSourceConfig(dm_scope="selected", dm_consent_acknowledged=True)
    assert cfg.dm_allowlist == []


def test_dm_invalid_scope_value_rejected():
    with pytest.raises(ValidationError):
        MattermostSourceConfig(dm_scope="everything")


# -- load-path: the validator must fire when YAML is merged ---------------


def _write_cfg(tmp_path, body: str):
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_config_load_selected_without_consent_raises(tmp_path, monkeypatch):
    # The load-bearing gate: _merge_model setattrs, so without the reconstruct
    # this would silently load. It must raise on Config() construction.
    cfg_path = _write_cfg(
        tmp_path,
        "mm_source:\n  enabled: true\n  dm_scope: selected\n  dm_allowlist: ['@alice']\n",
    )
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(cfg_path))
    with pytest.raises(ValidationError, match="dm_consent_acknowledged"):
        Config()


def test_config_load_selected_with_consent_loads(tmp_path, monkeypatch):
    cfg_path = _write_cfg(
        tmp_path,
        "mm_source:\n"
        "  enabled: true\n"
        "  dm_scope: selected\n"
        "  dm_allowlist: ['@alice', 'bob@corp.com']\n"
        "  dm_consent_acknowledged: true\n"
        "  dm_consent_acknowledged_at: '2026-06-18T00:00:00+00:00'\n",
    )
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(cfg_path))
    cfg = Config()
    assert cfg.mm_source.dm_scope == "selected"
    assert cfg.mm_source.dm_consent_acknowledged is True
    assert cfg.mm_source.dm_allowlist == ["@alice", "bob@corp.com"]


def test_config_load_own_posts_only_without_consent_loads(tmp_path, monkeypatch):
    cfg_path = _write_cfg(
        tmp_path,
        "mm_source:\n  enabled: true\n  dm_scope: own_posts_only\n",
    )
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(cfg_path))
    cfg = Config()
    assert cfg.mm_source.dm_scope == "own_posts_only"
    assert cfg.mm_source.dm_consent_acknowledged is False
