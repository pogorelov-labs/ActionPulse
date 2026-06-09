"""
Prompt quality evaluation for the daily prompt iteration loop (COMMON-12).

Evaluates a digest output against scoring criteria:
  - evidence_id validity
  - confidence calibration
  - section assignment rules
  - structural contract compliance

Usage:
    from digest_core.eval.prompt_eval import evaluate_digest
    report = evaluate_digest(digest_dict, evidence_ids={"ev-001", "ev-002"})
    print(report.summary())
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ── Issue severity ────────────────────────────────────────────────────────────

ISSUE_ERROR = "error"  # contract violation — must fix
ISSUE_WARN = "warn"  # quality concern — should fix
ISSUE_INFO = "info"  # informational observation


@dataclass
class EvalIssue:
    """A single quality finding in the eval report."""

    severity: str  # ISSUE_ERROR | ISSUE_WARN | ISSUE_INFO
    category: str  # e.g. "evidence_id", "confidence", "section_assignment"
    message: str
    item_title: Optional[str] = None
    section_title: Optional[str] = None

    def __str__(self) -> str:
        loc = ""
        if self.section_title:
            loc += f"[{self.section_title}]"
        if self.item_title:
            loc += f" «{self.item_title[:60]}»"
        return f"{self.severity.upper()} [{self.category}]{loc}: {self.message}"


# ── Scoring weights ───────────────────────────────────────────────────────────

# Each error deducts this many points from 100
_DEDUCT = {
    ISSUE_ERROR: 10,
    ISSUE_WARN: 3,
    ISSUE_INFO: 0,
}

# ── Known section titles ──────────────────────────────────────────────────────

SECTION_ACTIONS = "Мои действия"
SECTION_URGENT = "Срочное"
SECTION_FYI = "К сведению"
KNOWN_SECTIONS = {SECTION_ACTIONS, SECTION_URGENT, SECTION_FYI}

# Confidence thresholds per taxonomy (ARCHITECTURE.md §9.1)
CONF_MIN_ACCEPTABLE = 0.50  # prompt should not emit below this
CONF_WARN_ACTIONS = 0.70  # "Мои действия" items below this are suspicious


# ── Main evaluator ────────────────────────────────────────────────────────────


@dataclass
class EvalReport:
    """
    Quality report for a single digest output.

    Attributes:
        prompt_version:   version string from digest (e.g., "extract_actions.v1")
        digest_date:      YYYY-MM-DD
        issues:           list of EvalIssue found
        total_items:      total number of extracted items
        section_counts:   {section_title: item_count}
        score:            0-100 quality score (100 = perfect)
        evidence_ids_checked: whether evidence_ids were cross-validated
    """

    prompt_version: str
    digest_date: str
    issues: List[EvalIssue] = field(default_factory=list)
    total_items: int = 0
    section_counts: Dict[str, int] = field(default_factory=dict)
    score: int = 100
    evidence_ids_checked: bool = False

    # ── Derived stats ─────────────────────────────────────────────────────────

    @property
    def errors(self) -> List[EvalIssue]:
        return [i for i in self.issues if i.severity == ISSUE_ERROR]

    @property
    def warnings(self) -> List[EvalIssue]:
        return [i for i in self.issues if i.severity == ISSUE_WARN]

    @property
    def infos(self) -> List[EvalIssue]:
        return [i for i in self.issues if i.severity == ISSUE_INFO]

    @property
    def items_without_errors(self) -> int:
        """Number of items that have zero error-level issues."""
        error_items = {
            i.item_title for i in self.issues if i.severity == ISSUE_ERROR and i.item_title
        }
        return max(0, self.total_items - len(error_items))

    @property
    def quality_rate(self) -> float:
        """Per-item quality rate: fraction of items with no errors (0.0–1.0)."""
        if self.total_items == 0:
            return 1.0  # empty digest is valid
        return self.items_without_errors / self.total_items

    def _grade(self) -> str:
        if self.score >= 90:
            return "A"
        if self.score >= 75:
            return "B"
        if self.score >= 60:
            return "C"
        return "F"

    # ── Output ────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a human-readable markdown summary."""
        lines: List[str] = []
        lines.append(f"## Prompt Eval — {self.digest_date}")
        lines.append(f"Prompt version: `{self.prompt_version}`")
        lines.append(
            f"Score: **{self.score}/100** (grade {self._grade()}) | "
            f"Items: {self.total_items} | "
            f"Clean items: {self.quality_rate:.0%} | "
            f"Errors: {len(self.errors)} | Warnings: {len(self.warnings)}"
        )

        # section breakdown
        if self.section_counts:
            parts = ", ".join(f"{title} ({count})" for title, count in self.section_counts.items())
            lines.append(f"Sections: {parts}")

        if self.evidence_ids_checked:
            lines.append("Evidence IDs: validated against ingest snapshot ✓")

        lines.append("")
        lines.append("### Issues")

        if not self.issues:
            lines.append("✓ No issues found.")
        else:
            for issue in self.issues:
                icon = {"error": "✗", "warn": "⚠", "info": "ℹ"}[issue.severity]
                lines.append(f"{icon} {issue}")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "prompt_version": self.prompt_version,
            "digest_date": self.digest_date,
            "score": self.score,
            "grade": self._grade(),
            "total_items": self.total_items,
            "section_counts": self.section_counts,
            "evidence_ids_checked": self.evidence_ids_checked,
            "quality_rate": round(self.quality_rate, 3),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "issues": [
                {
                    "severity": i.severity,
                    "category": i.category,
                    "message": i.message,
                    "section": i.section_title,
                    "item": i.item_title,
                }
                for i in self.issues
            ],
        }


