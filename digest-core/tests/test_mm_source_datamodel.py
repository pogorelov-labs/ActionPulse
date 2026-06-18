"""P1a: offline Mattermost-source data-model foundation.

These tests exercise the cross-cutting plumbing that lets a ``source="mm"``
message flow through the core ingest path WITHOUT a real Mattermost adapter
(that is P1b). Everything here is synthetic, offline, and payload-free.

The non-negotiable invariant under test is that the EMAIL/EWS path stays
byte-identical while the synthetic mm message exercises the new branches:

  * the markdown-safe normalize bypass (quote-cleaner / HTML strip skipped),
  * source-aware threading (no wrong subject-similarity merge of mm roots),
  * authoritative ``source_ref['type']`` driven by ``message.source``,
  * ``msg_id`` "mm:" namespacing surviving the citation/enrichment joins,
  * the ``--sources`` selector (EWS strict, unknown source = hard error),
  * the ``source`` field staying OUT of ``_content_sha256`` (idempotency),
  * an old (no-``source``) ingest snapshot replaying as ``source="email"``.
"""

from datetime import datetime, timezone

import pytest

from digest_core import run as runner
from digest_core.config import Config
from digest_core.evidence.split import EvidenceSplitter
from digest_core.ingest.ews import NormalizedMessage
from digest_core.threads.build import ThreadBuilder

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

_MM_BODY_WITH_QUOTE = (
    "> earlier: can someone own the release tonight?\n"
    "@me yes I'll own it — please review the **PR** before 15:00"
)

# A long-ish chat CTA so the evidence splitter clears its min-tokens-per-chunk
# floor (~64 tokens); short one-liners would be dropped before a chunk is made.
_MM_LONG_CTA = (
    "@me please review the release checklist and confirm the **deployment** plan "
    "before the 15:00 cutoff today. We still need sign-off on the database "
    "migration step and the rollback procedure. Let me know if anything is unclear "
    "or if you want me to walk through the staging results first; otherwise I will "
    "proceed and post the final go/no-go decision in this channel shortly after."
)

_EMAIL_LONG_BODY = (
    "Please send the quarterly report by Friday so we can review it before the "
    "board meeting. Include the regional breakdown and the variance commentary "
    "against the forecast. If the finance team has not yet closed the numbers, "
    "let me know today and we will push the deadline; otherwise the current "
    "figures will be treated as final for the upcoming presentation slides."
)


def _mm_msg(
    *,
    post_id: str,
    root_id: str,
    body: str,
    channel: str = "release-train",
    when: datetime | None = None,
) -> NormalizedMessage:
    """A synthetic Mattermost-sourced message (offline, no network).

    Mirrors the P1b field map: namespaced ``mm:<post_id>`` id, ``conversation_id``
    = root_id (native threading), synthesized channel-name subject, raw markdown
    body, ``source="mm"``.
    """
    return NormalizedMessage(
        msg_id=f"mm:{post_id}",
        conversation_id=root_id,
        subject=channel,  # synthesized channel display name
        text_body=body,
        sender_email="author@corp",
        datetime_received=when or datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc),
        to_recipients=[],
        cc_recipients=[],
        source="mm",
    )


def _email_msg(msg_id: str, subject: str, body: str) -> NormalizedMessage:
    return NormalizedMessage(
        msg_id=msg_id,
        conversation_id=None,
        subject=subject,
        text_body=body,
        sender_email="boss@corp",
        datetime_received=datetime(2026, 3, 29, 9, 0, tzinfo=timezone.utc),
        to_recipients=["me@corp"],
        cc_recipients=[],
    )


# ---------------------------------------------------------------------------
# 1. source field + content-hash invariants
# ---------------------------------------------------------------------------


def test_source_field_defaults_to_email():
    """The default source TYPE is "email"; EWS never sets it."""
    msg = NormalizedMessage(
        msg_id="<x@corp>",
        conversation_id="c1",
        subject="s",
        text_body="b",
    )
    assert msg.source == "email"


def test_source_field_settable_to_mm():
    msg = _mm_msg(post_id="p1", root_id="r1", body="hi")
    assert msg.source == "mm"
    assert msg.msg_id == "mm:p1"


