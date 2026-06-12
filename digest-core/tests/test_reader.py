"""Digest reader (U4): enrichment, pure helpers, drill-down flow, pty drive.

The reader is the §5.1 posture test case — line-oriented drill-down, cards
printed into scrollback (never an alt screen). Tests cover the data side
(assemble-time subject/author enrichment riding the artifact), the pure
builders, the scripted interactive flow, and a real-pty keypath.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from rich.console import Console

from digest_core import run as runner
from digest_core.llm.schemas import Digest, Item, Section
from digest_core.ui import THEME
from digest_core.ui import reader as reader_mod
from digest_core.ui.reader import (
    item_card,
    list_digests,
    load_digest,
    read_digest_interactive,
    render_digest_plain,
)

from tests.test_menu_pty import _read_until
from tests.test_progress_sink import _run_replay


def _console() -> Console:
    return Console(record=True, width=100, force_terminal=False, theme=THEME)


def _item(**overrides) -> Item:
    payload = dict(
        title="Prepare the project status update",
        due="2026-06-13",
        evidence_id="ev-1",
        confidence=0.86,
        source_ref={"type": "email", "msg_id": "msg-1"},
        email_subject="RE: Project status",
        email_from="Ivan Petrov <ivan.petrov@corp.ru>",
        evidence_spans=[{"msg_id": "msg-1", "quote": "подготовь обновление статуса"}],
    )
    payload.update(overrides)
    return Item(**payload)


def _digest(date: str = "2026-06-12", items: list[Item] | None = None) -> Digest:
    return Digest(
        prompt_version="extract_actions.en.v2",
        digest_date=date,
        trace_id="t-1",
        sections=[Section(title="My actions", items=items or [_item()])],
    )


def _write_digest(out_dir: Path, digest: Digest) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"digest-{digest.digest_date}.json"
    path.write_text(
        json.dumps(digest.model_dump(exclude_none=True), ensure_ascii=False), encoding="utf-8"
    )
    return path


class TestEnrichment:
    """run.py populates email_subject/email_from from the normalized messages."""

    def test_replay_run_writes_enriched_artifact(self, monkeypatch, tmp_path):
        assert _run_replay(monkeypatch, tmp_path, None)
        artifact = json.loads(
            next((tmp_path / "out").glob("digest-*.json")).read_text(encoding="utf-8")
        )
        items = [item for section in artifact["sections"] for item in section["items"]]
        assert items
        for item in items:
            assert item["email_subject"] == "Статус проекта"
            assert item["email_from"] == "Manager <manager@corp.com>"

    def test_existing_values_kept_and_system_items_skipped(self):
        from digest_core.ingest.ews import NormalizedMessage
        from datetime import datetime, timezone

        message = NormalizedMessage(
            msg_id="msg-1",
            conversation_id="c-1",
            datetime_received=datetime.now(timezone.utc),
            sender_email="a@corp.ru",
            subject="Subject A",
            text_body="x",
            to_recipients=[],
            cc_recipients=[],
            importance="Normal",
            is_flagged=False,
            has_attachments=False,
            attachment_types=[],
            from_name="Anna",
        )
        keep = _item(email_subject="Already set", email_from=None)
        system = Item(
            title="banner",
            evidence_id="system",
            confidence=0.0,
            source_ref={"type": "system"},
        )
        digest = _digest(items=[keep, system])
        runner._enrich_items_from_messages(digest, [message])
        assert keep.email_subject == "Already set"  # never overwritten
        assert keep.email_from == "Anna <a@corp.ru>"
        assert system.email_subject is None  # system items carry no source

    def test_msg_id_falls_back_to_spans(self):
        item = _item(source_ref={"type": "email"})
        assert runner._item_msg_id(item) == "msg-1"


class TestPureHelpers:
    def test_list_digests_newest_first(self, tmp_path):
        for date in ("2026-06-10", "2026-06-12", "2026-06-11"):
            _write_digest(tmp_path, _digest(date))
        names = [p.name for p in list_digests(tmp_path)]
        assert names == [
            "digest-2026-06-12.json",
            "digest-2026-06-11.json",
            "digest-2026-06-10.json",
        ]

    def test_load_tolerates_pre_enrichment_artifacts(self, tmp_path):
        digest = _digest()
        payload = digest.model_dump(exclude_none=True)
        for section in payload["sections"]:
            for item in section["items"]:
                item.pop("email_from", None)
                item.pop("email_subject", None)
        path = tmp_path / "digest-2026-06-12.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        loaded = load_digest(path)
        assert loaded.sections[0].items[0].email_from is None

    def test_item_card_carries_topic_author_quote_and_trace(self):
        console = _console()
        console.print(item_card(_item(weak_evidence=True), "My actions"))
        text = console.export_text()
        assert "Prepare the project status update" in text
        assert "Ivan Petrov <ivan.petrov@corp.ru>" in text
        assert "RE: Project status" in text
        assert "2026-06-13" in text
        assert "0.86 (high)" in text
        assert "подготовь обновление статуса" in text  # quote stays source-language
        assert "evidence ev-1" in text and "msg msg-1" in text  # P2 trace
        assert "weak" in text

    def test_paged_choose_holds_the_nine_option_invariant(self, monkeypatch):
        seen = []
        answers = iter(["__next__", "__prev__", "e3"])

        def fake_choose(label, options, default_index=0, console=None, cancel_value=None):
            seen.append([v for v, _ in options])
            return next(answers)

        monkeypatch.setattr(reader_mod, "choose", fake_choose)
        entries = [(f"e{i}", f"entry {i}") for i in range(17)]
        value = reader_mod._paged_choose(_console(), "Items", entries)
        assert value == "e3"
        assert all(len(options) <= 9 for options in seen)
        assert "__next__" in seen[0] and "__prev__" not in seen[0]
        assert "__prev__" in seen[1]  # page 2 offers the way back


class TestInteractiveFlow:
    def test_drill_down_prints_card_and_walks_back(self, monkeypatch, tmp_path):
        _write_digest(tmp_path, _digest())
        answers = iter(["0", "0", None, None])  # section -> item -> back -> back

        def fake_paged(console, label, entries, back_label="Back"):
            return next(answers)

        monkeypatch.setattr(reader_mod, "_paged_choose", fake_paged)
        console = _console()
        assert read_digest_interactive(tmp_path, console=console) == 0
        text = console.export_text()
        assert "Digest 2026-06-12" in text
        assert "Ivan Petrov" in text  # the card landed in scrollback

    def test_missing_date_is_a_friendly_error(self, tmp_path):
        console = _console()
        assert read_digest_interactive(tmp_path, date="2026-01-01", console=console) == 1
        assert "No digest for 2026-01-01" in console.export_text()

    def test_empty_dir_is_a_friendly_error(self, tmp_path):
        console = _console()
        assert read_digest_interactive(tmp_path, console=console) == 1
        assert "No digests found" in console.export_text()


class TestPlainDegradation:
    def test_prefers_markdown_sibling(self, tmp_path):
        _write_digest(tmp_path, _digest())
        (tmp_path / "digest-2026-06-12.md").write_text("# the md render", encoding="utf-8")
        assert render_digest_plain(tmp_path) == "# the md render"

    def test_minimal_render_without_md(self, tmp_path):
        _write_digest(tmp_path, _digest())
        text = render_digest_plain(tmp_path)
        assert "Digest 2026-06-12" in text
        assert "My actions" in text
        assert "Prepare the project status update" in text

    def test_nothing_found_returns_none(self, tmp_path):
        assert render_digest_plain(tmp_path) is None


READER_DRIVER = """
import json, sys
from pathlib import Path
from rich.console import Console
from digest_core.ui import THEME
from digest_core.ui.reader import read_digest_interactive