def evaluate_digest(
    digest: Dict[str, Any],
    *,
    evidence_ids: Optional[Set[str]] = None,
) -> EvalReport:
    """
    Evaluate a digest output dict against quality criteria.

    Args:
        digest:       parsed digest JSON (schema v1 from Digest Pydantic model)
        evidence_ids: set of valid evidence IDs from the ingest snapshot.
                      If provided, evidence_id cross-validation is enabled.

    Returns:
        EvalReport with issues, score, and section stats.
    """
    prompt_version = digest.get("prompt_version", "unknown")
    digest_date = digest.get("digest_date", "unknown")
    sections: List[Dict[str, Any]] = digest.get("sections", [])

    report = EvalReport(
        prompt_version=prompt_version,
        digest_date=digest_date,
        evidence_ids_checked=(evidence_ids is not None),
    )

    # ── Top-level structural checks ───────────────────────────────────────────

    if "sections" not in digest:
        report.issues.append(
            EvalIssue(ISSUE_ERROR, "structure", "'sections' key is missing from digest")
        )
        _compute_score(report)
        return report

    if not isinstance(sections, list):
        report.issues.append(EvalIssue(ISSUE_ERROR, "structure", "'sections' field is not a list"))
        _compute_score(report)
        return report

    # ── Track section titles seen (for duplicate-section detection) ───────────
    seen_section_titles: Set[str] = set()
    all_item_titles: Set[str] = set()

    for section in sections:
        sec_title = section.get("title", "<no title>")
        items: List[Dict[str, Any]] = section.get("items", [])

        # Count
        report.section_counts[sec_title] = len(items)
        report.total_items += len(items)

        # Duplicate section title
        if sec_title in seen_section_titles:
            report.issues.append(
                EvalIssue(
                    ISSUE_ERROR,
                    "section_assignment",
                    f"Duplicate section title '{sec_title}'",
                    section_title=sec_title,
                )
            )
        seen_section_titles.add(sec_title)

        # Unknown section title
        if sec_title not in KNOWN_SECTIONS:
            report.issues.append(
                EvalIssue(
                    ISSUE_WARN,
                    "section_assignment",
                    f"Unknown section title '{sec_title}' (not in taxonomy)",
                    section_title=sec_title,
                )
            )

        # Empty section (should not appear per prompt rules)
        if len(items) == 0:
            report.issues.append(
                EvalIssue(
                    ISSUE_WARN,
                    "section_assignment",
                    "Empty section — prompt rules say not to include empty sections",
                    section_title=sec_title,
                )
            )

        for item in items:
            _check_item(item, sec_title, evidence_ids, all_item_titles, report)

    # ── Global checks ─────────────────────────────────────────────────────────

    # Totally empty digest with no sections is valid but worth noting
    if not sections:
        report.issues.append(
            EvalIssue(
                ISSUE_INFO,
                "structure",
                "Digest has no sections (empty result — valid if no actionable evidence)",
            )
        )

    _compute_score(report)
    return report