def test_source_not_in_content_sha256():
    """Adding `source` must NOT change existing email content hashes (idempotency).

    Two messages identical in msg_id|subject|text_body but differing only in
    `source` must hash to the same content sha — `_content_sha256` hashes only
    those three fields.
    """
    as_email = NormalizedMessage(msg_id="m1", conversation_id="c", subject="Subj", text_body="Body")
    as_mm = NormalizedMessage(
        msg_id="m1", conversation_id="c", subject="Subj", text_body="Body", source="mm"
    )
    assert runner._content_sha256([as_email]) == runner._content_sha256([as_mm])


def test_old_snapshot_without_source_replays_as_email(tmp_path):
    """An old --dump-ingest snapshot (no `source` key) replays as source="email"."""
    snapshot = tmp_path / "old-ingest.json"
    # Hand-write a legacy-format snapshot with NO `source` key on the message.
    legacy_message = {
        "msg_id": "<legacy@corp>",
        "conversation_id": "c-legacy",
        "datetime_received": "2026-03-29T09:00:00+00:00",
        "sender_email": "boss@corp",
        "subject": "Legacy",
        "text_body": "legacy body",
        "to_recipients": ["me@corp"],
        "cc_recipients": [],
        "importance": "Normal",
        "is_flagged": False,
        "has_attachments": False,
        "attachment_types": [],
    }
    import json

    snapshot.write_text(
        json.dumps({"meta": {"source": "ews"}, "messages": [legacy_message]}),
        encoding="utf-8",
    )

    messages = runner._load_ingest_snapshot(snapshot)
    assert len(messages) == 1
    assert messages[0].source == "email"
    assert messages[0].msg_id == "<legacy@corp>"


def test_dump_replay_roundtrip_preserves_mm_source(tmp_path):
    """A new snapshot carries `source` and replays it back faithfully."""
    import json

    snapshot = tmp_path / "mm-ingest.json"
    msgs = [_mm_msg(post_id="p1", root_id="r1", body="hi"), _email_msg("m2", "S", "b")]
    runner._dump_ingest_snapshot(snapshot, msgs, "2026-03-29")

    # `source` is present on disk for the mm message (asdict serialization).
    on_disk = json.loads(snapshot.read_text(encoding="utf-8"))["messages"]
    by_id = {m["msg_id"]: m for m in on_disk}
    assert by_id["mm:p1"]["source"] == "mm"
    assert by_id["m2"]["source"] == "email"

    replayed = runner._load_ingest_snapshot(snapshot)
    replayed_by_id = {m.msg_id: m for m in replayed}
    assert replayed_by_id["mm:p1"].source == "mm"
    assert replayed_by_id["m2"].source == "email"


# ---------------------------------------------------------------------------
# 2. markdown-safe normalize branch
# ---------------------------------------------------------------------------


def test_mm_body_not_truncated_at_quote_line():
    """(a) The quote-cleaner bypass: a leading `>` line must NOT truncate the body.

    On the email path, `QuoteCleaner._remove_quotes_with_spans` deletes everything
    from the first `>` line. The mm branch must skip it so the markdown survives.
    """
    config = Config()
    assert config.email_cleaner.enabled is True  # the truncating path is active

    msg = _mm_msg(post_id="p1", root_id="r1", body=_MM_BODY_WITH_QUOTE)
    normalized = runner._normalize_messages([msg], config)

    body = normalized[0].text_body
    # The actionable line below the `>` quote must survive.
    assert "I'll own it" in body
    assert "review the **PR** before 15:00" in body
    # The markdown `**` was NOT stripped to plain text (no html_to_text).
    assert "**PR**" in body
    assert normalized[0].source == "mm"


def test_email_normalize_path_byte_identical():
    """The email path must be byte-identical with the mm branch present.

    The mm branch is a conditional, not a rewrite, so an email message must
    normalize exactly as it would WITHOUT the branch: html_to_text + truncate +
    clean_email_body. We reproduce that reference output inline and assert the
    pipeline output equals it byte-for-byte.
    """
    from digest_core.normalize.html import HTMLNormalizer
    from digest_core.normalize.quotes import QuoteCleaner

    config = Config()
    email_body = (
        "Please send the report.\nThanks for the quick turnaround.\n"
        "> on Mon, boss wrote:\n> please prepare the slides for the review"
    )
    msg = _email_msg("m1", "Report", email_body)

    # Reference: the exact pre-branch email normalization.
    normalizer = HTMLNormalizer()
    quote_cleaner = QuoteCleaner(
        keep_top_quote_head=config.email_cleaner.keep_top_quote_head,
        config=config.email_cleaner,
    )
    ref_text, _ = normalizer.html_to_text(email_body)
    ref_text = normalizer.truncate_text(ref_text, max_bytes=200000)
    ref_clean, _ = quote_cleaner.clean_email_body(ref_text, lang="auto", policy="standard")

    normalized = runner._normalize_messages([msg], config)
    assert normalized[0].text_body == ref_clean
    assert normalized[0].body_norm == ref_clean
    assert normalized[0].source == "email"


