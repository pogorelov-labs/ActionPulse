"""ACTPULSE-97 — diagnostics must describe the toolchain the project actually uses.

`actionpulse diagnose` shelled out to bare `python3` and `command -v pytest`, so it
reported whatever was first on PATH. On one developer machine that meant the report
claimed Python 3.14.6 while the project ran on 3.11.15, with pytest/ruff/black from
a third install.

That is not cosmetic: corp-brief T1 records this output verbatim as *the* environment
record, and a corp-only failure often has no other evidence about the machine. A wrong
Python version there sends a later debugging session after the wrong hypothesis with
nothing to contradict it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from digest_core.cli import _diagnostics_env
from digest_core.config import EWSConfig
from digest_core.ingest.ews import EWSIngest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class TestDiagnosticsEnv:
    def test_names_the_running_interpreter_not_path_python(self):
        env = _diagnostics_env()
        assert env["ACTIONPULSE_DIAG_PY"] == sys.executable
        assert env["ACTIONPULSE_DIAG_BIN"] == str(Path(sys.executable).parent)

    def test_preserves_the_rest_of_the_environment(self, monkeypatch):
        monkeypatch.setenv("SOME_UNRELATED_VAR", "kept")
        assert _diagnostics_env()["SOME_UNRELATED_VAR"] == "kept"


@pytest.mark.skipif(not (SCRIPTS / "print_env.sh").exists(), reason="scripts/ absent")
class TestPrintEnvReportsTheProjectToolchain:
    def _run(self, env):
        return subprocess.run(
            [str(SCRIPTS / "print_env.sh")],
            capture_output=True,
            text=True,
            env=env,
        ).stdout

    def test_reports_the_project_interpreter_when_told(self):
        out = self._run(_diagnostics_env())
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        assert "project interpreter" in out
        assert sys.executable in out
        # the *reported* version must be this interpreter's, not PATH python3's
        python_block = out.split("Required tools:")[0]
        assert version in python_block

    def test_says_so_when_it_is_only_guessing_from_path(self):
        """Standalone `./scripts/print_env.sh` still works — but must not imply
        it knows the project's interpreter."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("ACTIONPULSE_DIAG_")}
        out = self._run(env)
        assert "PATH python3" in out
        assert "may NOT be the interpreter" in out

    def test_flags_a_path_tool_that_differs_from_the_project_env(self, tmp_path):
        """A machine with several toolchains is normal; reporting the wrong one is not."""
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        for tool in ("pytest", "ruff"):
            shim = fake_bin / tool
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(0o755)

        env = _diagnostics_env()
        env["ACTIONPULSE_DIAG_BIN"] = str(fake_bin)
        out = self._run(env)
        assert "(project env)" in out
        assert str(fake_bin / "pytest") in out


class TestEwsPreflightNamesActionPulseKeys:
    """One root cause ("nothing is configured") deserves one actionable message."""

    def _ingest(self, **kwargs):
        ingest = EWSIngest.__new__(EWSIngest)
        ingest.config = EWSConfig(**kwargs)
        return ingest

    def test_reports_every_missing_setting_at_once(self):
        with pytest.raises(ValueError) as excinfo:
            self._ingest(
                endpoint="", user_upn="", user_login="", user_domain=""
            )._check_configured()
        message = str(excinfo.value)
        # both, not one at a time
        assert "ews.endpoint" in message
        assert "ews.user_upn" in message

    def test_names_config_keys_and_env_vars_the_reader_can_act_on(self):
        """exchangelib's 'config.service_endpoint' names an attribute that does not
        exist in any ActionPulse config — grepping for it finds nothing."""
        with pytest.raises(ValueError) as excinfo:
            self._ingest(endpoint="", user_upn="")._check_configured()
        message = str(excinfo.value)
        assert "service_endpoint" not in message
        assert "EWS_ENDPOINT" in message
        assert "make setup" in message

    def test_missing_endpoint_alone_is_reported_even_with_a_valid_identity(self):
        """The identity error used to fire first and mask this."""
        with pytest.raises(ValueError, match="ews.endpoint"):
            self._ingest(endpoint="", user_upn="user@corp.example")._check_configured()

    def test_login_plus_domain_is_accepted_as_an_identity(self):
        self._ingest(
            endpoint="https://ews.corp.example/EWS/Exchange.asmx",
            user_upn="",
            user_login="user",
            user_domain="CORP",
        )._check_configured()

    def test_fully_configured_passes(self):
        self._ingest(
            endpoint="https://ews.corp.example/EWS/Exchange.asmx",
            user_upn="user@corp.example",
        )._check_configured()

    def test_whitespace_only_values_do_not_count_as_configured(self):
        with pytest.raises(ValueError, match="ews.endpoint"):
            self._ingest(endpoint="   ", user_upn="user@corp.example")._check_configured()


