"""
Test idempotency helpers for the T-48h rebuild window.
"""

import os
from datetime import datetime, timezone

import pytest

from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.run import (
    PIPELINE_VERSION,
    _artifact_age_hours,
    _config_sha256,
    _content_sha256,
    _idem_content_skip,
    _idem_pre_ingest_skip,
    _idem_sidecar_path,
    _read_idem_sidecar,
    _should_skip_existing_artifacts,
    _write_idem_sidecar,
)


@pytest.fixture
def temp_output_dir(tmp_path):
    """Temporary output directory for testing."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def test_idempotency_within_48h(temp_output_dir):
    """Recent JSON+MD artifacts should trigger a skip."""
    json_path = temp_output_dir / "digest-2024-01-15.json"
    md_path = temp_output_dir / "digest-2024-01-15.md"

    json_path.touch()
    md_path.touch()

    assert _should_skip_existing_artifacts(json_path, md_path) is True


def test_idempotency_outside_48h(temp_output_dir):
    """Old artifacts should not block a rebuild."""
    json_path = temp_output_dir / "digest-2024-01-15.json"
    md_path = temp_output_dir / "digest-2024-01-15.md"

    json_path.touch()
    md_path.touch()

    old_time = datetime.now(timezone.utc).timestamp() - (50 * 3600)
    os.utime(json_path, (old_time, old_time))
    os.utime(md_path, (old_time, old_time))

    assert _artifact_age_hours(json_path) >= 49
    assert _should_skip_existing_artifacts(json_path, md_path) is False


def test_idempotency_missing_artifacts(temp_output_dir):
    """Missing artifacts should not trigger a skip."""
    json_path = temp_output_dir / "digest-2024-01-15.json"
    md_path = temp_output_dir / "digest-2024-01-15.md"

    assert _should_skip_existing_artifacts(json_path, md_path) is False


def test_idempotency_partial_artifacts(temp_output_dir):
    """Partial artifacts should not trigger a skip."""
    json_path = temp_output_dir / "digest-2024-01-15.json"
    md_path = temp_output_dir / "digest-2024-01-15.md"

    json_path.touch()

    assert _should_skip_existing_artifacts(json_path, md_path) is False


# ---------------------------------------------------------------------------
# Config + content aware idempotency sidecar (PR1)
# ---------------------------------------------------------------------------


def _make_artifacts(out_dir, date="2024-01-15"):
    json_path = out_dir / f"digest-{date}.json"
    md_path = out_dir / f"digest-{date}.md"
    json_path.touch()
    md_path.touch()
    return json_path, md_path


def _age_artifacts(*paths, hours):
    when = datetime.now(timezone.utc).timestamp() - hours * 3600
    for path in paths:
        os.utime(path, (when, when))


def _nm(msg_id, body, subject="Subj"):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="conv",
        subject=subject,
        text_body=body,
        sender_email="a@corp.com",
        datetime_received=datetime(2024, 1, 15, tzinfo=timezone.utc),
        to_recipients=["user@corp.com"],
        cc_recipients=[],
    )


def test_idem_sidecar_roundtrip(temp_output_dir):
    json_path = temp_output_dir / "digest-2024-01-15.json"

    _write_idem_sidecar(json_path, config_sha="cfg", content_sha="cnt")

    assert _idem_sidecar_path(json_path).name == "digest-2024-01-15.idem.json"
    assert _read_idem_sidecar(json_path) == {
        "config_sha256": "cfg",
        "content_sha256": "cnt",
        "pipeline_version": PIPELINE_VERSION,
    }


def test_idem_sidecar_missing_returns_none(temp_output_dir):
    assert _read_idem_sidecar(temp_output_dir / "digest-2024-01-15.json") is None


def test_config_sha256_stable_and_sensitive():
    baseline = _config_sha256(Config())

    assert baseline == _config_sha256(Config())
    assert len(baseline) == 64

    changed = Config()
    changed.llm.model = "some-other-model"
    assert _config_sha256(changed) != baseline


def test_content_sha256_is_order_independent_and_sensitive():
    a = _nm("m-1", "alpha body")
    b = _nm("m-2", "beta body")

    assert _content_sha256([a, b]) == _content_sha256([b, a])
    assert _content_sha256([a]) != _content_sha256([b])
    assert _content_sha256([_nm("m-1", "alpha body")]) == _content_sha256([a])


def test_pre_ingest_skip_requires_fresh_config_and_version(temp_output_dir):
    json_path, md_path = _make_artifacts(temp_output_dir)
    _write_idem_sidecar(json_path, config_sha="cfg", content_sha="cnt")
    sidecar = _read_idem_sidecar(json_path)

    assert _idem_pre_ingest_skip(json_path, md_path, sidecar, "cfg") is True
    assert _idem_pre_ingest_skip(json_path, md_path, sidecar, "OTHER") is False  # config change
    assert _idem_pre_ingest_skip(json_path, md_path, None, "cfg") is False  # no sidecar


def test_pre_ingest_skip_false_when_stale(temp_output_dir):
    json_path, md_path = _make_artifacts(temp_output_dir)
    _write_idem_sidecar(json_path, config_sha="cfg", content_sha="cnt")
    _age_artifacts(json_path, md_path, hours=50)
    sidecar = _read_idem_sidecar(json_path)

    assert _idem_pre_ingest_skip(json_path, md_path, sidecar, "cfg") is False


def test_pre_ingest_skip_false_on_version_change(temp_output_dir):
    json_path, md_path = _make_artifacts(temp_output_dir)
    _write_idem_sidecar(json_path, config_sha="cfg", content_sha="cnt")
    sidecar = _read_idem_sidecar(json_path)
    sidecar["pipeline_version"] = "0.0.0-stale"

    assert _idem_pre_ingest_skip(json_path, md_path, sidecar, "cfg") is False


def test_content_skip_requires_config_content_and_version(temp_output_dir):
    json_path, md_path = _make_artifacts(temp_output_dir)
    _write_idem_sidecar(json_path, config_sha="cfg", content_sha="cnt")
    sidecar = _read_idem_sidecar(json_path)

    assert _idem_content_skip(json_path, md_path, sidecar, "cfg", "cnt") is True
    assert _idem_content_skip(json_path, md_path, sidecar, "cfg", "CHANGED") is False
    assert _idem_content_skip(json_path, md_path, sidecar, "OTHER", "cnt") is False
    assert _idem_content_skip(json_path, md_path, None, "cfg", "cnt") is False


def test_content_skip_is_independent_of_mtime(temp_output_dir):
    """Stale-but-identical content still skips the expensive rebuild."""
    json_path, md_path = _make_artifacts(temp_output_dir)
    _write_idem_sidecar(json_path, config_sha="cfg", content_sha="cnt")
    _age_artifacts(json_path, md_path, hours=100)
    sidecar = _read_idem_sidecar(json_path)

    assert _idem_content_skip(json_path, md_path, sidecar, "cfg", "cnt") is True
