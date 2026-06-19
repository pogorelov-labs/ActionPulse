"""Structured-log redaction (_redact_sensitive_data) — secrets never reach the logs."""

from digest_core.observability.logs import _redact_sensitive_data


def _redact(event: dict) -> dict:
    return _redact_sensitive_data(None, "info", dict(event))


def test_named_credential_fields_masked():
    out = _redact(
        {
            "mm_pat": "secretpat1234",
            "llm_token": "tok_abc123",
            "authorization": "Bearer z",
            "access_token": "at_xyz",
            "path": "/var/out",  # 'pat' is a substring — must NOT be redacted (exact-key match)
            "msg_id": "urn:email:m1",
        }
    )
    assert out["mm_pat"] == "[[REDACTED]]"
    assert out["llm_token"] == "[[REDACTED]]"
    assert out["authorization"] == "[[REDACTED]]"
    assert out["access_token"] == "[[REDACTED]]"
    assert out["path"] == "/var/out"
    assert out["msg_id"] == "urn:email:m1"


def test_bearer_token_in_message_value_masked():
    out = _redact({"event": "calling gateway, Authorization: Bearer abcDEF1234567890tok"})
    assert "abcDEF1234567890tok" not in out["event"]
    assert "Bearer [[REDACTED]]" in out["event"]  # prefix kept, secret masked


def test_plain_text_untouched():
    out = _redact({"event": "ingested 247 messages from inbox", "count": 247})
    assert out["event"] == "ingested 247 messages from inbox"
    assert out["count"] == 247
