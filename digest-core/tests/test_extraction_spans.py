"""Verbatim evidence-span validation (PR6, R2/R3).

Spans are kept only when the quote is an exact substring of a cited chunk body;
offsets are NOT taken from the model. By default missing/invalid spans annotate
(degrade-not-drop) rather than dropping the item.
"""

from digest_core.config import LLMConfig
from digest_core.evidence.split import EvidenceChunk
from digest_core.llm.gateway import LLMGateway

BODY = "Пожалуйста, пришли отчёт до пятницы и согласуй бюджет."


def _gateway(monkeypatch, **kwargs):
    monkeypatch.setenv("LLM_TOKEN", "t")
    config = LLMConfig(endpoint="https://x/v1/chat", model="qwen35-397b-a17b", timeout_s=30)
    return LLMGateway(config, **kwargs)


def _chunk(evidence_id="ev-1", msg_id="m-1", content=BODY):
    return EvidenceChunk(
        evidence_id=evidence_id,
        content=content,
        msg_id=msg_id,
        source_ref={"type": "email", "msg_id": msg_id},
        message_metadata={},
    )


def _item(spans):
    return {
        "title": "Прислать отчёт",
        "evidence_id": "ev-1",
        "confidence": 0.9,
        "source_ref": {"type": "email", "msg_id": "m-1"},
        "evidence_spans": spans,
    }


def test_verbatim_span_is_kept(monkeypatch):
    gw = _gateway(monkeypatch)
    spans = gw._validate_spans(
        [{"msg_id": "m-1", "quote": "пришли отчёт до пятницы"}], "ev-1", [_chunk()]
    )
    assert spans == [{"msg_id": "m-1", "quote": "пришли отчёт до пятницы"}]


def test_non_verbatim_span_is_dropped(monkeypatch):
    gw = _gateway(monkeypatch)
    spans = gw._validate_spans(
        [{"msg_id": "m-1", "quote": "send the report by Friday"}], "ev-1", [_chunk()]
    )
    assert spans == []


def test_malformed_spans_are_ignored(monkeypatch):
    gw = _gateway(monkeypatch)
    assert gw._validate_spans(None, "ev-1", [_chunk()]) == []
    assert gw._validate_spans([{"quote": ""}, "garbage", {}], "ev-1", [_chunk()]) == []


def test_span_msg_id_defaults_to_primary_chunk(monkeypatch):
    gw = _gateway(monkeypatch)
    spans = gw._validate_spans([{"quote": "согласуй бюджет"}], "ev-1", [_chunk()])
    assert spans == [{"msg_id": "m-1", "quote": "согласуй бюджет"}]


def test_validate_item_attaches_valid_span(monkeypatch):
    gw = _gateway(monkeypatch)
    out = gw._validate_item(
        _item([{"msg_id": "m-1", "quote": "пришли отчёт до пятницы"}]), [_chunk()]
    )
    assert out["evidence_spans"] == [{"msg_id": "m-1", "quote": "пришли отчёт до пятницы"}]


def test_item_kept_without_span_by_default(monkeypatch):
    # R3: degrade-not-drop. A non-verbatim span -> item survives, no spans key.
    gw = _gateway(monkeypatch)
    out = gw._validate_item(_item([{"msg_id": "m-1", "quote": "not in the body"}]), [_chunk()])
    assert out is not None
    assert "evidence_spans" not in out


def test_require_evidence_spans_drops_unsupported_item(monkeypatch):
    gw = _gateway(monkeypatch, require_evidence_spans=True)
    assert gw._validate_item(_item([]), [_chunk()]) is None
    # but a valid span keeps it
    kept = gw._validate_item(_item([{"msg_id": "m-1", "quote": "согласуй бюджет"}]), [_chunk()])
    assert kept is not None


def test_validation_crash_reports_discarded_items(monkeypatch):
    """A crashed validation must not masquerade as a clean empty day.

    sections=None blows up the section loop; the catch-all must return empty
    sections AND report at least one validation error in the request meta.
    """
    gw = _gateway(monkeypatch)
    gw.last_request_meta = {"validation_errors": 0}
    result = gw._validate_response({"sections": None}, [_chunk()])
    assert result == {"sections": []}
    assert gw.last_request_meta["validation_errors"] >= 1
