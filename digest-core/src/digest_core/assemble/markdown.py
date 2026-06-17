"""
Markdown output assembler for digest data, localized per report.language.
"""

from pathlib import Path
import structlog

from digest_core.assemble.labels import (
    DEFAULT_LANGUAGE,
    FYI,
    MY_ACTIONS,
    confidence_text,
    display_title,
    report_strings,
    section_title,
    should_show_confidence,
)
from digest_core.llm.schemas import Digest, EnhancedDigest

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
                    item_email_subject = item.get("email_subject")
                    item_weak_evidence = item.get("weak_evidence", False)
                else:
                    item_title = item.title
                    item_due = item.due
                    item_confidence = item.confidence
                    item_evidence_id = item.evidence_id
                    item_source_ref = item.source_ref
                    item_email_subject = getattr(item, "email_subject", None)
                    item_weak_evidence = getattr(item, "weak_evidence", False)

                lines.append(f"### {i}. {item_title}")

                # Add due date if present
                if item_due:
                    lines.append(f"**{self._s['due_label']}:** {item_due}")

                # Add confidence only when it adds signal (borderline items, and
                # never alongside the weak-evidence marker — see labels.py).
                if should_show_confidence(item_confidence, item_weak_evidence):
                    lines.append(
                        f"**{self._s['confidence_label']}:**"
                        f" {self._format_confidence(item_confidence)}"
                    )

                # Add evidence reference (required format) with email subject
                source_type = item_source_ref.get("type", "unknown")
                if item_email_subject:
                    lines.append(
                        f'**{self._s["source_label"]}:** {source_type}, {self._s["subject_word"]} "{item_email_subject}", evidence {item_evidence_id}'
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
        """Format confidence score as capitalized report-language text."""
        return confidence_text(confidence, self.language).capitalize()

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

    def write_enhanced_digest(
        self,
        digest: EnhancedDigest,
        output_path: Path,
        is_partial: bool = False,
        partial_reason: str = None,
    ) -> None:
        """
        Write enhanced digest v2 data to Markdown file.

        Args:
            digest: EnhancedDigest instance
            output_path: Path to output file
            is_partial: Whether this is a partial digest (due to LLM failure)
            partial_reason: Reason for partial digest (e.g., "llm_json_error")
        """
        logger.info(
            "Writing enhanced Markdown digest v2",
            output_path=str(output_path),
            is_partial=is_partial,
        )

        try:
            # Generate markdown content
            markdown_content = self._generate_enhanced_markdown(
                digest, is_partial=is_partial, partial_reason=partial_reason
            )

            # Write to file
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            word_count = self._count_words(markdown_content)
            logger.info(
                "Enhanced Markdown digest written successfully",
                output_path=str(output_path),
                word_count=word_count,
                is_partial=is_partial,
            )

        except Exception as e:
            logger.error(
                "Failed to write enhanced Markdown digest",
                output_path=str(output_path),
                error=str(e),
            )
            raise

    def _generate_enhanced_markdown(
        self,
        digest: EnhancedDigest,
        is_partial: bool = False,
        partial_reason: str = None,
    ) -> str:
        """Generate markdown content from enhanced digest v2."""
        lines = []

        # Header
        lines.append(f"# {self._s['digest_header']} - {digest.digest_date}")
        lines.append(f"*Trace ID: {digest.trace_id}*")
        lines.append(f"*Timezone: {digest.timezone}*")
        lines.append(f"*Schema version: {digest.schema_version}*")
        lines.append("")

        # Add partial digest banner if applicable
        if is_partial:
            if partial_reason == "llm_json_error":
                lines.append("---")
                lines.append(f"⚠️ **{self._s['partial_json_title']}**")
                lines.append("")
                lines.append(self._s["partial_json_body"])
                lines.append(self._s["partial_note"])
                lines.append("---")
                lines.append("")
            elif partial_reason == "llm_processing_failed":
                lines.append("---")
                lines.append(f"⚠️ **{self._s['partial_llm_title']}**")
                lines.append("")
                lines.append(self._s["partial_llm_body"])
                lines.append(self._s["partial_note"])
                lines.append("---")
                lines.append("")
            else:
                lines.append("---")
                lines.append(f"⚠️ **{self._s['partial_generic_title']}**")
                lines.append("")
                lines.append(self._s["partial_generic_body"])
                lines.append(self._s["partial_note"])
                lines.append("---")
                lines.append("")

        # Check if digest is empty
        total_items = (
            len(digest.my_actions)
            + len(digest.others_actions)
            + len(digest.deadlines_meetings)
            + len(digest.risks_blockers)
            + len(digest.fyi)
        )

        if total_items == 0:
            lines.append(self._s["no_actions"])
            if digest.markdown_summary:
                lines.append("")
                lines.append("---")
                lines.append(digest.markdown_summary)
            return "\n".join(lines)

        # My actions
        if digest.my_actions:
            lines.append(f"## {section_title(MY_ACTIONS, self.language)}")
            lines.append("")
            for i, action in enumerate(digest.my_actions, 1):
                lines.append(f"### {i}. {action.title}")
                lines.append(f"**{self._s['description_label']}:** {action.description}")
                if action.due_date:
                    due_label = f" ({action.due_date_label})" if action.due_date_label else ""
                    lines.append(f"**{self._s['due_label']}:** {action.due_date}{due_label}")
                if action.due_date_normalized:
                    lines.append(f"**{self._s['date_iso_label']}:** {action.due_date_normalized}")
                lines.append(f"**{self._s['confidence_label']}:** {action.confidence}")
                # Render actors or owners (V2 vs V3)
                actors_or_owners = getattr(action, "owners", None) or getattr(
                    action, "actors", None
                )
                if actors_or_owners:
                    lines.append(f"**{self._s['owners_label']}:** {', '.join(actors_or_owners)}")
                if action.response_channel:
                    lines.append(
                        f"**{self._s['response_channel_label']}:** {action.response_channel}"
                    )
                # Add source with email subject
                email_subject = getattr(action, "email_subject", None)
                if email_subject:
                    lines.append(
                        f'**{self._s["source_label"]}:** {self._s["subject_word"]} "{email_subject}", evidence {action.evidence_id}'
                    )
                else:
                    lines.append(f"**{self._s['source_label']}:** Evidence {action.evidence_id}")
                lines.append(f'**{self._s["quote_label"]}:** "{action.quote}"')
                lines.append("")

        # Others' actions
        if digest.others_actions:
            lines.append(f"## {self._s['enhanced_others_header']}")
            lines.append("")
            for i, action in enumerate(digest.others_actions, 1):
                lines.append(f"### {i}. {action.title}")
                lines.append(f"**{self._s['description_label']}:** {action.description}")
                if action.due_date:
                    due_label = f" ({action.due_date_label})" if action.due_date_label else ""
                    lines.append(f"**{self._s['due_label']}:** {action.due_date}{due_label}")
                lines.append(f"**{self._s['confidence_label']}:** {action.confidence}")
                # Render actors or owners (V2 vs V3)
                actors_or_owners = getattr(action, "owners", None) or getattr(
                    action, "actors", None
                )
                if actors_or_owners:
                    lines.append(f"**{self._s['owners_label']}:** {', '.join(actors_or_owners)}")
                # Add source with email subject
                email_subject = getattr(action, "email_subject", None)
                if email_subject:
                    lines.append(
                        f'**{self._s["source_label"]}:** {self._s["subject_word"]} "{email_subject}", evidence {action.evidence_id}'
                    )
                else:
                    lines.append(f"**{self._s['source_label']}:** Evidence {action.evidence_id}")
                lines.append(f'**{self._s["quote_label"]}:** "{action.quote}"')
                lines.append("")

        # Deadlines and meetings
        if digest.deadlines_meetings:
            lines.append(f"## {self._s['enhanced_deadlines_header']}")
            lines.append("")
            for i, item in enumerate(digest.deadlines_meetings, 1):
                lines.append(f"### {i}. {item.title}")
                date_label = f" ({item.date_label})" if item.date_label else ""
                lines.append(f"**{self._s['datetime_label']}:** {item.date_time}{date_label}")
                if item.location:
                    lines.append(f"**{self._s['location_label']}:** {item.location}")
                if item.participants:
                    lines.append(
                        f"**{self._s['participants_label']}:** {', '.join(item.participants)}"
                    )
                # Add source with email subject (use getattr for V3 compatibility)
                email_subject = getattr(item, "email_subject", None)
                if email_subject:
                    lines.append(
                        f'**{self._s["source_label"]}:** {self._s["subject_word"]} "{email_subject}", evidence {item.evidence_id}'
                    )
                else:
                    lines.append(f"**{self._s['source_label']}:** Evidence {item.evidence_id}")
                lines.append(f'**{self._s["quote_label"]}:** "{item.quote}"')
                lines.append("")

        # Risks and blockers
        if digest.risks_blockers:
            lines.append(f"## {self._s['enhanced_risks_header']}")
            lines.append("")
            for i, item in enumerate(digest.risks_blockers, 1):
                lines.append(f"### {i}. {item.title}")
                lines.append(f"**{self._s['severity_label']}:** {item.severity}")
                lines.append(f"**{self._s['impact_label']}:** {item.impact}")
                # Render owners if present (V3)
                owners = getattr(item, "owners", None)
                if owners:
                    lines.append(f"**{self._s['owners_label']}:** {', '.join(owners)}")
                # Add source with email subject
                item_email_subject = getattr(item, "email_subject", None)
                if item_email_subject:
                    lines.append(
                        f'**{self._s["source_label"]}:** {self._s["subject_word"]} "{item_email_subject}", evidence {item.evidence_id}'
                    )
                else:
                    lines.append(f"**{self._s['source_label']}:** Evidence {item.evidence_id}")
                lines.append(f'**{self._s["quote_label"]}:** "{item.quote}"')
                lines.append("")

        # FYI items
        if digest.fyi:
            lines.append(
                f"## {section_title(FYI, self.language)}"
                + (" (FYI)" if self.language == "ru" else "")
            )
            lines.append("")
            for i, item in enumerate(digest.fyi, 1):
                lines.append(f"### {i}. {item.title}")
                if item.category:
                    lines.append(f"**{self._s['category_label']}:** {item.category}")
                # Add source with email subject
                if item.email_subject:
                    lines.append(
                        f'**{self._s["source_label"]}:** {self._s["subject_word"]} "{item.email_subject}", evidence {item.evidence_id}'
                    )
                else:
                    lines.append(f"**{self._s['source_label']}:** Evidence {item.evidence_id}")
                lines.append(f'**{self._s["quote_label"]}:** "{item.quote}"')
                lines.append("")

        # Statistics section - get from model_dump if available
        if hasattr(digest, "model_dump"):
            data_dict = digest.model_dump()
        else:
            data_dict = digest.__dict__ if hasattr(digest, "__dict__") else {}

        total_processed = data_dict.get("total_emails_processed", 0)
        emails_with_actions = data_dict.get("emails_with_actions", 0)

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

        # Add markdown summary if present
        if digest.markdown_summary:
            lines.append("---")
            lines.append(digest.markdown_summary)

        return "\n".join(lines)
