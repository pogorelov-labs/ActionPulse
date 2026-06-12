"""Design-system conformance guard (TERMINAL_DESIGN_ROADMAP, map layer 4).

Walks the source tree and fails on structural violations of
docs/development/TERMINAL_DESIGN.md. A rule that lives in a test cannot
silently rot — extend the allowlists consciously, in the same PR that adds
a new exception, with a comment explaining why.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "digest_core"

# The Cyrillic rule is scoped to RENDERERS only: pipeline code legitimately
# contains Russian content-matching patterns (lemmatizer tables, urgency
# markers, quote headers) that exist regardless of the report language.
RENDERER_PREFIXES = ("assemble/", "deliver/")
RENDERER_FILES = ("cli.py", "setup_wizard.py", "setup_autodetect.py")
CYRILLIC_EXEMPT_FILE = "assemble/labels.py"  # the sanctioned home of RU report strings
PRAGMA = "# i18n-ok"

UI_DIR = "ui/"

RGB_RE = re.compile(r"rgb\(|#[0-9a-fA-F]{6}\b")
CONSOLE_RE = re.compile(r"(?<![\w.])Console\(")
THEME_RE = re.compile(r"(?<![\w.])Theme\(")
SPINNER_RE = re.compile(r"spinner\s*=\s*[\"']")
CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")


def _py_files():
    return sorted(SRC.rglob("*.py"))


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC))


def _violations(pattern: re.Pattern, allowed_prefixes: tuple[str, ...] = ()) -> list[str]:
    out = []
    for f in _py_files():
        rel = _rel(f)
        if any(rel.startswith(p) for p in allowed_prefixes):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                out.append(f"{rel}:{i}: {line.strip()[:80]}")
    return out


def test_no_raw_colors_outside_ui():
    """RGB/hex colors only in ui/ (the brand gradient); tokens everywhere else."""
    assert _violations(RGB_RE, (UI_DIR,)) == []


def test_console_constructed_only_in_ui_factory():
    """One Console per process — get_console() is the only construction site."""
    assert _violations(CONSOLE_RE, (UI_DIR,)) == []


def test_theme_constructed_only_in_ui():
    assert _violations(THEME_RE, (UI_DIR,)) == []


def test_spinner_literals_only_in_ui():
    """One brand spinner — reference ui.SPINNER, never a string literal."""
    assert _violations(SPINNER_RE, (UI_DIR,)) == []


def test_report_strings_only_in_labels():
    """L1 guard: renderer files carry no inline Cyrillic — report strings live
    in labels.py. A deliberate bilingual line carries the `# i18n-ok` pragma
    (on the line or the line above)."""
    bad = []
    for f in _py_files():
        rel = _rel(f)
        if rel == CYRILLIC_EXEMPT_FILE:
            continue
        is_renderer = rel.startswith(RENDERER_PREFIXES) or rel in RENDERER_FILES
        if not is_renderer:
            continue
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if not CYRILLIC_RE.search(line) or PRAGMA in line:
                continue
            if i >= 2 and PRAGMA in lines[i - 2]:
                continue
            bad.append(f"{rel}:{i}: {line.strip()[:80]}")
    assert bad == []
