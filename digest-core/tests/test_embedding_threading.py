"""Embedding-assisted thread merging (REDESIGN PR12a, cosine tier)."""

from __future__ import annotations

from datetime import datetime, timezone

from digest_core.ingest.ews import NormalizedMessage
from digest_core.threads.build import ThreadBuilder
from digest_core.threads.embedding_merge import (
    EmbeddingThreadMerger,
    cosine,
    representative_text,
)

BASE_TIME = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def _msg(msg_id: str, subject: str, body: str, conversation_id: str = "") -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=conversation_id,
        subject=subject,
        sender_email="a@corp.ru",
        to_recipients=["b@corp.ru"],
        cc_recipients=[],
        datetime_received=BASE_TIME,
        text_body=body,
    )


class FakeEmbeddings:
    """Returns a fixed vector per text via substring routing; counts calls."""

    def __init__(self, routes):
        self.routes = routes  # [(substring, vector), ...]
        self.calls = 0
        self.batches = []

    def embed(self, texts):
        self.calls += 1
        self.batches.append(list(texts))
        out = []
        for text in texts:
            for needle, vector in self.routes:
                if needle in text:
                    out.append(vector)
                    break
            else:
                out.append([0.0, 0.0, 1.0])
        return out

    def close(self):
        pass


class TestPrimitives:
    def test_representative_text_subject_plus_body_head(self):
        msg = _msg("m1", "Project X status", "line one  line two " + "x" * 600)
        text = representative_text([msg])
        assert text.startswith("Project X status\n")
        assert len(text) <= len("Project X status\n") + 500

    def test_representative_uses_earliest_message(self):
        early = _msg("m1", "First", "early body")
        late = NormalizedMessage(
            msg_id="m2",
            conversation_id="",
            subject="Later",
            sender_email="a@corp.ru",
            to_recipients=[],
            cc_recipients=[],
            datetime_received=BASE_TIME.replace(hour=12),
            text_body="late body",
        )
        assert representative_text([late, early]).startswith("First")

    def test_cosine(self):
        assert cosine([1, 0], [1, 0]) == 1.0
        assert cosine([1, 0], [0, 1]) == 0.0
        assert cosine([0, 0], [1, 0]) == 0.0  # zero vector is never similar


class TestMerger:
    def _groups(self):
        return {
            "subj_aaa": [_msg("m1", "Re: Проект X", "обновление статуса проекта")],
            "subj_bbb": [_msg("m2", "Вопрос по проекту X", "вопрос про статус проекта")],
            "subj_ccc": [_msg("m3", "Lunch menu", "сегодня в столовой")],
            "conv_zzz": [_msg("m4", "Re: Проект X", "из EWS", conversation_id="zzz")],
        }

    def _merger(self, embeddings, **kwargs):
        defaults = dict(similarity_threshold=0.85, max_candidates=64)
        defaults.update(kwargs)
        return EmbeddingThreadMerger(embeddings, **defaults)

    def test_merges_close_pair_under_smallest_id(self):
        embeddings = FakeEmbeddings(
            [("Проект X", [1.0, 0.0, 0.0]), ("проекту X", [0.99, 0.14, 0.0])]
        )
        merger = self._merger(embeddings)
        merged = merger.merge(self._groups())
        assert "subj_aaa" in merged and "subj_bbb" not in merged
        assert {m.msg_id for m in merged["subj_aaa"]} == {"m1", "m2"}
        assert merger.merges_made == 1
        # No message lost anywhere (merge-only invariant).
        total = sum(len(v) for v in merged.values())
        assert total == 4

    def test_one_batched_embed_call(self):
        embeddings = FakeEmbeddings([])
        self._merger(embeddings).merge(self._groups())
        assert embeddings.calls == 1
        assert len(embeddings.batches[0]) == 3  # only the subj_/single_ candidates

    def test_conv_groups_are_never_candidates(self):
        embeddings = FakeEmbeddings([("из EWS", [1.0, 0.0, 0.0])])
        merged = self._merger(embeddings).merge(self._groups())
        assert "conv_zzz" in merged
        assert all("из EWS" not in text for text in embeddings.batches[0])

    def test_below_threshold_stays_separate(self):
        embeddings = FakeEmbeddings(
            [("Проект X", [1.0, 0.0, 0.0]), ("проекту X", [0.5, 0.86, 0.0])]
        )
        merged = self._merger(embeddings).merge(self._groups())
        assert "subj_aaa" in merged and "subj_bbb" in merged

    def test_candidate_cap_skips_whole_tier(self):
        embeddings = FakeEmbeddings([])
        groups = self._groups()
        merged = self._merger(embeddings, max_candidates=2).merge(groups)
        assert merged == groups  # skipped, logged — never a silent partial merge
        assert embeddings.calls == 0

    def test_embed_failure_degrades_to_heuristics(self):
        class Boom(FakeEmbeddings):
            def embed(self, texts):
                raise ConnectionError("gateway down")

        groups = self._groups()
        merged = self._merger(Boom([])).merge(groups)
        assert merged == groups

    def test_vector_count_mismatch_degrades(self):
        class Short(FakeEmbeddings):
            def embed(self, texts):
                return [[1.0, 0.0]]

        groups = self._groups()
        assert self._merger(Short([])).merge(groups) == groups

    def test_fewer_than_two_candidates_is_noop(self):
        embeddings = FakeEmbeddings([])
        groups = {"conv_z": [_msg("m1", "s", "b", conversation_id="z")]}
        assert self._merger(embeddings).merge(groups) == groups
        assert embeddings.calls == 0