class TestLauncherShadowingIsDetected:
    """A stale `actionpulse` on PATH silently breaks every documented command.

    Real case: `/Library/Frameworks/Python.framework/Versions/3.13/bin/actionpulse`
    carried the shebang `#!/usr/local/bin/python3` — an interpreter with no
    digest_core — so a bare `actionpulse …` died on ModuleNotFoundError while the
    project's own launcher worked fine. The docs said the simple thing; the simple
    thing did not run.
    """

    def _run(self, env, tmp_path):
        return subprocess.run(
            [str(SCRIPTS / "print_env.sh")], capture_output=True, text=True, env=env, cwd=tmp_path
        ).stdout

    def _fake_broken_launcher(self, tmp_path):
        """A shim whose interpreter cannot import digest_core — the real failure."""
        shim_dir = tmp_path / "stale"
        shim_dir.mkdir()
        shim = shim_dir / "actionpulse"
        shim.write_text(
            '#!/bin/sh\nexec python3 -c "import digest_core_definitely_missing"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
        return shim_dir, shim

    def test_a_broken_path_launcher_is_reported_with_its_shebang_and_a_fix(self, tmp_path):
        shim_dir, shim = self._fake_broken_launcher(tmp_path)
        env = _diagnostics_env()
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
        out = self._run(env, tmp_path)
        assert "BROKEN actionpulse" in out
        assert str(shim) in out
        assert "shebang:" in out
        assert "uv run actionpulse" in out

    def test_the_probe_ignores_an_inherited_PYTHONPATH(self, tmp_path):
        """The bug this check nearly shipped with — and that two earlier versions of
        THIS test could not detect.

        `diagnose` runs the script as a child, so without `env -u PYTHONPATH` the
        broken shim imports through the *caller's* PYTHONPATH and the check reports
        "works": a false negative in a diagnostic, worse than no check.

        Getting a discriminating shim right took three tries, which is the point of
        mutation-checking a guard:
          1. import a never-existing module -> fails either way, proves nothing;
          2. import `digest_core` -> the venv already has it, so it *succeeds*
             either way, and the test failed against the correct implementation;
          3. import a marker module that exists ONLY on the injected PYTHONPATH ->
             fails in a bare shell (BROKEN, correct) and succeeds when PYTHONPATH
             leaks in ("works", wrong). Only this one can tell them apart.
        """
        fake_lib = tmp_path / "fakelib"
        fake_lib.mkdir()
        (fake_lib / "_ap_launcher_probe_marker.py").write_text("", encoding="utf-8")

        shim_dir = tmp_path / "stale"
        shim_dir.mkdir()
        shim = shim_dir / "actionpulse"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" -c "import _ap_launcher_probe_marker"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)

        env = _diagnostics_env()
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
        env["PYTHONPATH"] = str(fake_lib)  # would mask the breakage if inherited
        out = self._run(env, tmp_path)
        assert "BROKEN actionpulse" in out, (
            "an inherited PYTHONPATH masked the broken launcher — the probe must run "
            "it the way a bare shell would (env -u PYTHONPATH)"
        )

    def test_a_working_path_launcher_is_only_a_note(self):
        """Two working copies is normal; it must not read as an error."""
        out = self._run(_diagnostics_env(), Path(SCRIPTS).parent)
        assert "Launcher:" in out
        assert "✗ PATH has a BROKEN actionpulse-mcp" not in out
