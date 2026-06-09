"""Fused relevance score, gated by enable_relevance (PR9).

Default (off) -> legacy enhanced score (byte-identical, proven by the bucket /
balanced / selector / e2e suites staying green). On -> w_meta*metadata +
w_rerank*relevance with a metadata-only fallback when no scorer is available.
"""

from digest_core.config import SelectionWeightsConfig
from digest_core.evidence.signals import calculate_sender_rank
from digest_core.evidence.split import EvidenceChunk
from digest_core.select.context import ContextSelector
from digest_core.select.relevance import RelevanceScorer


def _chunk(
    evidence_id, content="some content", sender_rank=1, addressed=False, importance="Normal"
):
    return EvidenceChunk(
        evidence_id=evidence_id,
        conversation_id="c",
        content=content,
        token_count=50,
        priority_score=0.0,
        source_ref={"msg_id": "m", "type": "email"},
        message_metadata={
            "received_at": "2024-01-15T10:00:00Z",
            "importance": importance,
            "is_flagged": False,
            "attachment_types": [],
        },
        addressed_to_me=addressed,
        signals={
            "action_verbs": [],
            "dates": [],
            "contains_question": False,
            "sender_rank": sender_rank,
        },
    )


# --- gating -----------------------------------------------------------------


def test_relevance_off_uses_legacy_score():
    selector = ContextSelector(weights_config=SelectionWeightsConfig(enable_relevance=False))
    scored = selector._calculate_enhanced_scores([_chunk("ev-1", addressed=True)])
    assert scored[0].priority_score == selector._legacy_enhanced_score(
        _chunk("ev-1", addressed=True)
    )


def test_relevance_off_never_calls_scorer():
    class BoomScorer:
        def score_chunks(self, chunks):
            raise AssertionError("must not score when relevance is off")

    selector = ContextSelector(
        weights_config=SelectionWeightsConfig(enable_relevance=False), relevance_scorer=BoomScorer()
    )
    selector._calculate_enhanced_scores([_chunk("ev-1")])  # no raise


def test_fused_score_combines_metadata_and_relevance():
    class StubScorer:
        def score_chunks(self, chunks):
            return {"ev-1": 1.0, "ev-2": 0.0}

    weights = SelectionWeightsConfig(enable_relevance=True, w_meta=1.0, w_rerank=10.0)
    selector = ContextSelector(weights_config=weights, relevance_scorer=StubScorer())
    scored = selector._calculate_enhanced_scores([_chunk("ev-1"), _chunk("ev-2")])
    by_id = {c.evidence_id: c.priority_score for c in scored}
    assert by_id["ev-1"] - by_id["ev-2"] == 10.0


def test_fused_falls_back_to_metadata_without_scorer():
    selector = ContextSelector(
        weights_config=SelectionWeightsConfig(enable_relevance=True), relevance_scorer=None
    )
    scored = selector._calculate_enhanced_scores([_chunk("ev-1", addressed=True)])
    assert scored[0].priority_score == selector._metadata_score(_chunk("ev-1", addressed=True))


def test_metadata_score_drops_textual_terms():
    selector = ContextSelector()
    plain = _chunk("a")
    textual = _chunk("b")
    textual.signals = {
        "action_verbs": ["do", "send"],
        "dates": ["2026-01-01"],
        "contains_question": True,
        "sender_rank": 1,
    }
    assert selector._metadata_score(plain) == selector._metadata_score(textual)
    assert selector._legacy_enhanced_score(textual) > selector._legacy_enhanced_score(plain)


# --- RelevanceScorer --------------------------------------------------------


def test_relevance_scorer_cosine_ranks_aligned_higher():
    class Emb:
        def embed(self, texts):
            return [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]  # query, ev-1 aligned, ev-2 orthogonal

    scores = RelevanceScorer("q", embeddings=Emb()).score_chunks([_chunk("ev-1"), _chunk("ev-2")])
    assert scores["ev-1"] > scores["ev-2"]
    assert abs(scores["ev-1"] - 1.0) < 1e-6


def test_relevance_scorer_reranker_overrides_topk():
    class Emb:
        def embed(self, texts):
            return [[1.0, 0.0]] * len(texts)

    class Rer:
        def score(self, query, docs):
            return [0.123] * len(docs)

    scores = RelevanceScorer("q", embeddings=Emb(), reranker=Rer(), top_k=5).score_chunks(
        [_chunk("ev-1")]
    )
    assert scores["ev-1"] == 0.123


def test_relevance_scorer_empty_without_query_or_client():
    assert RelevanceScorer("", embeddings=object()).score_chunks([_chunk("ev-1")]) == {}
    assert RelevanceScorer("q", embeddings=None).score_chunks([_chunk("ev-1")]) == {}


# --- sender_rank ------------------------------------------------------------


def test_sender_rank_important_senders():
    assert calculate_sender_rank("ceo@corp.com") == 1  # no list -> legacy constant
    assert calculate_sender_rank("ceo@corp.com", ["ceo@"]) == 2
    assert calculate_sender_rank("intern@corp.com", ["ceo@"]) == 1
    assert calculate_sender_rank("x@example.com", ["example.com"]) == 2


# --- survivor determinism ---------------------------------------------------


def _budget_pressed_chunks():
    chunks = [
        _chunk(f"ev-{i}", content=f"тема {i} " * 40, addressed=(i % 2 == 0)) for i in range(8)
    ]
    for chunk in chunks:
        chunk.token_count = 2000
    return chunks


def test_selection_survivors_deterministic_under_budget_cut():
    survivors_1 = [
        c.evidence_id
        for c in ContextSelector().select_context(_budget_pressed_chunks(), max_tokens=4000)
    ]
    survivors_2 = [
        c.evidence_id
        for c in ContextSelector().select_context(_budget_pressed_chunks(), max_tokens=4000)
    ]
    assert survivors_1 and survivors_1 == survivors_2
