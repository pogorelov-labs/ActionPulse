"""InboxAPI source verbs (live EWS/MM). The adapters/clients are corp-only, so fakes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.api import ApiError, CorpOnlyError, InboxAPI
from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER
from digest_core.store.models import DM_AT_REST_REDACTION

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


def _config(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    cfg = Config()
    cfg.store.db_path = str(tmp_path / "m.db")
    return cfg


def _nm(msg_id, body, *, source="email", ctype=None):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c-" + msg_id,
        datetime_received=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        sender_email="ivan@corp",
        subject="S",
        text_body=body,
        source=source,
        mm_channel_type=ctype,
    )


class _FakeAdapter:
    def __init__(self, msgs):
        self._m = msgs

    def fetch(self, digest_date):
        return self._m


class _FakeMM:
    def get_my_channels(self):
        return [{"id": "c1", "name": "general", "display_name": "General", "type": "O"}]

    def get_post_reactions(self, post_id):
        return [{"emoji_name": "+1", "user_id": "u1"}]


def test_fetch_source_maps_and_does_not_persist(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        monkeypatch.setattr(
            api, "_source_adapter", lambda s: _FakeAdapter([_nm("a@corp", "budget")])
        )
        recs = api.fetch_source("ews", "2026-06-19")
        assert recs[0].message_id == "urn:email:a@corp" and recs[0].body == "budget"
        assert api.store.stats()["messages"] == 0  # read-shaped: never writes the store


def test_fetch_source_redacts_dm_body(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        dm = _nm("d@corp", "private colleague text", source="mm", ctype="D")
        monkeypatch.setattr(api, "_source_adapter", lambda s: _FakeAdapter([dm]))
        recs = api.fetch_source("mm", "2026-06-19")
        assert recs[0].body == DM_AT_REST_REDACTION and "colleague" not in recs[0].body


def test_fetch_source_corp_error_on_failure(tmp_path, monkeypatch):
    class _Boom:
        def fetch(self, digest_date):
            raise RuntimeError("no network")

    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        monkeypatch.setattr(api, "_source_adapter", lambda s: _Boom())
        with pytest.raises(CorpOnlyError):
            api.fetch_source("ews", "2026-06-19")


def test_list_containers_ews_reports_configured_folders(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api._config.ews.folders = ["Inbox", "Archive"]
        assert [c["name"] for c in api.list_containers("ews")] == ["Inbox", "Archive"]


def test_list_containers_and_reactions_mm(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        monkeypatch.setattr(api, "_mm_client", lambda: _FakeMM())
        assert api.list_containers("mm")[0]["display_name"] == "General"
        assert api.get_reactions("post1")[0]["emoji_name"] == "+1"


def test_unknown_source_errors(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        with pytest.raises(ApiError):
            api.list_containers("slack")
        with pytest.raises(ApiError):
            api.fetch_source("slack", "2026-06-19")