# ---------------------------------------------------------------------------
# 3. source-aware threading branch
# ---------------------------------------------------------------------------


# Two distinct conversations sharing the synthesized "release-train" subject,
# with short bodies similar enough (~0.74) to cross the semantic-merge floor.
_BODY_A = "please review the release checklist when you can today"
_BODY_B = "please review the deployment checklist when you can today"


def test_email_control_semantic_merge_wrongly_fuses_distinct_convs():
    """Control: on the EMAIL path, the semantic-merge step DOES fuse two distinct
    conversation_id roots that share a subject + similar bodies.

    This is the exact failure mode the mm source-branch must avoid — proving the
    branch is load-bearing (not a no-op) by showing the un-branched email behavior.
    """
    builder = ThreadBuilder()
    a = NormalizedMessage(
        msg_id="e1",
        conversation_id="rootA",
        subject="release-train",
        text_body=_BODY_A,
        sender_email="a@corp",
        datetime_received=datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc),
        to_recipients=[],
        cc_recipients=[],
    )
    b = NormalizedMessage(
        msg_id="e2",
        conversation_id="rootB",
        subject="release-train",
        text_body=_BODY_B,
        sender_email="a@corp",
        datetime_received=datetime(2026, 3, 29, 12, 1, tzinfo=timezone.utc),
        to_recipients=[],
        cc_recipients=[],
    )
    threads = builder.build_threads([a, b])
    assert len(threads) == 1  # wrongly merged
    assert builder.stats["threads_merged_by_semantic"] == 1


def test_two_mm_roots_not_merged_by_synthesized_subject():
    """(b) Two distinct mm roots in the same channel must NOT merge.

    Same input shape as the email control (distinct conversation_id, identical
    synthesized subject, ~0.74-similar bodies) but with source="mm". The
    source-aware branch holds mm groups out of the semantic-merge step, so they
    stay two threads keyed purely by conversation_id.
    """
    builder = ThreadBuilder()
    msgs = [
        _mm_msg(post_id="a1", root_id="rootA", body=_BODY_A, channel="release-train"),
        _mm_msg(
            post_id="b1",
            root_id="rootB",
            body=_BODY_B,
            channel="release-train",
            when=datetime(2026, 3, 29, 12, 1, tzinfo=timezone.utc),
        ),
    ]
    threads = builder.build_threads(msgs)

    # Two distinct conversation roots → exactly two threads (NOT fused).
    assert len(threads) == 2
    assert builder.stats["threads_merged_by_semantic"] == 0
    convs = {t.conversation_id for t in threads}
    assert convs == {"conv_rootA", "conv_rootB"}


def test_email_threading_semantic_merge_still_runs():
    """Email path unchanged: subjectless, similar-bodied email threads still merge.

    Two emails with no conversation_id but identical subject + near-identical body
    must still be subject/semantic-merged into one thread (the email behavior the
    source-aware branch must not disturb).
    """
    builder = ThreadBuilder()
    shared_body = "Please approve the budget for the Q2 marketing campaign today."
    msgs = [
        _email_msg("e1", "Budget approval", shared_body),
        _email_msg("e2", "Budget approval", shared_body + " Thanks."),
    ]
    threads = builder.build_threads(msgs)
    # Same normalized subject → grouped into a single subject thread.
    assert len(threads) == 1


def test_mixed_email_and_mm_threads_coexist():
    """A mixed batch threads each source on its own axis."""
    builder = ThreadBuilder()
    msgs = [
        _email_msg("e1", "Status update", "All green for the launch."),
        _mm_msg(post_id="p1", root_id="rootC", body="@me can you confirm?"),
    ]
    threads = builder.build_threads(msgs)
    assert len(threads) == 2
    convs = {t.conversation_id for t in threads}
    # mm thread keyed off conversation_id (Strategy 1 → conv_ prefix).
    assert any(c == "conv_rootC" for c in convs)


