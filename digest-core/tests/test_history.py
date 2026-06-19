"""Cross-digest history search (history.search_history). Pure — no store extra needed."""

from __future__ import annotations

from digest_core.assemble.labels import section_title
from digest_core.history import search_history
from digest_core.llm.schemas import Digest, Item, Section


def _item(title: str) -> Item:
    return Item(title=title, evidence_id="ev", confidence=0.9, source_ref={"msg_id": "m"})


def _write_digest(out_dir, date: str, sections):
    digest = Digest(prompt_version="x", digest_date=date, trace_id="t", sections=sections)
    (out_dir / f"digest-{date}.json").write_text(digest.model_dump_json(), encoding="utf-8")


def _seed(tmp_path):
    _write_digest(
        tmp_path,
        "2026-06-10",
        [
            Section(
                title=section_title("my_actions", "en"),
                items=[_item("Approve the budget"), _item("Ship the release")],
            )
        ],
    )
    _write_digest(
        tmp_path,
        "2026-06-12",
        [Section(title=section_title("urgent", "en"), items=[_item("Budget freeze announced")])],
    )


def test_query_filters_across_days_newest_first(tmp_path):
    _seed(tmp_path)
    hits = search_history(tmp_path, "budget")
    assert [h.digest_date for h in hits] == ["2026-06-12", "2026-06-10"]  # newest first
    assert all("budget" in h.item.title.lower() for h in hits)


def test_date_range(tmp_path):
    _seed(tmp_path)
    assert {h.digest_date for h in search_history(tmp_path, "budget", since="2026-06-11")} == {
        "2026-06-12"
    }
    assert {h.digest_date for h in search_history(tmp_path, "budget", until="2026-06-11")} == {
        "2026-06-10"
    }


def test_section_filter_by_canonical_key(tmp_path):
    _seed(tmp_path)
    hits = search_history(tmp_path, section="urgent")
    assert hits and all(h.section_key == "urgent" for h in hits)
    assert {h.item.title for h in hits} == {"Budget freeze announced"}


def test_no_query_lists_all_and_limit(tmp_path):
    _seed(tmp_path)
    assert len(search_history(tmp_path)) == 3  # all items across both digests
    assert len(search_history(tmp_path, limit=1)) == 1  # newest first → most recent kept


def test_empty_or_missing_dir(tmp_path):
    assert search_history(tmp_path) == []  # no artifacts
    assert search_history(tmp_path / "nope") == []  # missing dir
