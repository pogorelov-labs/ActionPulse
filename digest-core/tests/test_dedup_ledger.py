"""Cross-run dedup ledger (EP-7, frontier-audit F8).

Annotate-only memory behind ``memory.dedup_ledger`` (default OFF). The privacy
contract is the load-bearing part: hashed fingerprints only, TTL sweep as the
retention policy, and a strict no-op when the flag is off.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from digest_core import run as runner
from digest_core.memory.ledger import DedupLedger, item_fingerprint
from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def test_fingerprint_is_stable_and_opaque():
    fp = item_fingerprint("ev-abc", "msg-1")
    assert fp == item_fingerprint("ev-abc", "msg-1")
    assert re.fullmatch(r"[0-9a-f]{64}", fp)
    assert fp != item_fingerprint("ev-abc", "msg-2")


def test_ledger_roundtrip_and_seen(tmp_path):
    path = tmp_path / ".state" / "delivered-items.jsonl"
    ledger = DedupLedger(path, ttl_days=14, now=NOW)
    fp = item_fingerprint("ev-1", "m-1")
    assert not ledger.seen(fp)
    ledger.record(fp)
    ledger.save()

    reloaded = DedupLedger(path, ttl_days=14, now=NOW + timedelta(days=1))
    assert reloaded.seen(fp)
    assert len(reloaded) == 1


def test_ttl_sweep_is_the_retention_policy(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = DedupLedger(path, ttl_days=14, now=NOW)
    ledger.record(item_fingerprint("ev-old", "m-old"))
    ledger.save()

    expired = DedupLedger(path, ttl_days=14, now=NOW + timedelta(days=15))
    assert len(expired) == 0
    expired.save()
    assert path.read_text(encoding="utf-8") == ""  # evicted entries are not rewritten


def test_ledger_file_contains_only_hashes(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = DedupLedger(path, ttl_days=14, now=NOW)
    ledger.record(item_fingerprint("ev-секретное-письмо", "msg-ceo@corp"))
    ledger.save()

    raw = path.read_text(encoding="utf-8")
    for line in raw.splitlines():
        entry = json.loads(line)
        assert set(entry) == {"fp", "first_seen", "last_seen"}
        assert re.fullmatch(r"[0-9a-f]{64}", entry["fp"])
    assert "секрет" not in raw and "ceo" not in raw  # identifiers never persist raw


def test_corrupt_ledger_never_breaks_the_run(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    ledger = DedupLedger(path, ttl_days=14, now=NOW)
    assert len(ledger) == 0


def _replay_run(monkeypatch, tmp_path, out_name: str):
    snapshot_path = tmp_path / "snapshot.json"
    if not snapshot_path.exists():
        runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")
    out_dir = tmp_path / out_name

    monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
    monkeypatch.setattr(runner, "start_health_server", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "LLMGateway", FakeGateway)
    monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

    assert runner.run_digest(
        from_date="2026-03-29",
        sources=["ews"],
        out=str(out_dir),
        model="qwen35-397b-a17b",
        window="calendar_day",
        state=str(tmp_path / "state"),
        force=True,
        replay_ingest=str(snapshot_path),
    )
    return json.loads((out_dir / "digest-2026-03-29.json").read_text(encoding="utf-8"))


def test_default_is_on_per_decision_d3():
    from digest_core.config import Config

    assert Config().memory.dedup_ledger is True


def test_explicit_off_writes_nothing(monkeypatch, tmp_path):
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("memory:\n  dedup_ledger: false\n", encoding="utf-8")
    monkeypatch.setenv("DIGEST_CONFIG_PATH", str(config_yaml))
    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    payload = _replay_run(monkeypatch, tmp_path, "out-off")
    assert not (tmp_path / "state" / "delivered-items.jsonl").exists()
    for section in payload["sections"]:
        for item in section["items"]:
            assert "seen_before" not in item  # exclude_none keeps artifacts unchanged


def test_default_on_annotates_second_run(monkeypatch, tmp_path):
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    first = _replay_run(monkeypatch, tmp_path, "out-1")
    assert all(
        "seen_before" not in item for s in first["sections"] for item in s["items"]
    ), "first delivery is new"

    second = _replay_run(monkeypatch, tmp_path, "out-2")
    items = [item for s in second["sections"] for item in s["items"]]
    assert items and all(item.get("seen_before") is True for item in items)

    ledger_raw = (tmp_path / "state" / "delivered-items.jsonl").read_text(encoding="utf-8")
    for line in ledger_raw.splitlines():
        assert re.fullmatch(r"[0-9a-f]{64}", json.loads(line)["fp"])


def test_mm_trace_line_carries_repeat_marker():
    from digest_core.config import Config
    from digest_core.deliver.mattermost import MattermostDeliverer
    from digest_core.llm.schemas import Item

    deliverer = MattermostDeliverer(Config().deliver.mattermost)
    item = Item(
        title="Повторное действие",
        evidence_id="ev-r",
        confidence=0.8,
        source_ref={"type": "email", "msg_id": "m-r"},
        seen_before=True,
    )
    line = deliverer._format_trace_line(item, None)
    # A6/A7: the dedup-repeat marker uses the dedicated "repeat" string,
    # not "repaired" (which is a different concept).
    assert "↻ repeat" in line
    assert "repaired" not in line

    fresh = Item(
        title="Новое действие",
        evidence_id="ev-n",
        confidence=0.8,
        source_ref={"type": "email", "msg_id": "m-n"},
    )
    assert "↻" not in deliverer._format_trace_line(fresh, None)
