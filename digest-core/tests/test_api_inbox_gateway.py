"""InboxAPI gateway verbs + honest offline degradation. Needs the store extra."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.api import ApiError, GatewayUnavailable, InboxAPI
from digest_core.config import Config
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


class FakeEmbed:
    AXES = ("budget", "release", "vacation")

    def embed(self, texts):
        out = []
        for t in texts:
            tl = (t or "").lower()
            vec = [1.0 if ax in tl else 0.0 for ax in self.AXES]
            out.append(vec if any(vec) else [0.1, 0.1, 0.1])
        return out


class FailingEmbed:
    def embed(self, texts):
        raise RuntimeError("gateway down")


def _config(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    cfg = Config()
    cfg.store.db_path = str(tmp_path / "m.db")
    return cfg


def _msg(msg_id, body, *, thread=None, subject="S"):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=thread or ("c-" + msg_id),
        datetime_received=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        sender_email="ivan@corp",
        subject=subject,
        text_body=body,
    )


def test_search_degrades_to_keyword_when_gateway_down(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api.store.upsert_messages([_msg("a@corp", "approve the budget")])
        api._backend_client = FailingEmbed()  # inject an unreachable gateway
        hits = api.search("budget", mode="hybrid")  # degrades, does not raise
        assert {h.message_id for h in hits} == {"urn:email:a@corp"}
        assert all(h.provenance["method"] == "keyword" for h in hits)  # served keyword
        with pytest.raises(GatewayUnavailable):
            api.search("budget", mode="semantic", strict=True)


def test_related_offline_when_embedded(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api.store.upsert_messages(
            [
                _msg("b1@corp", "approve the quarterly budget"),
                _msg("b2@corp", "the budget review meeting"),
                _msg("r1@corp", "the release ships tomorrow"),
            ]
        )
        api.store.embed_backlog(FakeEmbed())
        api._backend_client = FakeEmbed()
        ids = [h.message_id for h in api.related("urn:email:b1@corp")]
        assert "urn:email:b1@corp" not in ids
        assert ids and ids[0] == "urn:email:b2@corp"


def test_ask_empty_store_answers_false_without_gateway(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        res = api.ask("anything at all")  # no messages → no gateway call
        assert res.answered is False


def test_ask_converts_unavailable_to_gateway_error(tmp_path, monkeypatch):
    from digest_core.ask import AskUnavailable

    def _boom(*a, **k):
        raise AskUnavailable("offline")

    monkeypatch.setattr("digest_core.ask.answer_question", _boom)
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        with pytest.raises(GatewayUnavailable):
            api.ask("x")


def test_summarize_thread(tmp_path, monkeypatch):
    from digest_core.ask import AskResult

    fake = AskResult("Q", "the summary", True, [], [], "m")
    monkeypatch.setattr("digest_core.ask.summarize_passages", lambda passages, *, config: fake)
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api.store.upsert_messages([_msg("a@corp", "hello", thread="T")])
        assert api.summarize_thread("T").answer == "the summary"
        with pytest.raises(ApiError):
            api.summarize_thread("no-such-thread")


def test_compare_cosine_and_term_diff(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api.store.upsert_messages(
            [
                _msg("b1@corp", "approve the budget plan"),
                _msg("b2@corp", "budget plan review"),
            ]
        )
        unembedded = api.compare("urn:email:b1@corp", "urn:email:b2@corp")
        assert unembedded.cosine is None  # no stored vectors yet
        assert "budget" in unembedded.shared_terms and "plan" in unembedded.shared_terms
        assert unembedded.distinct_a == ["approve"] and unembedded.distinct_b == ["review"]

        api.store.embed_backlog(FakeEmbed())
        embedded = api.compare("urn:email:b1@corp", "urn:email:b2@corp")
        assert embedded.cosine is not None and embedded.cosine > 0.9  # same budget axis

        with pytest.raises(ApiError):
            api.compare("urn:email:b1@corp", "urn:email:missing@corp")


def test_embed_backlog_and_reembed_via_api(tmp_path, monkeypatch):
    with InboxAPI.open(_config(tmp_path, monkeypatch)) as api:
        api.store.upsert_messages([_msg("a@corp", "the budget")])
        api._backend_client = FakeEmbed()
        assert api.embed_backlog()["embedded"] >= 1
        assert api.reembed(force=True)["embedded"] >= 1
