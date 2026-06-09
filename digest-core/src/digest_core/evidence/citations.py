"""
Citation builder and validator for extractive evidence traceability.

Responsibilities:
1. Build Citation objects from evidence chunks with msg_id and offsets
2. Validate offsets against normalized email bodies
3. Calculate checksums for integrity verification
"""

import hashlib
import re
import structlog
from typing import List, Dict, Optional, Tuple
from digest_core.llm.schemas import Citation
from digest_core.evidence.split import EvidenceChunk

logger = structlog.get_logger()


class CitationBuilder:
    """Build and validate citations from evidence chunks."""

    def __init__(self, normalized_messages_map: Dict[str, str]):
        """
        Initialize CitationBuilder.

        Args:
            normalized_messages_map: Dict mapping msg_id -> normalized email body text
                                     (AFTER html→text + email_cleaner)
        """
        self.normalized_messages_map = normalized_messages_map
        self.checksums_cache: Dict[str, str] = {}

    def build_citation(self, chunk: EvidenceChunk) -> Optional[Citation]:
        """
        Build a Citation from an evidence chunk.

        Args:
            chunk: EvidenceChunk with content and source_ref

        Returns:
            Citation object or None if unable to build
        """
        try:
            # Extract msg_id from source_ref
            msg_id = chunk.source_ref.get("msg_id")
            if not msg_id:
                logger.warning("Missing msg_id in source_ref", evidence_id=chunk.evidence_id)
                return None

            # Get normalized message body
            normalized_body = self.normalized_messages_map.get(msg_id)
            if not normalized_body:
                logger.warning(
                    "Normalized body not found for msg_id",
                    msg_id=msg_id,
                    evidence_id=chunk.evidence_id,
                )
                return None

            # Find chunk content in normalized body
            start_offset = normalized_body.find(chunk.content)
            if start_offset == -1:
                # Try fuzzy matching (handle whitespace differences)
                start_offset = self._fuzzy_find(chunk.content, normalized_body)

            if start_offset == -1:
                logger.warning(
                    "Chunk content not found in normalized body",
                    evidence_id=chunk.evidence_id,
                    msg_id=msg_id,
                    chunk_preview=chunk.content[:100],
                )
                return None

            end_offset = start_offset + len(chunk.content)

            # Create preview (truncate if too long)
            preview = chunk.content[:200]

            # Calculate checksum
            checksum = self._get_checksum(msg_id, normalized_body)

            citation = Citation(
                msg_id=msg_id,
                start=start_offset,
                end=end_offset,
                preview=preview,
                checksum=checksum,
            )

            return citation

        except Exception as e:
            logger.error("Failed to build citation", evidence_id=chunk.evidence_id, error=str(e))
            return None

    def build_citations_for_chunks(self, chunks: List[EvidenceChunk]) -> List[Citation]:
        """
        Build citations for multiple evidence chunks.

        Args:
            chunks: List of evidence chunks

        Returns:
            List of successfully built citations
        """
        citations = []

        for chunk in chunks:
            citation = self.build_citation(chunk)
            if citation:
                citations.append(citation)

        logger.info(
            "Built citations",
            total_chunks=len(chunks),
            successful_citations=len(citations),
        )

        return citations

    def _fuzzy_find(self, needle: str, haystack: str) -> int:
        """
        Fuzzy find needle in haystack (handle whitespace differences).

        Args:
            needle: Text to find
            haystack: Text to search in

        Returns:
            Start offset or -1 if not found
        """
        # Normalize whitespace for comparison
        needle_normalized = " ".join(needle.split())
        haystack_normalized = " ".join(haystack.split())

        fuzzy_start = haystack_normalized.find(needle_normalized)
        if fuzzy_start == -1:
            return -1

        # Map back to original haystack offset (approximate)
        # Count words up to fuzzy_start
        words_before = len(haystack_normalized[:fuzzy_start].split())

        # Count chars in original haystack for same number of words
        original_offset = 0
        word_count = 0
        for i, char in enumerate(haystack):
            if char.isspace():
                if i > 0 and not haystack[i - 1].isspace():
                    word_count += 1
                    if word_count >= words_before:
                        original_offset = i + 1
                        break
            elif word_count == 0 and i == 0:
                continue

        return original_offset if original_offset < len(haystack) else -1

    def _get_checksum(self, msg_id: str, normalized_body: str) -> str:
        """
        Calculate SHA-256 checksum of normalized email body.

        Args:
            msg_id: Message ID
            normalized_body: Normalized email body text

        Returns:
            SHA-256 hex digest
        """
        if msg_id in self.checksums_cache:
            return self.checksums_cache[msg_id]

        checksum = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
        self.checksums_cache[msg_id] = checksum

        return checksum