console = Console(theme=THEME, force_terminal=True, width=90)
code = read_digest_interactive(Path(sys.argv[1]), console=console)
print("RESULT:" + str(code), flush=True)
"""


class TestReaderPty:
    def _drive(self, out_dir: Path, keys: bytes) -> tuple[str, int]:
        import subprocess
        import sys as _sys

        from tests.test_menu_pty import DIGEST_CORE
        import pty as _pty

        master, slave = _pty.openpty()
        proc = subprocess.Popen(
            [_sys.executable, "-c", READER_DRIVER, str(out_dir)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            cwd=str(DIGEST_CORE),
            env={**os.environ, "PYTHONPATH": "src"},
        )
        os.close(slave)
        try:
            _read_until(master, proc, "Sections")
            os.write(master, keys)
            out = _read_until(master, proc, "RESULT:")
            proc.wait(timeout=10)
            return out, proc.returncode
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            os.close(master)

    def test_drill_down_to_card_and_esc_out(self, tmp_path):
        _write_digest(tmp_path, _digest())
        # 1 = the only section; 1 = the only item (card prints). Then back out:
        # two adjacent Esc bytes collapse into ONE "esc" (the arrow-tail
        # lookahead consumes the second), so three bytes = two Esc presses.
        out, code = self._drive(tmp_path, b"11\x1b\x1b\x1b")
        assert code == 0
        assert "RESULT:0" in out
        assert "Ivan Petrov" in out  # the card rendered through a real tty
        assert "RE: Project status" in out


class TestReadCommandNonTty:
    def test_prints_markdown_when_piped(self, tmp_path):
        from typer.testing import CliRunner

        from digest_core.cli import app

        _write_digest(tmp_path, _digest())
        (tmp_path / "digest-2026-06-12.md").write_text("# the md render", encoding="utf-8")
        result = CliRunner().invoke(app, ["read", "--out", str(tmp_path)])
        assert result.exit_code == 0
        assert "# the md render" in result.output

    def test_empty_dir_exits_1(self, tmp_path):
        from typer.testing import CliRunner

        from digest_core.cli import app

        result = CliRunner().invoke(app, ["read", "--out", str(tmp_path)])
        assert result.exit_code == 1
