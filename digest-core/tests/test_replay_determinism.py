"""Determinism foundation (PR1).

Proves the PR1 acceptance criteria:
  * identical offline runs produce identical ``evidence_id``s and digest;
  * record -> replay reproduces the item-set (it used to empty because
    ``evidence_id`` was a fresh ``uuid4()`` per run);
  * subject thread ids are stable across processes (no ``PYTHONHASHSEED``).

All offline / mocked — no EWS or LLM network calls.
"""

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from digest_core import run as runner
from digest_core.config import LLMConfig
from digest_core.evidence.split import EvidenceSplitter
from digest_core.ingest.ews import NormalizedMessage
from digest_core.llm.gateway import LLMGateway
from digest_core.threads.build import ThreadBuilder

PARAGRAPH = (
    "Please prepare the project status update and send it to me by Friday. "
    "We need at least sixty four tokens in this chunk so the splitter keeps it "
    "instead of filtering it out as too short for downstream extraction work."
)
LONG_BODY = (
    "Пожалуйста, подготовь обновление статуса проекта и пришли его мне до пятницы. " * 12
).strip()


def _expected_evidence_id(msg_id: str, conv: str, mi: int, ci: int, content: str) -> str:
    digest = hashlib.sha256(
        "\x01".join([msg_id, conv, str(mi), str(ci), content]).encode("utf-8")
    ).hexdigest()[:16]
    return "ev_" + digest


def _mock_message(**over):
    """A Mock(spec=NormalizedMessage) shaped like tests/test_evidence_split.py."""
    msg = Mock(spec=NormalizedMessage)
    msg.msg_id = over.get("msg_id", "msg-1")
    msg.conversation_id = over.get("conversation_id", "conv-1")
    msg.sender_email = "sender@example.com"
    msg.subject = over.get("subject", "Test Subject")
    msg.to_recipients = ["user@example.com"]
    msg.cc_recipients = []
    msg.datetime_received = datetime(2024, 12, 25, 12, 0, 0, tzinfo=timezone.utc)
    msg.importance = "Normal"
    msg.is_flagged = False
    msg.has_attachments = False
    msg.attachment_types = []
    msg.text_body = over.get("text_body", PARAGRAPH)
    return msg


