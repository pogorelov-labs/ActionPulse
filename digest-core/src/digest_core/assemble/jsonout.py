"""
JSON output assembler for digest data with strict schema validation.
"""

import json
from pathlib import Path
from typing import Dict, Any
import structlog

from digest_core.llm.schemas import Digest, Section, Item

logger = structlog.get_logger()


class JSONAssembler:
    """Assemble digest data into JSON output with strict schema compliance."""

    def __init__(self):
        self.indent = 2
        self.ensure_ascii = False

    def write_digest(self, digest_data: Digest, output_path: Path) -> None:
        """Write digest data to JSON file with strict schema validation."""
        logger.info("Writing JSON digest", output_path=str(output_path))

        try:
            # Validate digest data against schema
            if not self.validate_digest(digest_data):
                raise ValueError("Digest data does not conform to schema")

            # Convert to dict for JSON serialization
            digest_dict = self._digest_to_dict(digest_data)

            # Write to file with proper encoding
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(digest_dict, f, indent=self.indent, ensure_ascii=self.ensure_ascii)

            logger.info(
                "JSON digest written successfully",
                output_path=str(output_path),
                sections_count=len(digest_data.sections),
                total_items=sum(len(section.items) for section in digest_data.sections),
            )

        except Exception as e:
            logger.error(
                "Failed to write JSON digest",
                output_path=str(output_path),
                error=str(e),
            )
            raise

    def _digest_to_dict(self, digest_data: Digest) -> Dict[str, Any]:
        """Convert Digest object to dictionary with strict schema compliance."""
        result = {
            "schema_version": digest_data.schema_version,
            "prompt_version": digest_data.prompt_version,
            "digest_date": digest_data.digest_date,
            "trace_id": digest_data.trace_id,
            "sections": [
                {
                    "title": section.title,
                    "items": [
                        {
                            "title": item.title,
                            "due": item.due,
                            "evidence_id": item.evidence_id,
                            "confidence": item.confidence,
                            "source_ref": item.source_ref,
                            "email_subject": item.email_subject,
                            "email_from": item.email_from,
                        }
                        for item in section.items
                    ],
                }
                for section in digest_data.sections
            ],
            "total_emails_processed": getattr(digest_data, "total_emails_processed", None),
            "emails_with_actions": getattr(digest_data, "emails_with_actions", None),
        }
        return result

    def read_digest(self, input_path: Path) -> Digest:
        """Read digest data from JSON file with schema validation."""
        logger.info("Reading JSON digest", input_path=str(input_path))

        try:
            with open(input_path, "r", encoding="utf-8") as f:
                digest_dict = json.load(f)

            # Convert to Digest object with validation
            digest_data = self._dict_to_digest(digest_dict)

            # Validate the reconstructed digest
            if not self.validate_digest(digest_data):
                raise ValueError("Read digest data does not conform to schema")

            logger.info(
                "JSON digest read successfully",
                input_path=str(input_path),
                sections_count=len(digest_data.sections),
            )

            return digest_data

        except Exception as e:
            logger.error("Failed to read JSON digest", input_path=str(input_path), error=str(e))
            raise

    def _dict_to_digest(self, digest_dict: Dict[str, Any]) -> Digest:
        """Convert dictionary to Digest object with validation."""
        sections = []
        for section_dict in digest_dict.get("sections", []):
            items = []
            for item_dict in section_dict.get("items", []):
                item = Item(
                    title=item_dict["title"],
                    due=item_dict.get("due"),
                    evidence_id=item_dict["evidence_id"],
                    confidence=item_dict["confidence"],
                    source_ref=item_dict["source_ref"],
                    email_subject=item_dict.get("email_subject"),
                    email_from=item_dict.get("email_from"),
                )
                items.append(item)

            section = Section(title=section_dict["title"], items=items)
            sections.append(section)

        return Digest(
            schema_version=digest_dict.get("schema_version", "1.0"),
            prompt_version=digest_dict.get("prompt_version", "extract_actions.v1"),
            digest_date=digest_dict["digest_date"],
            trace_id=digest_dict["trace_id"],
            sections=sections,
            total_emails_processed=digest_dict.get("total_emails_processed", 0),
            emails_with_actions=digest_dict.get("emails_with_actions", 0),
        )

    def validate_digest(self, digest_data: Digest) -> bool:
        """Validate digest data structure against schema."""
        try:
            # Check required top-level fields
            if not digest_data.schema_version or not digest_data.prompt_version:
                logger.warning("Missing schema_version or prompt_version")
                return False

            if not digest_data.digest_date or not digest_data.trace_id:
                logger.warning("Missing digest_date or trace_id")
                return False

            # Validate date format (YYYY-MM-DD)
            try:
                from datetime import datetime

                datetime.strptime(digest_data.digest_date, "%Y-%m-%d")
            except ValueError:
                logger.warning("Invalid digest_date format", date=digest_data.digest_date)
                return False

            # Check sections
            if not isinstance(digest_data.sections, list):
                logger.warning("Sections must be a list")
                return False

            for section in digest_data.sections:
                if not self._validate_section(section):
                    return False

            return True

        except Exception as e:
            logger.warning("Digest validation failed", error=str(e))
            return False

    def _validate_section(self, section: Section) -> bool:
        """Validate a section against schema."""
        if not section.title or not isinstance(section.title, str):
            logger.warning("Section title must be a non-empty string")
            return False

        if not isinstance(section.items, list):
            logger.warning("Section items must be a list")
            return False

        # Validate each item
        for item in section.items:
            if not self._validate_item(item):
                return False

        return True

    def _validate_item(self, item: Item) -> bool:
        """Validate an item against schema."""
        # Required fields
        if not item.title or not isinstance(item.title, str):
            logger.warning("Item title must be a non-empty string")
            return False

        if not item.evidence_id or not isinstance(item.evidence_id, str):
            logger.warning("Item evidence_id must be a non-empty string")
            return False

        # Confidence validation
        if not isinstance(item.confidence, (int, float)) or not (0 <= item.confidence <= 1):
            logger.warning(
                "Item confidence must be a number between 0 and 1",
                confidence=item.confidence,
            )
            return False

        # Source reference validation
        if not isinstance(item.source_ref, dict):
            logger.warning("Item source_ref must be a dictionary")
            return False

        if "type" not in item.source_ref:
            logger.warning("Item source_ref must contain 'type' field")
            return False

        # Optional fields validation
        if item.due is not None and not isinstance(item.due, str):
            logger.warning("Item due must be a string or None")
            return False

        return True

    def get_schema_info(self) -> Dict[str, Any]:
        """Get information about the JSON schema."""
        return {
            "schema_version": "1.0",
            "required_fields": [
                "schema_version",
                "prompt_version",
                "digest_date",
                "trace_id",
                "sections",
            ],
            "optional_fields": ["total_emails_processed", "emails_with_actions"],
            "section_required_fields": ["title", "items"],
            "item_required_fields": [
                "title",
                "evidence_id",
                "confidence",
                "source_ref",
            ],
            "item_optional_fields": ["due", "email_subject", "email_from"],
            "date_format": "YYYY-MM-DD",
            "confidence_range": [0, 1],
        }
