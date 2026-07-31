"""
Markdown output assembler for digest data, localized per report.language.
"""

from pathlib import Path
import structlog

from digest_core.assemble.labels import (
    DEFAULT_LANGUAGE,
    confidence_text,
    display_title,
    report_strings,
    should_show_confidence,
)
from digest_core.llm.schemas import Digest

logger = structlog.get_logger()


class MarkdownAssembler:
    """Assemble digest data into Markdown output in the configured report language."""

    def __init__(self, language: str = DEFAULT_LANGUAGE):
        self.language = language
        self._s = report_strings(language)
        self.max_words = 400
        self.max_items_per_section = 10

    def write_digest(self, digest_data: Digest, output_path: Path) -> None:
        """Write digest data to Markdown file."""
        logger.info("Writing Markdown digest", output_path=str(output_path))

        try:
            # Generate markdown content
            markdown_content = self._generate_markdown(digest_data)

            # Write to file
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            word_count = self._count_words(markdown_content)
            logger.info(
                "Markdown digest written successfully",
                output_path=str(output_path),
                word_count=word_count,
            )

        except Exception as e:
            logger.error(
                "Failed to write Markdown digest",
                output_path=str(output_path),
                error=str(e),
            )
            raise

    def _generate_markdown(self, digest_data: Digest) -> str:
        """Generate markdown content from digest data."""
        lines = []

        # Header
        digest_date = (
            digest_data.get("digest_date", "")
            if isinstance(digest_data, dict)
            else getattr(digest_data, "digest_date", "")
        )
        trace_id = (
            digest_data.get("trace_id", "")
            if isinstance(digest_data, dict)
            else getattr(digest_data, "trace_id", "")
        )
        lines.append(f"# {self._s['digest_header']} - {digest_date}")
        lines.append("")
        lines.append(f"*Trace ID: {trace_id}*")
        lines.append("")

        # Check if digest is empty
        sections = (
            digest_data.get("sections", [])
            if isinstance(digest_data, dict)
            else digest_data.sections
        )
        total_items = sum(
            len(section.get("items", []) if isinstance(section, dict) else section.items)
            for section in sections
        )
        if total_items == 0:
            lines.append(self._s["no_actions"])
            return "\n".join(lines)

        # Sections
        for section in sections:
            # Handle both object and dict formats
            items = section.get("items", []) if isinstance(section, dict) else section.items
            title = section.get("title", "") if isinstance(section, dict) else section.title

            if not items:
                continue

            lines.append(f"## {display_title(title, self.language)}")
            lines.append("")

            # Limit items per section
            items_to_show = items[: self.max_items_per_section]

            for i, item in enumerate(items_to_show, 1):
                # Handle both object and dict formats
                if isinstance(item, dict):
                    item_title = item.get("title", "")
                    item_due = item.get("due")
                    item_confidence = item.get("confidence", 0)
                    item_evidence_id = item.get("evidence_id", "")
                    item_source_ref = item.get("source_ref", {})
                    # Accept OLD-key dicts (email_subject) so a pre-rename
                    # artifact rendered as a raw dict still shows its subject.
                    item_source_subject = item.get("source_subject") or item.get("email_subject")
                    item_weak_evidence = item.get("weak_evidence", False)
                    item_owners = item.get("owners") or []
                    item_participants = item.get("participants") or []
                    item_location = item.get("location")
                    item_impact = item.get("impact")
                else:
                    item_title = item.title
                    item_due = item.due
                    item_confidence = item.confidence
                    item_evidence_id = item.evidence_id
                    item_source_ref = item.source_ref
                    item_source_subject = getattr(item, "source_subject", None)
                    item_weak_evidence = getattr(item, "weak_evidence", False)
                    # getattr with a default: a pre-A1.5 artifact deserialized into an
                    # older Item, or a plain stand-in in a test, has no such attribute.
                    item_owners = getattr(item, "owners", None) or []
                    item_participants = getattr(item, "participants", None) or []
                    item_location = getattr(item, "location", None)
                    item_impact = getattr(item, "impact", None)

                lines.append(f"### {i}. {item_title}")

                # Add due date if present
                if item_due:
                    lines.append(f"**{self._s['due_label']}:** {item_due}")

                # A1.5 — facts only the v3 contract extracts. Each is emitted only when
                # present, so a v1 digest renders byte-identically to before.
                if item_owners:
                    lines.append(f"**{self._s['owners_label']}:** {', '.join(item_owners)}")
                if item_participants:
                    lines.append(
                        f"**{self._s['participants_label']}:** {', '.join(item_participants)}"
                    )
                if item_location:
                    lines.append(f"**{self._s['location_label']}:** {item_location}")
                if item_impact:
                    lines.append(f"**{self._s['impact_label']}:** {item_impact}")

                # Add confidence only when it adds signal (borderline items, and
                # never alongside the weak-evidence marker — see labels.py).
                if should_show_confidence(item_confidence, item_weak_evidence):
                    lines.append(
                        f"**{self._s['confidence_label']}:**"
                        f" {self._format_confidence(item_confidence)}"
                    )

                # Weak-evidence marker — parity with the Mattermost ⚠ badge so a
                # quarantined / weakly-supported item reads the same on both
                # surfaces (the .md previously showed no warning at all).
                if item_weak_evidence:
                    lines.append(f"⚠ {self._s['weak_basis']}")

                # Add evidence reference (required format) with email subject
                source_type = item_source_ref.get("type", "unknown")
                if item_source_subject:
                    lines.append(
                        f'**{self._s["source_label"]}:** {source_type}, {self._s["subject_word"]} "{item_source_subject}", evidence {item_evidence_id}'
                    )
                else:
                    lines.append(
                        f"**{self._s['source_label']}:** {source_type}, evidence {item_evidence_id}"
                    )

                lines.append("")

            # Add note if items were truncated
            if len(items) > self.max_items_per_section:
                remaining = len(items) - self.max_items_per_section
                lines.append(self._s["and_more_items"].format(remaining=remaining))
                lines.append("")

        # Statistics section
        total_processed = (
            digest_data.get("total_emails_processed", 0)
            if isinstance(digest_data, dict)
            else getattr(digest_data, "total_emails_processed", 0)
        )
        emails_with_actions = (
            digest_data.get("emails_with_actions", 0)
            if isinstance(digest_data, dict)
            else getattr(digest_data, "emails_with_actions", 0)
        )

        if total_processed > 0:
            lines.append(f"## {self._s['statistics_header']}")
            lines.append("")
            percent = (
                int((emails_with_actions / total_processed) * 100) if total_processed > 0 else 0
            )
            lines.append(
                self._s["processed_summary"].format(
                    total=total_processed, with_actions=emails_with_actions, percent=percent
                )
            )
            lines.append("")

        # (Vestigial Sources section removed -- it rendered
        #  "### Evidence <id>" / "*ID: <id>*" with the id twice and no content;
        #  the per-item Source lines are the real P2 traceability.)

        # Check word count and truncate if necessary
        content = "\n".join(lines)
        word_count = self._count_words(content)

        if word_count > self.max_words:
            logger.warning(
                "Markdown content exceeds word limit",
                word_count=word_count,
                max_words=self.max_words,
            )
            content = self._truncate_content(content, self.max_words)

        return content

    def _format_confidence(self, confidence: float) -> str:
        """Format confidence score as report-language text (lowercase, MM parity)."""
        return confidence_text(confidence, self.language)

    def _count_words(self, text: str) -> int:
        """Count words in text."""
        # Simple word counting (split by whitespace)
        words = text.split()
        return len(words)

    def _truncate_content(self, content: str, max_words: int) -> str:
        """Truncate content to fit the word limit at a LINE boundary.

        Accumulates whole lines until the cumulative word count would exceed the
        budget (leaving room for the truncation note), then appends the note.
        Newlines are preserved so headings and blank lines survive — unlike a
        naive ``" ".join(words[:N])`` which collapses all markdown structure.
        """
        words = content.split()
        if len(words) <= max_words:
            return content

        budget = max_words - 10  # leave room for the truncation note
        kept_lines: list[str] = []
        used = 0
        for line in content.split("\n"):
            line_words = len(line.split())
            if used + line_words > budget:
                break
            kept_lines.append(line)
            used += line_words

        truncated_content = "\n".join(kept_lines).rstrip("\n")
        truncated_content += "\n\n" + self._s["truncated_note"]
        return truncated_content

    def generate_summary(self, digest_data) -> str:
        """Generate a brief summary of the digest."""
        sections = (
            digest_data.get("sections", [])
            if isinstance(digest_data, dict)
            else digest_data.sections
        )
        total_items = sum(
            len(section.get("items", []) if isinstance(section, dict) else section.items)
            for section in sections
        )

        if total_items == 0:
            return self._s["no_actions"]

        summary_parts = [self._s["found_actions"].format(total=total_items)]

        for section in sections:
            items = section.get("items", []) if isinstance(section, dict) else section.items
            title = section.get("title", "") if isinstance(section, dict) else section.title
            if items:
                summary_parts.append(f"- {display_title(title, self.language)}: {len(items)}")

        return " ".join(summary_parts)

    def validate_markdown(self, content: str) -> bool:
        """Validate markdown content structure."""
        try:
            lines = content.split("\n")

            # Check for header
            if not any(line.startswith("# ") for line in lines):
                return False

            # Check for sections
            if not any(line.startswith("## ") for line in lines):
                return False

            # Check for evidence references
            evidence_refs = [
                line
                for line in lines
                # i18n-ok: validates rendered content in both report languages
                if ("Источник:" in line or "Source:" in line) and "evidence" in line
            ]
            if not evidence_refs:
                logger.warning("No evidence references found in markdown")
                return False

            return True

        except Exception as e:
            logger.warning("Markdown validation failed", error=str(e))
            return False

    def get_word_count(self, content: str) -> int:
        """Get word count of content."""
        return self._count_words(content)

    def format_evidence_reference(self, source_type: str, evidence_id: str) -> str:
        """Format evidence reference in required format."""
        return f"**{self._s['source_label']}:** {source_type}, evidence {evidence_id}"
