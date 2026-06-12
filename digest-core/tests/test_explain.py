"""LLM failure explainer (U7): payload whitelist, gateway ride, surfaces."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from digest_core import explain as explain_mod
from digest_core.cli import app
from digest_core.explain import (
    ExplainUnavailable,
    build_user_payload,
    collect_log_tail,
    explain_run,
)
from digest_core.ui import THEME
from digest_core.ui import menu as menu_mod


def _meta(tmp_path: Path, **overrides) -> dict:
    log_file = tmp_path / "run.log"
    log_file.write_text("\n".join(f'{{"event": "line {i}"}}' for i in range(200)), encoding="utf-8")
    meta = {
        "trace_id": "t-123456789",
        "digest_date": "2026-06-12",
        "status": "failed",
        "partial": False,
        "error": "EWS unreachable: ConnectionError",
        "stage_health": {"ingest": {"retries": 7}},
        "stage_durations_ms": {"ingest": 64000},
        "llm_request_trace": {},
        "log_file": str(log_file),
        "config_sanitized": {"must": "never reach the prompt"},
        "provenance": {"pipeline_version": "1.2.0"},
    }
    meta.update(overrides)
    return meta


def _write_meta(monkeypatch, tmp_path: Path, meta: dict) -> None:
    monkeypatch.setenv("ACTIONPULSE_HOME", str(tmp_path))
    out = tmp_path / "var" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"trace-{meta['trace_id']}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


class FakeGateway:
    last_instance = None

    def __init__(self, config, rate_broker=None, stage="explain", **kwargs):
        self.config = config
        self.stage = stage
        self.calls = []
        FakeGateway.last_instance = self

    def judge(self, system_prompt, user_content, trace_id="explain"):
        self.calls.append((system_prompt, user_content, trace_id))
        return {
            "likely_cause": "VPN/corp network is down — EWS never answered.",
            "explanation": "Ingest retried 7 times over 64s and every attempt failed.",
            "next_steps": ["Connect to the corp VPN", "Run actionpulse diagnose"],
        }

    def close(self):
        pass


class FailingGateway(FakeGateway):
    def judge(self, *args, **kwargs):
        raise ConnectionError("no route to llm gateway")


class TestPayload:
    def test_whitelist_and_log_tail(self, tmp_path):
        meta = _meta(tmp_path)
        payload = json.loads(build_user_payload(meta, collect_log_tail(meta)))
        assert payload["run_meta"]["status"] == "failed"
        assert payload["run_meta"]["stage_health"] == {"ingest": {"retries": 7}}
        assert payload["run_meta"]["pipeline_version"] == "1.2.0"
        assert "config_sanitized" not in json.dumps(payload)  # whitelist holds
        tail = payload["log_tail"].splitlines()
        assert len(tail) == explain_mod.LOG_TAIL_LINES  # capped
        assert tail[-1] == '{"event": "line 199"}'

    def test_missing_log_file_is_empty_tail(self, tmp_path):
        meta = _meta(tmp_path, log_file=str(tmp_path / "gone.log"))
        assert collect_log_tail(meta) == ""
        assert collect_log_tail({"log_file": None}) == ""

    def test_ru_language_appends_answer_instruction(self):
        assert "Answer in Russian" in explain_mod._system_prompt("ru")
        assert "Answer in Russian" not in explain_mod._system_prompt("en")


class TestExplainRun:
    def test_happy_path(self, monkeypatch, tmp_path):
        meta = _meta(tmp_path)
        _write_meta(monkeypatch, tmp_path, meta)
        monkeypatch.setattr(explain_mod, "LLMGateway", FakeGateway)
        result = explain_run()
        assert "VPN" in result.likely_cause
        assert result.next_steps == ["Connect to the corp VPN", "Run actionpulse diagnose"]
        assert result.status == "failed" and result.digest_date == "2026-06-12"
        system_prompt, user_content, _ = FakeGateway.last_instance.calls[0]
        assert "run doctor" in system_prompt
        assert "stage_health" in user_content
        # The explainer rides its own stage budget, never the extractor's.
        assert FakeGateway.last_instance.stage == "explain"
        assert (
            FakeGateway.last_instance.config.max_output_tokens
            == explain_mod.EXPLAIN_MAX_OUTPUT_TOKENS
        )

    def test_no_meta_is_friendly(self, monkeypatch, tmp_path):
        # The search itself is diagnostics' behavior; here only the message
        # path matters (the dev repo may legitimately contain old runs).
        def no_meta(**kwargs):
            raise FileNotFoundError("nothing")

        monkeypatch.setattr(explain_mod, "_find_metadata", no_meta)
        try:
            explain_run()
        except ExplainUnavailable as exc:
            assert "No run metadata" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ExplainUnavailable")

    def test_offline_gateway_fails_fast_with_corp_hint(self, monkeypatch, tmp_path):
        _write_meta(monkeypatch, tmp_path, _meta(tmp_path))
        monkeypatch.setattr(explain_mod, "LLMGateway", FailingGateway)
        try:
            explain_run()
        except ExplainUnavailable as exc:
            assert "corp network" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ExplainUnavailable")


class TestExplainCommand:
    def test_renders_card(self, monkeypatch, tmp_path):
        _write_meta(monkeypatch, tmp_path, _meta(tmp_path))
        monkeypatch.setattr(explain_mod, "LLMGateway", FakeGateway)
        result = CliRunner().invoke(app, ["explain"])
        assert result.exit_code == 0
        assert "VPN" in result.output
        assert "→ Connect to the corp VPN" in result.output
        assert "run 2026-06-12 · status failed" in result.output

    def test_unavailable_exits_1(self, monkeypatch, tmp_path):
        def no_meta(**kwargs):
            raise FileNotFoundError("nothing")

        monkeypatch.setattr(explain_mod, "_find_metadata", no_meta)
        result = CliRunner().invoke(app, ["explain"])
        assert result.exit_code == 1

    def test_failed_run_prints_explain_hint(self, monkeypatch):
        monkeypatch.setattr(
            "digest_core.cli.run_digest", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        )
        monkeypatch.setattr("digest_core.cli.setup_logging", lambda **k: None)
        result = CliRunner().invoke(app, ["run", "--replay-ingest", "/nope.json"])
        assert result.exit_code == 1
        assert "actionpulse explain" in result.output


class TestMenuOffer:
    def _scripted(self, monkeypatch, choices):
        seq = iter(choices)

        def fake_choose(label, options, default_index=0, console=None, cancel_value=None):
            try:
                return next(seq)
            except StopIteration:
                return "quit"

        monkeypatch.setattr(menu_mod, "choose", fake_choose)
        monkeypatch.setattr(menu_mod.Console, "input", lambda self, *a, **k: "", raising=False)

    def _console(self):
        return Console(record=True, width=100, force_terminal=False, theme=THEME)

    def test_failed_run_offers_explain(self, monkeypatch, tmp_path):
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", tmp_path / "last_run.json")
        calls = {"explain": 0}
        self._scripted(monkeypatch, ["run", "today", "explain", "quit"])

        def boom(_dry, _choice):
            raise RuntimeError("ews unreachable")

        menu_mod.run_menu(
            on_run=boom,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            on_explain=lambda: calls.__setitem__("explain", calls["explain"] + 1),
            console=self._console(),
        )
        assert calls["explain"] == 1

    def test_offer_declined_via_menu(self, monkeypatch, tmp_path):
        monkeypatch.setattr(menu_mod, "LAST_RUN_PATH", tmp_path / "last_run.json")
        calls = {"explain": 0}
        self._scripted(monkeypatch, ["run", "today", "menu", "quit"])

        def boom(_dry, _choice):
            raise RuntimeError("ews unreachable")

        menu_mod.run_menu(
            on_run=boom,
            on_diagnose=lambda: None,
            on_settings=lambda: None,
            on_read=lambda date: None,
            on_explain=lambda: calls.__setitem__("explain", calls["explain"] + 1),
            console=self._console(),
        )
        assert calls["explain"] == 0
