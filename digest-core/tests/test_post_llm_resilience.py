"""A paid extraction must survive a broken enrichment pass.

`_guard` covers only the four stages BEFORE the LLM — the cheap, re-runnable
ones. Everything after the call ran unprotected, and the digest first touched
disk at ASSEMBLE, so a crash in quarantine / dedup / enrichment / meetings /
carryover discarded a corp gateway call that cannot be retaken on a capture run.

These tests break each pass on purpose and assert the run still produces a
digest, that the failure is recorded rather than swallowed, and that the raw
extraction is on disk either way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from digest_core import run as runner
from tests.test_progress_sink import _run_replay

ENRICHMENT_PASSES = [
    ("_post_llm_digest_enrichment", "citations_and_gate"),
    ("_apply_dedup_ledger", "dedup_ledger"),
    ("_enrich_items_from_messages", "source_enrichment"),
    ("_enrich_digest_with_meetings", "meetings_section"),
    ("_enrich_digest_from_store", "store_carryover"),
]


def _digest_artifact(tmp_path: Path) -> dict:
    matches = list((tmp_path / "out").glob("digest-????-??-??.json"))
    assert len(matches) == 1, f"expected exactly one digest artifact, got {matches}"
    return json.loads(matches[0].read_text(encoding="utf-8"))


def _raw_artifact(tmp_path: Path) -> dict:
    matches = list((tmp_path / "out").glob("digest-*.raw.json"))
    assert len(matches) == 1, f"expected exactly one raw artifact, got {matches}"
    return json.loads(matches[0].read_text(encoding="utf-8"))


class TestRawDigestPersisted:
    def test_raw_extraction_is_written_before_enrichment(self, monkeypatch, tmp_path):
        assert _run_replay(monkeypatch, tmp_path, None)
        raw = _raw_artifact(tmp_path)
        assert raw["sections"], "the raw extraction should carry the model's items"

    def test_raw_artifact_is_invisible_to_the_reader(self, monkeypatch, tmp_path):
        """It must not read as a digest — the sibling-glob trap from #212."""
        from digest_core.ui.reader import list_digests

        assert _run_replay(monkeypatch, tmp_path, None)
        names = [p.name for p in list_digests(tmp_path / "out")]
        assert not any(".raw.json" in n for n in names), names

    def test_raw_artifact_is_still_covered_by_retention(self):
        """A new artifact class that retention misses would accumulate forever."""
        import fnmatch

        from digest_core.maintenance import RETENTION_GLOBS

        assert any(
            fnmatch.fnmatch("digest-2026-03-29.raw.json", pattern) for pattern in RETENTION_GLOBS
        )

    def test_a_failing_raw_write_does_not_break_the_run(self, monkeypatch, tmp_path):
        """The safety net must never become the failure it exists to prevent."""
        real_write = runner._write_json

        def explode(path, payload):
            if str(path).endswith(".raw.json"):
                raise OSError("disk full")
            return real_write(path, payload)

        monkeypatch.setattr(runner, "_write_json", explode)
        assert _run_replay(monkeypatch, tmp_path, None)
        assert _digest_artifact(tmp_path)["sections"]


class TestEnrichmentFailuresAreSkipped:
    @pytest.mark.parametrize("attr,pass_name", ENRICHMENT_PASSES)
    def test_a_broken_pass_does_not_cost_the_extraction(
        self, monkeypatch, tmp_path, attr, pass_name
    ):
        def boom(*args, **kwargs):
            raise RuntimeError(f"{attr} is broken")

        monkeypatch.setattr(runner, attr, boom)
        result = _run_replay(monkeypatch, tmp_path, None)

        assert result, f"a broken {attr} killed the run"
        # the digest still reached disk...
        artifact = _digest_artifact(tmp_path)
        assert artifact["sections"], "items were lost to a failed enrichment pass"
        # ...and the raw extraction is recoverable regardless
        assert _raw_artifact(tmp_path)["sections"]

    @pytest.mark.parametrize("attr,pass_name", ENRICHMENT_PASSES)
    def test_the_skip_is_recorded_not_swallowed(self, monkeypatch, tmp_path, attr, pass_name):
        """No silent caps: a section quietly missing must be visible in run_meta."""

        def boom(*args, **kwargs):
            raise RuntimeError("nope")

        monkeypatch.setattr(runner, attr, boom)
        assert _run_replay(monkeypatch, tmp_path, None)

        meta_files = list((tmp_path / "out").glob("trace-*.meta.json"))
        assert meta_files, "no run metadata written"
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        skipped = {entry["pass"] for entry in meta.get("enrichment_skipped", [])}
        assert pass_name in skipped, f"{pass_name} skip not recorded; got {skipped}"

    def test_a_skipped_gate_never_claims_validation_succeeded(self, monkeypatch, tmp_path):
        """If the citation gate crashed it validated nothing — it must not report ok."""

        def boom(*args, **kwargs):
            raise RuntimeError("gate exploded")

        monkeypatch.setattr(runner, "_post_llm_digest_enrichment", boom)
        assert _run_replay(monkeypatch, tmp_path, None)

        meta = json.loads(
            next((tmp_path / "out").glob("trace-*.meta.json")).read_text(encoding="utf-8")
        )
        # _run_replay does not pass --validate-citations, so ok stays True by the
        # same rule the degraded path uses; the gate's own numbers must be zeroed.
        assert meta["support_recall"] == 0.0
        assert meta["items_weak"] == 0 and meta["items_repaired"] == 0

    def test_healthy_run_records_no_skips(self, monkeypatch, tmp_path):
        assert _run_replay(monkeypatch, tmp_path, None)
        meta = json.loads(
            next((tmp_path / "out").glob("trace-*.meta.json")).read_text(encoding="utf-8")
        )
        assert "enrichment_skipped" not in meta