class TestBuilderIntegration:
    def _messages(self):
        return [
            _msg("m1", "Re: Проект X", "обновление статуса проекта"),
            _msg("m2", "Вопрос по проекту X", "вопрос про статус проекта"),
        ]

    def test_without_merger_grouping_is_unchanged(self):
        threads = ThreadBuilder().build_threads(self._messages())
        assert len(threads) == 2  # different normalized subjects never merge today

    def test_with_merger_threads_merge_and_stats_count(self):
        embeddings = FakeEmbeddings(
            [("Проект X", [1.0, 0.0, 0.0]), ("проекту X", [0.99, 0.14, 0.0])]
        )
        merger = EmbeddingThreadMerger(embeddings, similarity_threshold=0.85, max_candidates=64)
        builder = ThreadBuilder(embedding_merger=merger)
        threads = builder.build_threads(self._messages())
        assert len(threads) == 1
        assert threads[0].message_count == 2
        assert builder.stats["threads_merged_by_embedding"] == 1


class TestRunWiring:
    def _ctx(self, tmp_path, *, embedding_merge: bool, replay_llm=None, record_llm=None):
        from types import SimpleNamespace

        from digest_core.config import LLMConfig, ThreadingConfig
        from digest_core.progress import NullSink

        return SimpleNamespace(
            config=SimpleNamespace(
                llm=LLMConfig(endpoint="https://gw.corp/api/v1/chat", model="qwen35-397b-a17b"),
                threading=ThreadingConfig(embedding_merge=embedding_merge),
            ),
            rate_broker=None,
            replay_llm=str(replay_llm) if replay_llm else None,
            record_llm=str(record_llm) if record_llm else None,
            trace_id="t",
            sink=NullSink(),
        )

    def test_flag_off_returns_none(self, tmp_path):
        from digest_core.run import _build_thread_embeddings

        assert _build_thread_embeddings(self._ctx(tmp_path, embedding_merge=False)) is None

    def test_replay_without_sidecar_disables_tier(self, tmp_path):
        from digest_core.run import _build_thread_embeddings

        ctx = self._ctx(tmp_path, embedding_merge=True, replay_llm=tmp_path / "rec.json")
        assert _build_thread_embeddings(ctx) is None  # fidelity-only gate

    def test_replay_with_sidecar_uses_it(self, tmp_path):
        from digest_core.run import _build_thread_embeddings

        recording = tmp_path / "rec.json"
        sidecar = tmp_path / "rec.json.fleet.json"
        sidecar.write_text('{"endpoints": {}}', encoding="utf-8")
        client = _build_thread_embeddings(
            self._ctx(tmp_path, embedding_merge=True, replay_llm=recording)
        )
        assert client is not None
        assert client._replay_data == {"endpoints": {}}
        client.close()

    def test_live_client_carries_stage_and_model(self, tmp_path):
        from digest_core.run import _build_thread_embeddings

        client = _build_thread_embeddings(self._ctx(tmp_path, embedding_merge=True))
        assert client is not None
        assert client._stage == "embeddings"
        assert client.model == "bge-m3"
        client.close()
