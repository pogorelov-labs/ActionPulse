"""Ask-your-inbox: RAG over the store (U8). Requires the `store` extra.

Offline: keyword retrieval (no embeddings) + a mocked LLMGateway, so the grounding
contract is assertable without the corp network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import digest_core.ask as ask_mod
from digest_core.config import StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER, MessageStore

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    return MessageStore.open(StoreConfig(db_path=str(tmp_path / "m.db")))


def _msg(msg_id, body):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c-" + msg_id,
        subject="Subject",
        text_body=body,
        sender_email="ivan@corp",
        datetime_received=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        source="email",
    )


class _FakeGateway:
    """Records the payload it received; returns a canned grounded verdict."""

    last_user = None

    def __init__(self, *a, **k):
        pass

    def judge(self, system, user, trace_id="ask"):
        _FakeGateway.last_user = user
        return {
            "answer": "Approve the Q4 budget by Friday.",
            "answered": True,
            "citations": [{"message_id": "urn:email:a@corp", "quote": "approve the budget"}],
        }

    def close(self):
        pass


def test_answer_is_grounded_and_cited(tmp_path, monkeypatch):
    monkeypatch.setattr(ask_mod, "LLMGateway", _FakeGateway)
    with _store(tmp_path, monkeypatch) as store:
        store.upsert_messages([_msg("a@corp", "please approve the budget by Friday")])
        res = ask_mod.answer_question(store, "budget", mode="keyword")
    assert res.answered and "budget" in res.answer.lower()
    assert res.citations and res.citations[0].message_id == "urn:email:a@corp"
    # The retrieved passage (fuller than the snippet) actually reached the model.
    assert res.passages and "approve the budget" in res.passages[0]["text"]
    assert "approve the budget" in (_FakeGateway.last_user or "")


def test_no_hits_returns_not_found_without_calling_gateway(tmp_path, monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def judge(self, *a, **k):
            raise AssertionError("gateway must not be called when there are no passages")

        def close(self):
            pass

    monkeypatch.setattr(ask_mod, "LLMGateway", _Boom)
    with _store(tmp_path, monkeypatch) as store:
        store.upsert_messages([_msg("a@corp", "lunch plans for tuesday")])
        res = ask_mod.answer_question(store, "quarterly budget forecast", mode="keyword")
    assert res.answered is False and not res.citations and not res.passages


def test_gateway_failure_raises_ask_unavailable(tmp_path, monkeypatch):
    class _Down:
        def __init__(self, *a, **k):
            pass

        def judge(self, *a, **k):
            raise RuntimeError("offline")

        def close(self):
            pass

    monkeypatch.setattr(ask_mod, "LLMGateway", _Down)
    with _store(tmp_path, monkeypatch) as store:
        store.upsert_messages([_msg("a@corp", "please approve the budget")])
        with pytest.raises(ask_mod.AskUnavailable):
            ask_mod.answer_question(store, "budget", mode="keyword")


def test_context_passages_uses_fuller_text(tmp_path, monkeypatch):
    with _store(tmp_path, monkeypatch) as store:
        store.upsert_messages([_msg("a@corp", "approve the budget by Friday please and confirm")])
        hits = store.search("budget", mode="keyword")
        passages = store.context_passages(hits)
    assert passages[0]["message_id"] == "urn:email:a@corp"
    assert "approve the budget by Friday" in passages[0]["text"]  # fuller than a 200-char snippet
