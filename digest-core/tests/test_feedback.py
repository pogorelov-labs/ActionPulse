"""Reactions flywheel scaffold (EP-15): delivered-posts ledger + reaction harvest.

Driver-independent (no store extra needed): pure ledger IO + harvest logic + the
non-fatal run.py delivery wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from digest_core import run as runner
from digest_core.config import Config
from digest_core.feedback.delivered_ledger import DeliveredPost, read_ledger, record_delivery
from digest_core.feedback.reactions import classify, harvest_reactions, summarize, to_gold_rows

# --------------------------------------------------------------------------- #
# delivered_ledger
# --------------------------------------------------------------------------- #


def test_record_and_read_ledger_roundtrip(tmp_path):
    n = record_delivery(
        tmp_path,
        post_ids=["p1", "p2"],
        evidence_ids=["ev_a", "ev_b", "system"],  # 'system' is dropped
        channel_id="chan-1",
        digest_date="2026-06-01",
        trace_id="trace-xyz",
        now=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
    )
    assert n == 2
    entries = read_ledger(tmp_path)
    assert [e.post_id for e in entries] == ["p1", "p2"]
    assert entries[0].evidence_ids == ["ev_a", "ev_b"]  # system filtered out
    assert entries[0].channel_id == "chan-1" and entries[0].digest_date == "2026-06-01"
    assert entries[0].trace_id == "trace-xyz"  # persisted for gold-row keying


def test_ledger_no_post_ids_is_noop(tmp_path):
    assert record_delivery(tmp_path, post_ids=[], evidence_ids=["ev_a"]) == 0
    assert read_ledger(tmp_path) == []


def test_ledger_date_filter_and_append(tmp_path):
    record_delivery(tmp_path, post_ids=["p1"], evidence_ids=["ev_a"], digest_date="2026-06-01")
    record_delivery(tmp_path, post_ids=["p2"], evidence_ids=["ev_b"], digest_date="2026-06-02")
    assert {e.post_id for e in read_ledger(tmp_path)} == {"p1", "p2"}  # appended
    only = read_ledger(tmp_path, digest_date="2026-06-02")
    assert [e.post_id for e in only] == ["p2"]


# --------------------------------------------------------------------------- #
# reactions harvest
# --------------------------------------------------------------------------- #


class _FakeClient:
    def __init__(self, by_post):
        self._by_post = by_post

    def get_post_reactions(self, post_id):
        value = self._by_post.get(post_id)
        if isinstance(value, Exception):
            raise value
        return value or []


def test_classify_ack_nack_other():
    assert classify("white_check_mark") == "ack"
    assert classify(":+1:") == "ack"  # colons + alias
    assert classify("X") == "nack"  # case-insensitive
    assert classify("tada") == "other"


def test_harvest_and_summarize_folds_onto_evidence():
    entries = [
        DeliveredPost("p1", "c", "2026-06-01", ["ev_1", "ev_2"]),
        DeliveredPost("p2", "c", "2026-06-01", ["ev_3"]),
    ]
    client = _FakeClient(
        {
            "p1": [
                {"emoji_name": "white_check_mark", "user_id": "u1"},
                {"emoji_name": "x", "user_id": "u2"},
            ],
            "p2": [{"emoji_name": "tada", "user_id": "u3"}],
        }
    )
    records = harvest_reactions(client, entries)
    assert len(records) == 3
    s = summarize(records)
    assert s["totals"] == {"ack": 1, "nack": 1, "other": 1}
    # The post-1 ack and nack each fold onto BOTH its evidence ids (coarse mapping).
    assert s["by_evidence"]["ev_1"] == {"ack": 1, "nack": 1}
    assert s["by_evidence"]["ev_2"] == {"ack": 1, "nack": 1}
    assert "ev_3" not in s["by_evidence"]  # 'other' (tada) contributes no signal


def test_harvest_skips_a_failing_post():
    entries = [
        DeliveredPost("good", "c", "d", ["ev_1"]),
        DeliveredPost("bad", "c", "d", ["ev_2"]),
    ]
    client = _FakeClient(
        {"good": [{"emoji_name": "+1", "user_id": "u"}], "bad": RuntimeError("404")}
    )
    records = harvest_reactions(client, entries)  # must not raise
    assert [r.post_id for r in records] == ["good"]


# --------------------------------------------------------------------------- #
# run.py delivery wiring
# --------------------------------------------------------------------------- #


def test_record_delivered_posts_from_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    ctx = SimpleNamespace(config=Config(), digest_date="2026-06-01", trace_id="t-1")
    digest = SimpleNamespace(
        sections=[
            SimpleNamespace(
                items=[
                    SimpleNamespace(evidence_id="ev_1"),
                    SimpleNamespace(evidence_id="system"),  # dropped
                ]
            )
        ]
    )
    runner._record_delivered_posts(ctx, digest, {"post_ids": ["p1", "p2"], "channel_id": "chan"})
    entries = read_ledger(ctx.config.resolved_state_dir())
    assert [e.post_id for e in entries] == ["p1", "p2"]
    assert entries[0].evidence_ids == ["ev_1"]
    assert entries[0].trace_id == "t-1"  # ctx.trace_id threaded into the ledger


def test_record_delivered_posts_noop_without_post_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    ctx = SimpleNamespace(config=Config(), digest_date="2026-06-01", trace_id="t-1")
    runner._record_delivered_posts(
        ctx, SimpleNamespace(sections=[]), {"status": "sent", "parts": 1}
    )
    assert read_ledger(ctx.config.resolved_state_dir()) == []


# --------------------------------------------------------------------------- #
# harvest → gold bridge (to_gold_rows) + flywheel composition
# --------------------------------------------------------------------------- #


def test_to_gold_rows_emits_per_evidence_rows_skipping_other_and_traceless():
    records = [
        # ack reaction crediting both evidence ids of its post → 2 rows
        SimpleNamespace(
            signal="ack", trace_id="t-1", evidence_ids=["ev_1", "ev_2"], emoji="white_check_mark"
        ),
        # nack with a 'system' id mixed in → only the real id is emitted
        SimpleNamespace(signal="nack", trace_id="t-1", evidence_ids=["system", "ev_3"], emoji="x"),
        # 'other' carries no label → dropped
        SimpleNamespace(signal="other", trace_id="t-1", evidence_ids=["ev_4"], emoji="tada"),
        # no trace_id → cannot be keyed → dropped
        SimpleNamespace(signal="ack", trace_id="", evidence_ids=["ev_5"], emoji="+1"),
    ]
    rows = to_gold_rows(records, lang="en")
    assert rows == [
        {"trace_id": "t-1", "evidence_id": "ev_1", "emoji": "white_check_mark", "lang": "en"},
        {"trace_id": "t-1", "evidence_id": "ev_2", "emoji": "white_check_mark", "lang": "en"},
        {"trace_id": "t-1", "evidence_id": "ev_3", "emoji": "x", "lang": "en"},
    ]


def test_flywheel_harvest_to_calibrate_end_to_end(tmp_path):
    """The whole offline path: ledger → harvest → gold-JSONL → GoldSet → calibrate.

    This is the composition the unit tests don't individually exercise: the bridge
    (``to_gold_rows``) must emit rows that ``load_gold_jsonl`` accepts and labels the
    same way the harvest classified them.
    """
    import json

    from digest_core.eval.calibrate import calibrate_stratum
    from digest_core.eval.gold_set import item_key, load_gold_jsonl

    # 1. a delivered-posts ledger (as api-mode delivery would write it): each post
    #    carries its own distinct evidence id (the per-item ideal post-threading aims at)
    record_delivery(
        tmp_path,
        post_ids=["post-good"],
        evidence_ids=["ev_keep"],
        digest_date="2026-06-01",
        trace_id="t-1",
    )
    record_delivery(
        tmp_path,
        post_ids=["post-bad"],
        evidence_ids=["ev_drop"],
        digest_date="2026-06-01",
        trace_id="t-1",
    )
    entries = read_ledger(tmp_path)

    # 2. harvest reactions (fake corp client): ack on the good post, nack on the bad
    client = _FakeClient(
        {
            "post-good": [{"emoji_name": "white_check_mark", "user_id": "u1"}],
            "post-bad": [{"emoji_name": "x", "user_id": "u2"}],
        }
    )
    records = harvest_reactions(client, entries)

    # 3. bridge → gold JSONL on disk
    gold_path = tmp_path / "gold.jsonl"
    rows = to_gold_rows(records)
    gold_path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    # 4. load the gold set: the ack→True / nack→False labels survive the round-trip
    gold = load_gold_jsonl(gold_path)
    assert gold.label("t-1", item_key("ev_keep", "")) is True
    assert gold.label("t-1", item_key("ev_drop", "")) is False

    # 5. calibrate can consume the resulting (score, gold) pairs without error
    scored = [(0.9, gold.label("t-1", item_key("ev_keep", ""))), (0.2, False)]
    result = calibrate_stratum(scored, target_recall=0.5)
    assert result["usable"] is True


def test_classify_and_gold_emoji_label_agree_on_canonical_vocab():
    """The harvest classifier and the gold labeller share ONE emoji table (no drift).

    A divergence here is the latent calibration bug this unification removes: an emoji
    counted ack/nack by harvest but ignored by the gold set (or vice versa).
    """
    from digest_core.eval.gold_set import _emoji_label
    from digest_core.feedback.reactions import DEFAULT_ACK_EMOJIS, DEFAULT_NACK_EMOJIS

    for emoji in DEFAULT_ACK_EMOJIS:
        assert classify(emoji) == "ack"
        assert _emoji_label(emoji) is True
    for emoji in DEFAULT_NACK_EMOJIS:
        assert classify(emoji) == "nack"
        assert _emoji_label(emoji) is False
    # an unknown emoji is 'other' on both sides
    assert classify("tada") == "other"
    assert _emoji_label("tada") is None
