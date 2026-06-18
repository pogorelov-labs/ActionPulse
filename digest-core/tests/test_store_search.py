"""Search tests: FTS5 keyword + brute-force-cosine semantic + RRF hybrid.

Requires the `store` extra (skipped otherwise). Embeddings come from a tiny
deterministic fake backend so cosine ranking is assertable offline.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.config import StoreConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.store import HAS_SQLCIPHER, MessageStore

pytestmark = pytest.mark.skipif(not HAS_SQLCIPHER, reason="sqlcipher3 not installed (store extra)")


class FakeEmbed:
    """Keyword-axis embeddings: cosine=1 when the query shares a topic word."""

    AXES = ("budget", "release", "vacation")

    def embed(self, texts):
        out = []
        for t in texts:
            tl = (t or "").lower()
            vec = [1.0 if ax in tl else 0.0 for ax in self.AXES]
            if not any(vec):
                vec = [0.1, 0.1, 0.1]
            out.append(vec)
        return out


def _cfg(tmp_path, monkeypatch, **over):
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    return StoreConfig(db_path=str(tmp_path / "messages.db"), **over)


def _msg(msg_id, body, *, subject="Subject", source="email", when=None, mm_channel_type=None):
    # mm messages default to an OPEN channel ('O') — a kept, searchable work artifact.
    # (DMs are 'D'/'G'; a missing type now fails closed and is redacted, see store/dm.)
    if source == "mm" and mm_channel_type is None:
        mm_channel_type = "O"
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c-" + msg_id,
        datetime_received=when or datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        sender_email="ivan@corp",
        subject=subject,
        text_body=body,
        source=source,
        mm_channel_type=mm_channel_type,
    )


def _seed(store):
    store.upsert_messages(
        [
            _msg("a@corp", "please approve the quarterly budget", subject="Finance"),
            _msg("b@corp", "the release is scheduled for tomorrow", subject="Release"),
            _msg("mm:1", "обсудим релиз и budget на встрече", subject="General", source="mm"),
        ]
    )


def test_chunks_created_on_upsert(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        st = store.stats()
        assert st["chunks"] >= 3  # at least one chunk per message
        assert st["embeddings"] == 0  # not embedded until backlog runs


def test_keyword_search(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        hits = store.search("budget", mode="keyword")
        ids = {h.message_id for h in hits}
        assert "urn:email:a@corp" in ids
        assert all(h.provenance["method"] == "keyword" for h in hits)
        # Cyrillic keyword
        ru = store.search("релиз", mode="keyword")
        assert {h.message_id for h in ru} == {"urn:mm:1"}


def test_embed_backlog_then_semantic(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        res = store.embed_backlog(FakeEmbed())
        assert res["embedded"] == store.stats()["chunks"]
        # Re-running embeds nothing new (backlog is empty).
        assert store.embed_backlog(FakeEmbed())["embedded"] == 0

        hits = store.search("budget", mode="semantic", backend=FakeEmbed())
        assert hits, "expected semantic hits"
        # The budget message ranks first (cosine 1 vs 0 for release).
        assert hits[0].message_id in {"urn:email:a@corp", "urn:mm:1"}
        assert hits[0].provenance["method"] == "semantic"
        assert hits[0].provenance["cosine"] == pytest.approx(1.0, abs=1e-6)


def test_hybrid_search_fuses(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        store.embed_backlog(FakeEmbed())
        hits = store.search("budget", mode="hybrid", backend=FakeEmbed())
        assert hits
        assert hits[0].provenance["method"] == "hybrid"
        # The budget message appears in BOTH lists → wins the RRF fusion.
        assert hits[0].message_id == "urn:email:a@corp"


def test_semantic_requires_backend(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        with pytest.raises(ValueError):
            store.search("budget", mode="semantic", backend=None)


def test_source_filter(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        hits = store.search("budget", mode="keyword", source="mm")
        assert {h.message_id for h in hits} == {"urn:mm:1"}


def test_since_filter(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages(
            [
                _msg("old@corp", "old budget memo", when=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                _msg("new@corp", "new budget memo", when=datetime(2026, 6, 1, tzinfo=timezone.utc)),
            ]
        )
        hits = store.search("budget", mode="keyword", since="2026-03-01")
        assert {h.message_id for h in hits} == {"urn:email:new@corp"}


def test_content_change_recreates_chunks_and_clears_embeddings(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages([_msg("a@corp", "approve the budget")])
        store.embed_backlog(FakeEmbed())
        assert store.stats()["embeddings"] >= 1
        # Change the body → chunks recreated, old embeddings cascade-deleted.
        store.upsert_messages([_msg("a@corp", "talk about the release instead")])
        assert store.stats()["embeddings"] == 0  # backlog again
        store.embed_backlog(FakeEmbed())
        # Now semantic finds it under the new topic, not the old one.
        assert store.search("release", mode="semantic", backend=FakeEmbed())


# --------------------------------------------------------------------------- #
# Hardening (review findings)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query", ["budget AND status", "budget OR release", "AND", 'budget "', "NEAR"]
)
def test_fts_operator_queries_do_not_crash(tmp_path, monkeypatch, query):
    """User queries containing FTS5 operators/quotes are treated as literal terms,
    never parsed as syntax (which used to raise OperationalError and crash search)."""
    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        _seed(store)
        hits = store.search(query, mode="keyword")  # must not raise
        assert isinstance(hits, list)


def test_embed_backlog_count_mismatch_raises(tmp_path, monkeypatch):
    from digest_core.store.ingest import StoreEmbedError

    class _ShortEmbed:
        def embed(self, texts):
            return []  # fewer vectors than inputs

    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages([_msg("a@corp", "approve the budget")])
        with pytest.raises(StoreEmbedError):
            store.embed_backlog(_ShortEmbed())


def test_embed_backlog_dim_drift_raises(tmp_path, monkeypatch):
    from digest_core.store.ingest import StoreEmbedError

    class _DimEmbed:
        def __init__(self, dim):
            self.dim = dim

        def embed(self, texts):
            return [[0.1] * self.dim for _ in texts]

    with MessageStore.open(_cfg(tmp_path, monkeypatch)) as store:
        store.upsert_messages([_msg("a@corp", "first message")])
        store.embed_backlog(_DimEmbed(4))  # establishes dim=4 for the model
        store.upsert_messages([_msg("b@corp", "second message")])
        with pytest.raises(StoreEmbedError):
            store.embed_backlog(_DimEmbed(8))  # 8 != established 4 → reject


def test_reembed_force_after_model_switch(tmp_path, monkeypatch):
    db = str(tmp_path / "messages.db")
    monkeypatch.setenv("DIGEST_STORE_KEY", "ab" * 32)
    with MessageStore.open(StoreConfig(db_path=db, embedding_model="model-a")) as store:
        _seed(store)
        store.embed_backlog(FakeEmbed())
    # Switch the configured model: a plain reembed finds no work; search goes empty.
    with MessageStore.open(StoreConfig(db_path=db, embedding_model="model-b")) as store:
        assert store.embed_backlog(FakeEmbed())["embedded"] == 0
        assert store.search("budget", mode="semantic", backend=FakeEmbed()) == []
        # --force drops the stale vectors and re-embeds under model-b.
        assert store.reembed(FakeEmbed(), force=True)["embedded"] >= 1
        assert store.search("budget", mode="semantic", backend=FakeEmbed())


def test_bruteforce_max_rows_caps_loaded_vectors(tmp_path, monkeypatch):
    with MessageStore.open(_cfg(tmp_path, monkeypatch, bruteforce_max_rows=1)) as store:
        _seed(store)
        store.embed_backlog(FakeEmbed())
        # Cap=1 → at most one chunk's vector is loaded for scoring.
        hits = store.search("budget", mode="semantic", backend=FakeEmbed(), limit=10)
        assert len(hits) <= 1