def _check_item(
    item: Dict[str, Any],
    sec_title: str,
    evidence_ids: Optional[Set[str]],
    seen_titles: Set[str],
    report: EvalReport,
) -> None:
    """Run all per-item quality checks."""
    title = item.get("title", "<no title>")
    evidence_id = item.get("evidence_id")
    confidence = item.get("confidence")
    source_ref = item.get("source_ref")

    # ── evidence_id ───────────────────────────────────────────────────────────
    if not evidence_id:
        report.issues.append(
            EvalIssue(
                ISSUE_ERROR,
                "evidence_id",
                "Item missing evidence_id",
                item_title=title,
                section_title=sec_title,
            )
        )
    elif evidence_ids is not None and evidence_id not in evidence_ids:
        report.issues.append(
            EvalIssue(
                ISSUE_ERROR,
                "evidence_id",
                f"evidence_id '{evidence_id}' not found in ingest evidence list",
                item_title=title,
                section_title=sec_title,
            )
        )

    # ── source_ref ────────────────────────────────────────────────────────────
    if not source_ref:
        report.issues.append(
            EvalIssue(
                ISSUE_ERROR,
                "source_ref",
                "Item missing source_ref",
                item_title=title,
                section_title=sec_title,
            )
        )
    elif not isinstance(source_ref, dict):
        report.issues.append(
            EvalIssue(
                ISSUE_ERROR,
                "source_ref",
                "source_ref is not a dict",
                item_title=title,
                section_title=sec_title,
            )
        )
    elif "type" not in source_ref:
        report.issues.append(
            EvalIssue(
                ISSUE_ERROR,
                "source_ref",
                "source_ref missing required 'type' field",
                item_title=title,
                section_title=sec_title,
            )
        )

    # ── confidence ────────────────────────────────────────────────────────────
    if confidence is None:
        report.issues.append(
            EvalIssue(
                ISSUE_ERROR,
                "confidence",
                "Item missing confidence field",
                item_title=title,
                section_title=sec_title,
            )
        )
    elif not isinstance(confidence, (int, float)):
        report.issues.append(
            EvalIssue(
                ISSUE_ERROR,
                "confidence",
                f"confidence is not a number: {confidence!r}",
                item_title=title,
                section_title=sec_title,
            )
        )
    else:
        if not (0.0 <= confidence <= 1.0):
            report.issues.append(
                EvalIssue(
                    ISSUE_ERROR,
                    "confidence",
                    f"confidence {confidence:.2f} out of range [0, 1]",
                    item_title=title,
                    section_title=sec_title,
                )
            )
        elif confidence < CONF_MIN_ACCEPTABLE:
            report.issues.append(
                EvalIssue(
                    ISSUE_WARN,
                    "confidence",
                    f"confidence {confidence:.2f} below min acceptable ({CONF_MIN_ACCEPTABLE}) "
                    "— prompt rules say to omit such items",
                    item_title=title,
                    section_title=sec_title,
                )
            )
        elif sec_title == SECTION_ACTIONS and confidence < CONF_WARN_ACTIONS:
            report.issues.append(
                EvalIssue(
                    ISSUE_WARN,
                    "confidence",
                    f"«Мои действия» item has low confidence {confidence:.2f} "
                    f"(threshold for actionable items: {CONF_WARN_ACTIONS}) — potential false positive",
                    item_title=title,
                    section_title=sec_title,
                )
            )

    # ── Duplicate item titles ─────────────────────────────────────────────────
    title_lower = title.lower().strip()
    if title_lower in seen_titles:
        report.issues.append(
            EvalIssue(
                ISSUE_WARN,
                "duplicate",
                f"Duplicate item title «{title[:60]}» appears in multiple sections or twice",
                item_title=title,
                section_title=sec_title,
            )
        )
    seen_titles.add(title_lower)

    # ── due date format ───────────────────────────────────────────────────────
    due = item.get("due")
    if due is not None and due not in ("today", "tomorrow"):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(due)):
            report.issues.append(
                EvalIssue(
                    ISSUE_WARN,
                    "due_date",
                    f"due '{due}' is not YYYY-MM-DD / today / tomorrow / null",
                    item_title=title,
                    section_title=sec_title,
                )
            )