def _real_message() -> NormalizedMessage:
    return NormalizedMessage(
        msg_id="msg-1",
        conversation_id="conv-1",
        datetime_received=datetime(2026, 3, 29, 9, 0, tzinfo=timezone.utc),
        sender_email="manager@corp.com",
        subject="Статус проекта",
        text_body=LONG_BODY,
        to_recipients=["user@corp.com"],
        cc_recipients=[],
        importance="High",
        body_norm=LONG_BODY,
        received_at=datetime(2026, 3, 29, 9, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# evidence_id: deterministic content hash (was uuid4)
# ---------------------------------------------------------------------------


def test_evidence_id_is_deterministic_content_hash():
    splitter = EvidenceSplitter()
    a = splitter._create_evidence_chunk(PARAGRAPH, "conv-1", _mock_message(), 0, 0)
    b = splitter._create_evidence_chunk(PARAGRAPH, "conv-1", _mock_message(), 0, 0)

    assert a.evidence_id == b.evidence_id
    assert a.evidence_id.startswith("ev_")
    assert len(a.evidence_id) == len("ev_") + 16
    assert a.evidence_id == _expected_evidence_id("msg-1", "conv-1", 0, 0, PARAGRAPH)


@pytest.mark.parametrize(
    "kwargs_a, kwargs_b",
    [
        ({"msg_id": "msg-1"}, {"msg_id": "msg-2"}),
        ({"conversation_id": "conv-1"}, {"conversation_id": "conv-9"}),
        ({"text_body": PARAGRAPH}, {"text_body": PARAGRAPH + " extra"}),
    ],
)
def test_evidence_id_changes_when_any_input_changes(kwargs_a, kwargs_b):
    splitter = EvidenceSplitter()
    msg_a = _mock_message(**kwargs_a)
    msg_b = _mock_message(**kwargs_b)
    a = splitter._create_evidence_chunk(msg_a.text_body, msg_a.conversation_id, msg_a, 0, 0)
    b = splitter._create_evidence_chunk(msg_b.text_body, msg_b.conversation_id, msg_b, 0, 0)
    assert a.evidence_id != b.evidence_id


def test_evidence_id_changes_with_indices():
    splitter = EvidenceSplitter()
    base = splitter._create_evidence_chunk(PARAGRAPH, "conv-1", _mock_message(), 0, 0)
    by_msg_idx = splitter._create_evidence_chunk(PARAGRAPH, "conv-1", _mock_message(), 1, 0)
    by_chunk_idx = splitter._create_evidence_chunk(PARAGRAPH, "conv-1", _mock_message(), 0, 1)
    assert len({base.evidence_id, by_msg_idx.evidence_id, by_chunk_idx.evidence_id}) == 3


def test_split_message_content_evidence_ids_stable_across_runs():
    splitter = EvidenceSplitter()
    # LONG_BODY clears the min_tokens_per_chunk filter so chunks are produced.
    ids_run1 = [
        c.evidence_id
        for c in splitter._split_message_content(_mock_message(text_body=LONG_BODY), "conv-1", 0)
    ]
    ids_run2 = [
        c.evidence_id
        for c in splitter._split_message_content(_mock_message(text_body=LONG_BODY), "conv-1", 0)
    ]
    assert ids_run1 and ids_run1 == ids_run2


# ---------------------------------------------------------------------------
# subject thread id: stable content hash (was PYTHONHASHSEED-randomized hash())
# ---------------------------------------------------------------------------


def test_subject_thread_id_is_deterministic_content_hash():
    subject = "Re: Weekly status report"
    messages = [
        NormalizedMessage(
            msg_id="m-1",
            conversation_id="",  # forces the normalized-subject thread strategy
            subject=subject,
            text_body="body",
            sender_email="a@corp.com",
            datetime_received=datetime(2026, 3, 29, tzinfo=timezone.utc),
            to_recipients=["user@corp.com"],
            cc_recipients=[],
        )
    ]

    builder_1 = ThreadBuilder()
    groups_1 = builder_1._group_messages_into_threads(
        messages, builder_1._build_msg_id_index(messages)
    )
    builder_2 = ThreadBuilder()
    groups_2 = builder_2._group_messages_into_threads(
        messages, builder_2._build_msg_id_index(messages)
    )

    key_1 = next(iter(groups_1))
    key_2 = next(iter(groups_2))
    assert key_1 == key_2  # stable across builder instances (would vary with hash())
    assert key_1.startswith("subj_")
    assert len(key_1) == len("subj_") + 16

    normalized, _ = builder_1.subject_normalizer.normalize(subject)
    expected = "subj_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    assert key_1 == expected


# ---------------------------------------------------------------------------
# record -> replay reproduces the item-set (the headline fix)
# ---------------------------------------------------------------------------


def _mock_http_response(content: str):
    response = Mock()
    response.status_code = 200
    response.headers = {}
    response.raise_for_status = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }
    return response


def _payload_citing(evidence):
    return json.dumps(
        {
            "sections": [
                {
                    "title": "Мои действия",
                    "items": [
                        {
                            "title": "Подготовить обновление статуса проекта",
                            "due": "2026-03-30",
                            "evidence_id": evidence[0].evidence_id,
                            "confidence": 0.9,
                            "source_ref": {
                                "type": "email",
                                "msg_id": evidence[0].source_ref["msg_id"],
                            },
                        }
                    ],
                }
            ]
        }
    )


def _items(result):
    return [item for section in result.get("sections", []) for item in section.get("items", [])]


def _split_real_message():
    return EvidenceSplitter()._split_message_content(_real_message(), "conv-1", 0)


def test_record_then_replay_reproduces_itemset(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_TOKEN", "test-token")
    recording = tmp_path / "llm-recording.json"
    config = LLMConfig(
        endpoint="https://api.example.com/v1/chat", model="qwen35-397b-a17b", timeout_s=30
    )

    # RECORD: real gateway, HTTP mocked; the recorded response cites the
    # content-hash evidence_id of the freshly split chunk.
    chunks_run1 = _split_real_message()
    assert chunks_run1, "fixture must produce at least one evidence chunk"
    gw_record = LLMGateway(config, record_llm=str(recording))
    gw_record.client.post = Mock(return_value=_mock_http_response(_payload_citing(chunks_run1)))
    out_record = gw_record.extract_actions(chunks_run1, "Return strict JSON", "trace-record")
    assert _items(out_record), "record run keeps the cited item"

    # REPLAY: re-split the same message (a fresh 'run'). With content-hash ids the
    # evidence_id matches the recorded citation, so the item survives validation.
    chunks_run2 = _split_real_message()
    assert chunks_run2[0].evidence_id == chunks_run1[0].evidence_id  # determinism
    gw_replay = LLMGateway(config, replay_llm=str(recording))
    gw_replay.client.post = Mock(side_effect=RuntimeError("replay must not hit the network"))
    out_replay = gw_replay.extract_actions(chunks_run2, "Return strict JSON", "trace-replay")

    assert _items(out_replay), "replay reproduces the item-set (regression: used to empty)"
    assert _items(out_replay)[0]["evidence_id"] == chunks_run1[0].evidence_id
    gw_replay.client.post.assert_not_called()


# ---------------------------------------------------------------------------
# identical offline pipeline runs produce an identical digest
# ---------------------------------------------------------------------------


class _DummyMetrics:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeDeliverer:
    def __init__(self, config):
        self.config = config

    def deliver_digest(self, digest, json_path=None):
        return {"status": "sent", "parts": 1}


class _EchoGateway:
    """Echoes the first chunk's evidence_id — deterministic given stable ids."""

    def __init__(self, *args, **kwargs):
        self.last_request_meta = {
            "tokens_in": 1,
            "tokens_out": 1,
            "http_status": 200,
            "latency_ms": 1,
            "retry_count": 0,
            "validation_errors": 0,
        }

    def extract_actions(self, evidence, prompt_template, trace_id):
        return {
            "sections": [
                {
                    "title": "Мои действия",
                    "items": [
                        {
                            "title": "Подготовить обновление статуса проекта",
                            "due": "2026-03-30",
                            "evidence_id": evidence[0].evidence_id,
                            "confidence": 0.9,
                            "source_ref": {
                                "type": "email",
                                "msg_id": evidence[0].source_ref["msg_id"],
                            },
                        }
                    ],
                }
            ]
        }

    def get_request_stats(self):
        return {"last_latency_ms": 1, "model": "qwen35-397b-a17b", "timeout_s": 120}


def _run_pipeline_once(monkeypatch, out_dir, snapshot_path):
    monkeypatch.setattr(runner, "MetricsCollector", _DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", _EchoGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", _FakeDeliverer)
    return runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(out_dir),
        model="qwen35-397b-a17b",
        window="calendar_day",
        # Per-run state isolation: with the dedup ledger ON by default (D3),
        # determinism is f(input, state) — identical runs must start from
        # identical (fresh) state, or run 2 is legitimately annotated.
        state=str(out_dir / "state"),
        force=True,
        replay_ingest=str(snapshot_path),
    )


def _evidence_ids(digest_json):
    return [
        item.get("evidence_id")
        for section in digest_json.get("sections", [])
        for item in section.get("items", [])
    ]


def test_identical_runs_produce_identical_digest(monkeypatch, tmp_path):
    snapshot = tmp_path / "snapshot.json"
    runner._dump_ingest_snapshot(snapshot, [_real_message()], "2026-03-29")
    out_1 = tmp_path / "out1"
    out_2 = tmp_path / "out2"

    assert _run_pipeline_once(monkeypatch, out_1, snapshot)
    assert _run_pipeline_once(monkeypatch, out_2, snapshot)

    digest_1 = json.loads((out_1 / "digest-2026-03-29.json").read_text(encoding="utf-8"))
    digest_2 = json.loads((out_2 / "digest-2026-03-29.json").read_text(encoding="utf-8"))

    # Only the per-run trace_id is volatile; the digest content is byte-stable.
    digest_1.pop("trace_id", None)
    digest_2.pop("trace_id", None)
    assert digest_1 == digest_2

    ids = _evidence_ids(digest_1)
    assert ids and all(eid.startswith("ev_") for eid in ids)
