"""Source-neutral envelope + resilient multi-source ingest (PR12b)."""

from datetime import datetime, timezone

from digest_core.ingest.envelope import (
    envelopes_from_messages,
    messages_from_envelopes,
)
from digest_core.ingest.ews import NormalizedMessage
from digest_core.ingest.source_adapter import EWSSourceAdapter, SourceAdapter, run_sources


def _msg(msg_id="m-1"):
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id="c",
        subject="s",
        text_body="b",
        sender_email="a@b.com",
        datetime_received=datetime(2026, 3, 29, tzinfo=timezone.utc),
        to_recipients=["u@corp"],
        cc_recipients=[],
    )


class _FakeAdapter:
    def __init__(self, name, messages=None, boom=False):
        self.name = name
        self._messages = messages or []
        self._boom = boom

    def fetch(self, digest_date):
        if self._boom:
            raise RuntimeError("source down")
        return self._messages


def test_envelope_roundtrip():
    messages = [_msg("m-1"), _msg("m-2")]
    envelopes = envelopes_from_messages("ews", messages)
    assert [e.source for e in envelopes] == ["ews", "ews"]
    assert [e.msg_id for e in envelopes] == ["m-1", "m-2"]
    assert messages_from_envelopes(envelopes) == messages


def test_run_sources_collects_all_sources():
    adapters = [_FakeAdapter("ews", [_msg("m-1")]), _FakeAdapter("slack", [_msg("m-2")])]
    envelopes = run_sources(adapters, "2026-03-29")
    assert {e.source for e in envelopes} == {"ews", "slack"}
    assert len(envelopes) == 2


def test_run_sources_skips_failing_source():
    adapters = [_FakeAdapter("ews", [_msg("m-1")]), _FakeAdapter("down", boom=True)]
    envelopes = run_sources(adapters, "2026-03-29")
    assert [e.source for e in envelopes] == ["ews"]  # one source down is not fatal


def test_run_sources_strict_reraises_failing_source():
    # strict=True is what the sole live EWS source uses: a fetch failure must
    # propagate so the run's degradation policy sees the real exception.
    import pytest

    adapters = [_FakeAdapter("down", boom=True)]
    with pytest.raises(RuntimeError, match="source down"):
        run_sources(adapters, "2026-03-29", strict=True)


def test_ews_adapter_delegates_to_fetch_messages():
    class FakeIngest:
        time_config = object()

        def __init__(self):
            self.called = None

        def fetch_messages(self, digest_date, time_config):
            self.called = (digest_date, time_config)
            return [_msg("m-9")]

    ingest = FakeIngest()
    adapter = EWSSourceAdapter(ingest)
    assert adapter.name == "ews"
    assert [m.msg_id for m in adapter.fetch("2026-03-29")] == ["m-9"]
    assert ingest.called[0] == "2026-03-29"


def test_fake_adapter_satisfies_protocol():
    assert isinstance(_FakeAdapter("x"), SourceAdapter)