# ---------------------------------------------------------------------------
# 4. authoritative source_ref.type (+ mm locators)
# ---------------------------------------------------------------------------


def _split_one(msg: NormalizedMessage):
    builder = ThreadBuilder()
    threads = builder.build_threads([msg])
    splitter = EvidenceSplitter(user_aliases=["me@corp"], user_timezone="UTC")
    return splitter.split_evidence(threads)


def test_mm_source_ref_type_is_mm():
    """(c) source_ref['type'] == 'mm' for an mm message, with chat locators."""
    msg = _mm_msg(post_id="postXYZ", root_id="rootZ", body=_MM_LONG_CTA)
    chunks = _split_one(msg)
    assert chunks, "expected at least one evidence chunk"
    ref = chunks[0].source_ref
    assert ref["type"] == "mm"
    # mm locators surfaced for downstream EP-15 / linking.
    assert ref["channel_id"] == "conv_rootZ" or ref["channel_id"] == "rootZ"
    assert ref["post_id"] == "postXYZ"
    # (d) mm: namespacing preserved on the chunk's msg_id.
    assert ref["msg_id"] == "mm:postXYZ"
    assert chunks[0].msg_id == "mm:postXYZ"


def test_email_source_ref_type_unchanged():
    """Email source_ref stays {"type": "email", ...} byte-identical (no mm keys)."""
    msg = _email_msg("e1", "Hello", _EMAIL_LONG_BODY)
    chunks = _split_one(msg)
    assert chunks
    ref = chunks[0].source_ref
    assert ref["type"] == "email"
    assert "channel_id" not in ref
    assert "post_id" not in ref


def test_gateway_overwrites_echoed_source_type_from_chunk():
    """The LLM's echoed source_ref['type'] is overwritten from the cited chunk.

    Server-driven source typing: even if the model echoes the wrong type, the
    validated item carries the authoritative type of the chunk it cited.
    """
    from digest_core.evidence.split import EvidenceChunk
    from digest_core.llm.gateway import LLMGateway
    from digest_core.config import LLMConfig

    chunk = EvidenceChunk(
        evidence_id="ev_mm_1",
        conversation_id="conv_rootZ",
        content="@me please review the PR",
        source_ref={"type": "mm", "msg_id": "mm:postXYZ", "post_id": "postXYZ"},
    )
    gw = LLMGateway(LLMConfig())
    # The model wrongly echoes type="email"; the gateway must correct it.
    item = {
        "title": "Review the PR",
        "evidence_id": "ev_mm_1",
        "confidence": 0.9,
        "source_ref": {"type": "email", "msg_id": "mm:postXYZ"},
    }
    validated = gw._validate_item(item, [chunk])
    assert validated is not None
    assert validated["source_ref"]["type"] == "mm"


# ---------------------------------------------------------------------------
# 5. --sources selector wiring
# ---------------------------------------------------------------------------


def test_build_source_adapters_default_ews_strict():
    """Default ["ews"] → exactly one strict EWSSourceAdapter, no lenient adapters."""
    ingest = object()  # adapter only stores the reference; not called here
    strict, lenient = runner._build_source_adapters(["ews"], ingest)
    assert [a.name for a in strict] == ["ews"]
    assert lenient == []


def test_build_source_adapters_email_alias():
    """ "email" is an alias of the EWS source TYPE; it selects the EWS adapter."""
    strict, lenient = runner._build_source_adapters(["email"], object())
    assert [a.name for a in strict] == ["ews"]
    assert lenient == []


def test_build_source_adapters_dedups_ews():
    """Asking for both "ews" and "email" still yields a single EWS adapter."""
    strict, lenient = runner._build_source_adapters(["ews", "email"], object())
    assert [a.name for a in strict] == ["ews"]


def test_build_source_adapters_unknown_source_errors():
    """An unknown source name is a hard error, never silently ignored."""
    with pytest.raises(ValueError, match="Unknown ingest source"):
        runner._build_source_adapters(["telegram"], object())


def test_build_source_adapters_mm_unconfigured_errors():
    """Selecting "mm" while unconfigured (no base_url / MM_PAT) fails loudly (P1b).

    P1b builds a real MM adapter, but only when configured. With a default
    Config (no base_url, MM_PAT unset) the build must raise an actionable error
    rather than degrade to a silent empty digest.
    """
    cfg = Config()
    with pytest.raises(ValueError, match="Mattermost source selected"):
        runner._build_source_adapters(["mm"], object(), cfg)
