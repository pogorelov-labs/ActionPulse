"""Driver-independent store unit tests (URN, content hash, row mapping, key pragma).

These run everywhere (no SQLCipher driver needed) — they exercise pure-Python
projection + helpers, so coverage holds even on a runner without the store extra.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.config import StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import db as store_db
from digest_core.store._driver import key_pragma
from digest_core.store.models import build_urn, content_hash, message_to_row
from digest_core.store.schema import CURRENT_SCHEMA_VERSION


def test_open_without_driver_raises_clear_error(monkeypatch):
    """With the SQLCipher driver absent, open() degrades to a StoreError with a
    hint — the import graph never required the driver to get here."""
    monkeypatch.setattr(store_db, "HAS_SQLCIPHER", False)
    with pytest.raises(store_db.StoreError) as exc:
        store_db.MessageStore.open(StoreConfig(db_path="/tmp/should-not-be-created.db"))
    assert "uv sync --extra store" in str(exc.value)


def test_build_urn_email_and_mm():
    assert build_urn("email", "abc@corp") == "urn:email:abc@corp"
    assert build_urn("mm", "mm:post123") == "urn:mm:post123"
    # mm id without the prefix still yields a usable urn
    assert build_urn("mm", "post123") == "urn:mm:post123"


def test_content_hash_deterministic_and_sensitive():
    a = content_hash("urn:email:x", "Subject", "body text")
    assert a == content_hash("urn:email:x", "Subject", "body text")
    assert a != content_hash("urn:email:x", "Subject", "different body")
    assert a != content_hash("urn:email:x", "Other", "body text")


def test_key_pragma_raw_vs_passphrase():
    raw = key_pragma("ab" * 32)  # 64 hex chars → raw key
    assert raw == "PRAGMA key = \"x'" + "ab" * 32 + "'\""
    passphrase = key_pragma("s3cr3t")  # not 64-hex → passphrase form
    assert passphrase == "PRAGMA key = 's3cr3t'"
    # single quotes are doubled to escape
    assert key_pragma("a'b") == "PRAGMA key = 'a''b'"


def test_message_to_row_maps_all_fields():
    msg = NormalizedMessage(
        msg_id="m1@corp",
        conversation_id="conv-1",
        datetime_received=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        sender_email="ivan@corp",
        subject="Budget",
        text_body="approve the budget",
        to_recipients=["me@corp"],
        cc_recipients=[],
        importance="High",
        is_flagged=True,
        has_attachments=True,
        attachment_types=["pdf"],
        from_name="Ivan Petrov",
        source="email",
    )
    row = message_to_row(msg, schema_version=CURRENT_SCHEMA_VERSION, pipeline_version="1.2.0")
    assert row["id"] == "urn:email:m1@corp"
    assert row["source"] == "email"
    assert row["thread_id"] == "conv-1"
    assert row["received_at"] == "2026-06-01T12:00:00+00:00"
    assert row["received_epoch"] == int(
        datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    )
    assert row["author_display"] == "Ivan Petrov"
    assert row["author_email"] == "ivan@corp"
    assert row["subject"] == "Budget"
    assert row["body_normalized"] == "approve the budget"
    # raw falls back to normalized when not provided
    assert row["body_raw"] == "approve the budget"
    assert row["is_flagged"] == 1 and row["has_attachments"] == 1
    assert row["attachment_types"] == '["pdf"]'
    assert row["to_recipients"] == '["me@corp"]'
    assert row["pipeline_version"] == "1.2.0"
    # v3.0 seams present but empty for now
    assert row["canonical_url"] is None and row["lang"] is None and row["risk_level"] is None


def test_message_to_row_uses_explicit_raw_body():
    msg = NormalizedMessage(
        msg_id="m2@corp",
        conversation_id=None,
        datetime_received=datetime(2026, 6, 1, tzinfo=timezone.utc),
        subject="S",
        text_body="<html>clean</html>",
        source="email",
    )
    row = message_to_row(msg, schema_version=1, raw_body="<html>raw original</html>")
    assert row["body_raw"] == "<html>raw original</html>"
    assert row["body_normalized"] == "<html>clean</html>"


def test_store_env_overrides_apply_without_yaml_section(monkeypatch):
    """Regression: DIGEST_STORE_* generic overrides must apply via Config() even when
    config.yaml has no `store:` section (it ships commented out). The store merge runs
    unconditionally so these don't silently no-op."""
    from digest_core.config import Config

    monkeypatch.setenv("DIGEST_STORE_VECTOR_DTYPE", "float16")
    monkeypatch.setenv("DIGEST_STORE_SEARCH_DEFAULT_MODE", "keyword")
    cfg = Config()
    assert cfg.store.vector_dtype == "float16"
    assert cfg.store.search_default_mode == "keyword"
