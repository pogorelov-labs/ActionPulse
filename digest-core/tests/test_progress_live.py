"""RichLiveSink — the split-region live footer (T4).

Drives the real pipeline (replay harness) into a recording terminal-mode
console: permanent history lines land in scrollback, the transient footer
vanishes at run end, and the cursor is restored on every exit path.
"""

from pathlib import Path

from rich.console import Console

from digest_core import run as runner
from digest_core.progress import NullSink
from digest_core.ui import THEME, PlainSink, RichLiveSink, resolve_sink

from tests.test_e2e_pipeline import DummyMetrics, FakeDeliverer, FakeGateway, make_message


def _tty_console() -> Console:
    return Console(theme=THEME, record=True, width=100, force_terminal=True)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t


class TestFooter:
    def test_idle_footer_is_empty(self):
        sink = RichLiveSink(console=_tty_console())
        assert sink._footer().plain == ""

    def test_active_footer_shows_stage_and_elapsed(self):
        clock = FakeClock()
        sink = RichLiveSink(console=_tty_console(), now=clock)
        sink._stage = "llm"
        sink._stage_started = clock.t
        clock.t += 3.2
        footer = sink._footer()  # Spinner renderable
        text = footer.text.plain
        assert "LLM" in text
        assert "3.2s" in text

    def test_footer_warms_after_10s(self):
        clock = FakeClock()
        sink = RichLiveSink(console=_tty_console(), now=clock)
        sink._stage = "llm"
        sink._stage_started = clock.t
        clock.t += 3.0
        assert str(sink._footer().style) == "ap.accent"
        clock.t += 9.0  # 12s elapsed
        assert str(sink._footer().style) == "ap.warn"

    def test_llm_note_adds_second_line(self):
        sink = RichLiveSink(console=_tty_console())
        sink._stage = "llm"
        sink._stage_started = 0.0
        sink.on_llm_attempt("qwen35-397b-a17b", 1, 2)
        group = sink._footer()
        assert len(group.renderables) == 2
        assert "attempt 1/2 · qwen35-397b-a17b" in group.renderables[1].plain


class TestLifecycle:
    def test_live_starts_on_first_stage_and_stops_on_run_end(self):
        console = _tty_console()
        sink = RichLiveSink(console=console)
        assert sink._live is None
        sink.on_stage_start("ingest")
        assert sink._live is not None
        sink.on_stage_end("ingest", {"messages": 5}, 100)
        sink.on_run_end("ok")
        assert sink._live is None
        # Permanent history survived; footer (transient) did not linger.
        assert "✓ INGEST    5 messages (0.1s)" in console.export_text()

    def test_run_end_without_any_stage_is_safe(self):
        sink = RichLiveSink(console=_tty_console())
        sink.on_run_end("skipped")  # no Live ever started

    def test_full_pipeline_history_in_scrollback(self, monkeypatch, tmp_path):
        FakeDeliverer.deliveries.clear()
        snapshot_path = tmp_path / "snapshot.json"
        runner._dump_ingest_snapshot(snapshot_path, [make_message()], "2026-03-29")
        monkeypatch.chdir(Path(__file__).resolve().parents[2])
        monkeypatch.setattr(runner, "MetricsCollector", DummyMetrics)
        monkeypatch.setattr(runner, "start_health_server", lambda *a, **k: None)
        monkeypatch.setattr(runner, "LLMGateway", FakeGateway)
        monkeypatch.setattr(runner, "MattermostDeliverer", FakeDeliverer)

        console = _tty_console()
        sink = RichLiveSink(console=console)
        result = runner.run_digest(
            from_date="2026-03-29",
            sources=["ews"],
            out=str(tmp_path / "out"),
            model="qwen35-397b-a17b",
            window="calendar_day",
            state=str(tmp_path / "state"),
            force=True,
            replay_ingest=str(snapshot_path),
            sink=sink,
        )
        assert result
        # on_run_end fired from the pipeline's finally: Live released.
        assert sink._live is None
        text = console.export_text()
        assert "✓ INGEST" in text
        assert "✓ LLM" in text
        assert "delivered → mattermost" in text


class TestResolveSinkMatrix:
    def test_explicit_choices(self):
        assert isinstance(resolve_sink("none", True), NullSink)
        assert isinstance(resolve_sink("plain", True), PlainSink)
        assert isinstance(resolve_sink("live", False), RichLiveSink)

    def test_auto_tty_gets_live(self, monkeypatch):
        monkeypatch.delenv("CI", raising=False)
        assert isinstance(resolve_sink("auto", True), RichLiveSink)

    def test_auto_non_tty_gets_plain(self, monkeypatch):
        monkeypatch.delenv("CI", raising=False)
        sink = resolve_sink("auto", False)
        assert isinstance(sink, PlainSink) and not isinstance(sink, RichLiveSink)

    def test_auto_ci_gets_plain_even_on_tty(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        sink = resolve_sink("auto", True)
        assert isinstance(sink, PlainSink) and not isinstance(sink, RichLiveSink)


class TestIntraStageFooter:
    """U2: data progress and retry notes live in the footer only."""

    def test_progress_shows_in_footer(self):
        clock = FakeClock()
        sink = RichLiveSink(console=_tty_console(), now=clock)
        sink._stage = "ingest"
        sink._stage_started = clock.t
        sink.on_stage_progress("ingest", 247, None, "messages", "page 3")
        clock.t += 3.1
        assert "247 messages · page 3" in sink._footer().text.plain

    def test_retry_warms_footer_immediately_and_adds_note(self):
        clock = FakeClock()
        sink = RichLiveSink(console=_tty_console(), now=clock)
        sink._stage = "ingest"
        sink._stage_started = clock.t
        clock.t += 2.0  # well under the 10 s attention shift
        sink.on_stage_retry("ingest", 2, 8, "ConnectionError: boom")
        footer = sink._footer()
        assert str(footer.renderables[0].style) == "ap.warn"
        assert "retry 2/8 — ConnectionError: boom" in footer.renderables[1].plain

    def test_resumed_progress_clears_the_retry_note(self):
        clock = FakeClock()
        sink = RichLiveSink(console=_tty_console(), now=clock)
        sink._stage = "ingest"
        sink._stage_started = clock.t
        sink.on_stage_retry("ingest", 2, 8, "boom")
        sink.on_stage_progress("ingest", 300, None, "messages", "page 4")
        footer = sink._footer()  # back to a single calm spinner line
        assert str(footer.style) == "ap.accent"
        assert "300 messages" in footer.text.plain

    def test_stage_end_resets_progress_state(self):
        sink = RichLiveSink(console=_tty_console())
        sink.on_stage_start("ingest")
        sink.on_stage_progress("ingest", 10, None, "messages")
        sink.on_stage_end("ingest", {"messages": 10}, 50)
        sink.on_stage_start("threads")
        assert "10 messages" not in sink._footer().text.plain
        sink.on_run_end("ok")