class CitationValidator:
    """Validate citation offsets against source text."""

    def __init__(self, normalized_messages_map: Dict[str, str]):
        """
        Initialize CitationValidator.

        Args:
            normalized_messages_map: Dict mapping msg_id -> normalized email body text
        """
        self.normalized_messages_map = normalized_messages_map
        self.validation_errors: List[Dict] = []

    def validate_citation(self, citation: Citation) -> Tuple[bool, Optional[str]]:
        """
        Validate a single citation.

        Args:
            citation: Citation to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Get normalized body
            normalized_body = self.normalized_messages_map.get(citation.msg_id)
            if not normalized_body:
                return False, f"Normalized body not found for msg_id={citation.msg_id}"

            # Validate offset bounds
            if citation.start < 0:
                return False, f"Invalid start offset: {citation.start} < 0"

            if citation.end <= citation.start:
                return False, f"Invalid end offset: {citation.end} <= {citation.start}"

            if citation.end > len(normalized_body):
                return (
                    False,
                    f"End offset {citation.end} exceeds body length {len(normalized_body)}",
                )

            # Extract text at offset
            extracted_text = normalized_body[citation.start : citation.end]

            # Validate preview matches
            preview_match = extracted_text[:200] == citation.preview[:200]
            if not preview_match:
                # Try fuzzy match (whitespace differences)
                extracted_normalized = " ".join(extracted_text.split())
                preview_normalized = " ".join(citation.preview.split())
                preview_match = extracted_normalized[:200] == preview_normalized[:200]

            if not preview_match:
                return (
                    False,
                    f"Preview mismatch at offset {citation.start}:{citation.end}",
                )

            # Validate checksum if provided
            if citation.checksum:
                expected_checksum = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
                if citation.checksum != expected_checksum:
                    return False, f"Checksum mismatch for msg_id={citation.msg_id}"

            return True, None

        except Exception as e:
            return False, f"Validation exception: {str(e)}"

    def validate_citations(self, citations: List[Citation], strict: bool = True) -> bool:
        """
        Validate multiple citations.

        Args:
            citations: List of citations to validate
            strict: If True, fail on first error; if False, collect all errors

        Returns:
            True if all citations valid, False otherwise
        """
        self.validation_errors = []
        all_valid = True

        for i, citation in enumerate(citations):
            is_valid, error = self.validate_citation(citation)

            if not is_valid:
                all_valid = False
                error_info = {
                    "index": i,
                    "msg_id": citation.msg_id,
                    "start": citation.start,
                    "end": citation.end,
                    "error": error,
                }
                self.validation_errors.append(error_info)

                logger.error("Citation validation failed", **error_info)

                if strict:
                    return False

        if all_valid:
            logger.info("All citations validated successfully", count=len(citations))
        else:
            logger.warning(
                "Citation validation failed",
                total=len(citations),
                errors=len(self.validation_errors),
            )

        return all_valid

    def get_validation_errors(self) -> List[Dict]:
        """Get list of validation errors from last validate_citations() call."""
        return self.validation_errors


def enrich_item_with_citations(
    item: any, evidence_chunks: List[EvidenceChunk], citation_builder: CitationBuilder
) -> None:
    """
    Enrich a digest item with citations.

    Args:
        item: Digest item (ActionItem, DeadlineMeeting, etc.)
        evidence_chunks: All evidence chunks
        citation_builder: CitationBuilder instance

    Mutates item.citations in-place.
    """
    # Find chunk by evidence_id
    matching_chunks = [c for c in evidence_chunks if c.evidence_id == item.evidence_id]

    if not matching_chunks:
        logger.warning("No matching chunks for evidence_id", evidence_id=item.evidence_id)
        return

    # Build citations for matching chunks
    for chunk in matching_chunks:
        citation = citation_builder.build_citation(chunk)
        if citation:
            item.citations.append(citation)

    if not item.citations:
        logger.warning("Failed to build any citations for item", evidence_id=item.evidence_id)


def reselect_span(title: str, body: str) -> str:
    """Non-generative span re-selection (PR11).

    Pick the verbatim sentence of ``body`` with the most token overlap with ``title``.
    The result is guaranteed to be a substring of ``body`` (no generation), so repair
    stays replay-safe and offset-verifiable. Returns "" when nothing overlaps.
    """
    if not title or not body:
        return ""
    title_tokens = set(_word_tokens(title))
    if not title_tokens:
        return ""
    best_span, best_overlap = "", 0
    for sentence in _split_sentences(body):
        overlap = len(title_tokens & set(_word_tokens(sentence)))
        if overlap > best_overlap:
            best_span, best_overlap = sentence.strip(), overlap
    return best_span if best_overlap > 0 else ""


def _word_tokens(text: str) -> List[str]:
    return re.findall(r"\w+", (text or "").lower())


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?\n])\s+", text or "")
    return [part for part in parts if part.strip()]
