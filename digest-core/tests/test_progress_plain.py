"""PlainSink renderer + --progress flag (T3).

The exit criterion from the roadmap: `cli run` output reads like a build log
instead of raw JSON. The render test drives the real pipeline (replay harness
from test_e2e_pipeline) into a recording console and asserts the actual lines.
"""

from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

import digest_core.cli as cli_mod
from digest_core import run as runner
from digest_core.cli import app
from digest_core.progress import NullSink
from digest_core.ui import THEME, PlainSink, resolve_sink
from digest_core.ui.sinks import _fmt_duration, _phrase

from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


def _recording_console() -> Console:
    return Console(theme=THEME, record=True, width=100, force_terminal=False)


class TestFormatters:
    def test_durations(self):
        assert _fmt_duration(312) == "0.3s"
        assert _fmt_duration(3120) == "3.1s"
        assert _fmt_duration(72_000) == "1m12s"

    def test_known_phrases(self):
        assert _phrase({"messages": 124}) == "124 messages"
        assert _phrase({"messages": 119, "threads": 37}) == "119 messages → 37 threads"
        assert _phrase({"threads": 37, "chunks": 41}) == "37 threads → 41 chunks"
        assert _phrase({"selected": 28, "of": 41}) == "28/41 chunks selected"
        assert _phrase({"sections": 3, "items": 7}) == "3 sections · 7 items"
        assert _phrase({"items": 7}) == "7 items"

    def test_fallback_phrase(self):
        assert _phrase({"foo": 1, "bar": 2}) == "foo=1 · bar=2"
        assert _phrase({}) == ""


class TestPlainSinkRendering:
    def test_event_lines(self):
        console = _recording_console()
        sink = PlainSink(console=console)
        sink.on_stage_end("ingest", {"messages": 124}, 3100)
        sink.on_llm_attempt("qwen35-397b-a17b", 1, 2)
        sink.on_stage_failed("threads", "boom")
        sink.on_delivery("mattermost", True)
        sink.on_delivery("mattermost", False, "HTTP 500")
        text = console.export_text()
        assert "✓ INGEST    124 messages (3.1s)" in text
        assert "· llm attempt 1/2 · qwen35-397b-a17b" in text
        assert "✗ THREADS   failed — boom" in text
        assert "✓ delivered → mattermost" in text
        assert "⚠ delivery to mattermost failed — HTTP 500" in text

    def test_full_pipeline_reads_like_a_build_log(self, monkeypatch, tmp_path):
        FakeDeliverer.deliveries.clear()
        snapshot_path = tmp_path / "snapshot.json"
        runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")
        monkeypatch.chdir(Path(__file__).resolve().parents[2])
        monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
        monkeypatch.setattr(runner, "start_health_server", lambda *a, **k: None)
        monkeypatch.setattr(runner, "LLMGateway", FakeGateway)
        monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

        console = _recording_console()
        result = runner.run_digest(
            from_date="2026-03-29",
            sources=["ews"],
            out=str(tmp_path / "out"),
            model="qwen35-397b-a17b",
            window="calendar_day",
            state=str(tmp_path / "state"),
            force=True,
            replay_ingest=str(snapshot_path),
            sink=PlainSink(console=console),
        )
        assert result

        lines = [line for line in console.export_text().splitlines() if line.strip()]
        # One permanent line per stage, in pipeline order — the build-log shape.
        stages = [line.split()[1] for line in lines if line.startswith("✓ ")]
        assert stages[0] == "INGEST"
        assert "THREADS" in stages and "LLM" in stages and "ASSEMBLE" in stages
        assert any("1 messages" in line for line in lines)
        assert any("sections" in line and "items" in line for line in lines)
        assert any("delivered → mattermost" in line for line in lines)
        # No raw JSON in the rendered channel.
        assert not any(line.lstrip().startswith("{") for line in lines)


class TestResolveSink:
    def test_matrix(self, monkeypatch):
        # The full auto matrix (TTY/CI/live) lives in test_progress_live.py.
        monkeypatch.delenv("CI", raising=False)
        assert isinstance(resolve_sink("none", True), NullSink)
        assert isinstance(resolve_sink("plain", True), PlainSink)
        assert isinstance(resolve_sink("auto", False), PlainSink)


def _strip_ansi(text: str) -> str:
    # CI runners render typer's rich help with ANSI spans that split option
    # names mid-word; strip codes before asserting (the #95 CI failure).
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestCliFlag:
    def test_help_mentions_progress(self):
        result = CliRunner().invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--progress" in _strip_ansi(result.output)

    def test_invalid_value_exits_1(self):
        result = CliRunner().invoke(app, ["run", "--progress", "disco"])
        assert result.exit_code == 1

    def test_sink_passed_to_run(self, monkeypatch):
        captured = {}

        def fake_dry_run(*args, **kwargs):
            captured["sink"] = kwargs.get("sink")

        monkeypatch.setattr(cli_mod, "run_digest_dry_run", fake_dry_run)
        monkeypatch.setattr(cli_mod, "setup_logging", lambda **k: "/tmp/x.log")
        result = CliRunner().invoke(app, ["run", "--dry-run", "--progress", "plain"])
        assert result.exit_code == 0
        assert isinstance(captured["sink"], PlainSink)

        result = CliRunner().invoke(app, ["run", "--dry-run", "--progress", "none"])
        assert result.exit_code == 0
        assert isinstance(captured["sink"], NullSink)
