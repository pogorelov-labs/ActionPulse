"""The corp brief must be runnable, and its privacy promise must be true.

The brief is the artifact that converts a corp session into repo evidence, and it
gates everything downstream (ACTPULSE-90 -> A1.7 / EP-14 / EP-15 / PC-2). It had
never been *executed* — it was written from reasoning about what a session should
do, which is how three of its first instructions turned out not to run.

The important one was not an inconvenience. The brief told the agent to write real
corporate mail to `ap-snapshot.json` / `ap-recording-v1.json` while promising those
were gitignored — and neither name matched `*.snapshot.json` (needs a dot) or
`*-recording.json` (must end there). A privacy rule that depends on exact spelling
is not a rule, so it is asserted here instead of promised in prose.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BRIEF = REPO / "digest-core" / "docs" / "CORP_AGENT_BRIEF.md"

pytestmark = pytest.mark.skipif(not BRIEF.exists(), reason="brief absent")


def _brief() -> str:
    return BRIEF.read_text(encoding="utf-8")


def _is_ignored(relpath: str) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", relpath], cwd=REPO, capture_output=True
        ).returncode
        == 0
    )


def _capture_filenames() -> set[str]:
    """Every file the brief's commands write that could hold corp data."""
    names = set()
    for match in re.finditer(r"--(?:dump-ingest|record-llm|json-out)[= ]+(\S+)", _brief()):
        names.add(match.group(1).lstrip("~/"))
    return names


class TestCaptureFilesCannotBeCommitted:
    def test_the_brief_actually_names_capture_files(self):
        """Guard the guard: if the extraction breaks, the checks below pass vacuously."""
        assert len(_capture_filenames()) >= 3, _capture_filenames()

    @pytest.mark.parametrize("name", sorted(_capture_filenames()))
    def test_every_capture_file_the_brief_creates_is_gitignored(self, name):
        assert _is_ignored(name), (
            f"the brief tells the agent to create {name!r}, which holds real mail or "
            "run data, but .gitignore does not match it — one `git add -A` from a "
            "privacy incident"
        )

    @pytest.mark.parametrize(
        "name",
        [
            # shapes, not spellings — the failure was a name one hyphen off
            "ap-snapshot.json",
            "my.snapshot.json",
            "snapshot.json",
            "ap-recording-v1.json",
            "ews-recording.json",
            "llm-recording-2026.json",
            "parity.json",
            "digest-core/ap-snapshot.json",
        ],
    )
    def test_capture_shapes_are_ignored_wherever_they_land(self, name):
        assert _is_ignored(name), name

    def test_the_committed_eval_corpus_is_still_trackable(self):
        """Broadening the ignore must not swallow the corpus the repo ships."""
        corpus = sorted((REPO / "digest-core/src/digest_core/eval/corpus").glob("*.snapshot.json"))
        if not corpus:
            pytest.skip("no committed corpus snapshots")
        relative = corpus[0].relative_to(REPO).as_posix()
        assert not _is_ignored(relative), (
            f"{relative} is committed on purpose; the corp-capture ignore rules "
            "must not swallow it (digest-core/.gitignore re-includes it)"
        )


class TestBriefIsAgentExecutable:
    def test_it_never_tells_a_non_interactive_agent_to_run_the_wizard(self):
        """`make setup` ends in an interactive wizard and hangs an agent at step 1."""
        prompt = _brief().split("## 1. The task list")[0]
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith("make setup") or stripped.endswith("&& make setup"):
                pytest.fail(f"copy-paste prompt runs the interactive wizard: {line!r}")

    def test_launcher_is_always_invoked_through_uv_run(self):
        """A bare `actionpulse` may be absent, or a stale global shim that fails with
        ModuleNotFoundError. `uv run` always resolves inside the project env."""
        bare = [
            line
            for line in _brief().splitlines()
            if re.search(r"(?<!uv run )(?<!`)\bactionpulse ", line)
            and "never bare" not in line
            and "`actionpulse`" not in line
        ]
        assert not bare, "bare launcher invocations: " + "; ".join(bare)

    def test_tasks_needing_optional_extras_say_to_install_them(self):
        """T5 needs `store`, T6 needs `mcp`; a plain `uv sync` omits both by design."""
        text = _brief()
        assert "--extra store" in text and "--extra mcp" in text

    def test_every_referenced_companion_doc_exists(self):
        for match in re.finditer(r"digest-core/docs/([A-Za-z0-9_./-]+\.md)", _brief()):
            path = REPO / "digest-core" / "docs" / match.group(1)
            assert path.exists(), f"brief references a missing doc: {match.group(0)}"

    def test_the_deliverable_directory_exists(self):
        """'A session that produces no file here did not happen.'"""
        assert (REPO / "digest-core/docs/corp-runs").is_dir()