def _compute_score(report: EvalReport) -> None:
    """Deduct points and clamp to [0, 100]."""
    deduction = sum(_DEDUCT[i.severity] for i in report.issues)
    report.score = max(0, 100 - deduction)


# ── Convenience loaders ───────────────────────────────────────────────────────


def evaluate_digest_file(
    digest_path: Path,
    ingest_snapshot_path: Optional[Path] = None,
) -> EvalReport:
    """
    Load digest JSON from disk and evaluate it.

    Args:
        digest_path:           path to digest-YYYY-MM-DD.json
        ingest_snapshot_path:  optional path to ingest snapshot for evidence_id validation

    Returns:
        EvalReport
    """
    digest = json.loads(digest_path.read_text(encoding="utf-8"))

    evidence_ids: Optional[Set[str]] = None
    if ingest_snapshot_path is not None:
        snapshot = json.loads(ingest_snapshot_path.read_text(encoding="utf-8"))
        evidence_ids = _extract_evidence_ids(snapshot)

    return evaluate_digest(digest, evidence_ids=evidence_ids)


def _extract_evidence_ids(snapshot: Dict[str, Any]) -> Set[str]:
    """
    Extract valid evidence_ids from a snapshot file.

    Supports these formats (tried in order):

    1. **Explicit evidence list** — ``{"evidence_ids": ["ev-001", ...]}``
       Simplest: a file you create manually with the IDs sent to the LLM.

    2. **Flat chunk list** — ``{"chunks": [{"evidence_id": "ev-001", ...}, ...]}``
       Matches evidence output from the pipeline's split stage.

    3. **LLM replay** — ``{"responses": [{"data": {"sections": [...]}}]}``
       Falls back to parsing evidence_ids from the *input messages* text
       using the ``Evidence N (ID: <id>, ...)`` pattern that ``run.py``
       formats for the LLM.  If no input messages are recorded, this
       extracts from the output — which is circular (flagged in report).

    Note: **ingest snapshots** (``--dump-ingest``) contain raw messages
    *before* evidence splitting — they have no ``evidence_id`` fields.
    Use them with ``--replay-ingest``, not with ``eval-prompt``.
    """
    ids: Set[str] = set()

    # Format 1: explicit list
    explicit = snapshot.get("evidence_ids", [])
    if explicit:
        return set(explicit)

    # Format 2: flat chunk list
    for chunk in snapshot.get("chunks", []):
        eid = chunk.get("evidence_id")
        if eid:
            ids.add(eid)
    if ids:
        return ids

    # Format 3: LLM replay — extract from the recorded INPUT messages only.
    # Deriving valid ids from the LLM *output* would be circular (the output's
    # ids would validate themselves), so it is intentionally NOT done (PR7).
    # When inputs are absent, return the empty set: callers surface this as
    # ``evidence_ids_unverifiable`` rather than silently passing.
    for response in snapshot.get("responses", []):
        for msg in response.get("messages", []):
            content = msg.get("content", "")
            ids.update(_parse_evidence_ids_from_text(content))

    return ids


def _parse_evidence_ids_from_text(text: str) -> Set[str]:
    """
    Parse evidence IDs from the formatted evidence block text sent to the LLM.

    The pipeline formats evidence as:
        Evidence N (ID: ev_abc123def456, Msg: msg-xyz, Thread: conv-001)

    Matches both PR1 content-hash ids (ev_<hex>) and legacy uuid ids (ev-<uuid>).
    """
    return set(re.findall(r"\bID:\s*(ev[_-][a-zA-Z0-9-]+)", text))
