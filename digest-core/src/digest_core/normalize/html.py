"""
HTML to text normalization with robust parsing.

Improvements:
- Lists (<ul>/<ol>) → markdown format ("- " / "1. ")
- Tables (<table>) → pipe-markdown
- Remove tracking pixels (1×1), hidden elements (display:none, visibility:hidden)
- Remove <style>, <script>, <svg>
- Unicode normalization (quotes, dashes, spaces)
- Fallback to text/plain on HTML parsing errors
"""

import re
import html
import unicodedata
from bs4 import BeautifulSoup
from typing import Optional, Tuple
import structlog

logger = structlog.get_logger()


class HTMLNormalizer:
    """HTML to text conversion with robust parsing."""

    def __init__(self, metrics=None):
        """
        Initialize HTMLNormalizer.

        Args:
            metrics: Optional MetricsCollector for tracking parse errors and removals
        """
        self.metrics = metrics

        # Unicode normalization mappings
        self.unicode_replacements = {
            # Quotes
            "\u201c": '"',  # Left double quote
            "\u201d": '"',  # Right double quote
            "\u2018": "'",  # Left single quote
            "\u2019": "'",  # Right single quote
            "\u201e": '"',  # Double low-9 quote
            "\u201a": "'",  # Single low-9 quote
            "\u00ab": '"',  # Left-pointing double angle quote
            "\u00bb": '"',  # Right-pointing double angle quote
            # Dashes and hyphens
            "\u2013": "-",  # En dash
            "\u2014": "--",  # Em dash
            "\u2015": "--",  # Horizontal bar
            "\u2212": "-",  # Minus sign
            # Spaces
            "\u00a0": " ",  # Non-breaking space
            "\u2002": " ",  # En space
            "\u2003": " ",  # Em space
            "\u2009": " ",  # Thin space
            "\u200b": "",  # Zero-width space
            "\u202f": " ",  # Narrow no-break space
            "\ufeff": "",  # Zero-width no-break space (BOM)
            # Ellipsis
            "\u2026": "...",  # Horizontal ellipsis
        }

    def html_to_text(
        self, html_content: str, fallback_plaintext: Optional[str] = None
    ) -> Tuple[str, bool]:
        """
        Convert HTML to clean text with fallback support.

        Args:
            html_content: Raw HTML content
            fallback_plaintext: Optional text/plain fallback if HTML parsing fails

        Returns:
            Tuple of (normalized_text, parse_success)
        """
        if not html_content:
            return "", True

        try:
            # Parse HTML
            soup = BeautifulSoup(html_content, "html.parser")

            # Step 1: Remove unwanted elements
            self._remove_unwanted_elements(soup)

            # Step 2: Remove hidden elements
            self._remove_hidden_elements(soup)

            # Step 3: Convert lists to markdown
            self._convert_lists_to_markdown(soup)

            # Step 4: Convert tables to markdown
            self._convert_tables_to_markdown(soup)

            # Step 5: Get text content
            text = soup.get_text()

            # Step 6: Clean up whitespace
            text = self._clean_whitespace(text)

            # Step 7: Decode HTML entities
            text = html.unescape(text)

            # Step 8: Normalize unicode characters
            text = self._normalize_unicode(text)

            if fallback_plaintext and self._should_use_plaintext_fallback(html_content, text):
                logger.info("Using text/plain fallback after low-quality HTML parse")
                if self.metrics:
                    self.metrics.record_html_parse_error("fallback_used")
                return self._normalize_unicode(fallback_plaintext), False

            logger.debug("HTML parsed successfully", text_length=len(text))
            return text, True

        except Exception as e:
            logger.warning("HTML parsing failed", error=str(e), error_type=type(e).__name__)

            if self.metrics:
                self.metrics.record_html_parse_error("bs4_error")

            # Fallback strategy
            if fallback_plaintext:
                logger.info("Using text/plain fallback")
                if self.metrics:
                    self.metrics.record_html_parse_error("fallback_used")
                return self._normalize_unicode(fallback_plaintext), False

            # Last resort: regex-based HTML tag removal
            logger.info("Using regex fallback")
            if self.metrics:
                self.metrics.record_html_parse_error("malformed_html")
            text = re.sub(r"<[^>]+>", "", html_content)
            text = html.unescape(text)
            text = self._normalize_unicode(text)
            return text, False

    def _should_use_plaintext_fallback(self, html_content: str, parsed_text: str) -> bool:
        """Detect cases where HTML parsing returned the original garbage unchanged."""
        normalized_source = self._normalize_unicode(
            self._clean_whitespace(html.unescape(html_content))
        )
        normalized_text = parsed_text.strip()

        if not normalized_text:
            return True

        # Case 1: BS4 passed the original garbage through unchanged
        if normalized_text == normalized_source and ("<" in html_content or ">" in html_content):
            return True

        # Case 2: No real text content outside tags (including unclosed/partial tags)
        text_outside_tags = re.sub(r"<[^>]*", "", html_content)
        text_outside_tags = re.sub(r">", "", text_outside_tags).strip()
        if not re.search(r"[A-Za-z\u0400-\u04FF]{2,}", text_outside_tags) and (
            "<" in html_content or ">" in html_content
        ):
            return True

        return False

    def _remove_unwanted_elements(self, soup):
        """Remove script, style, svg, and tracking elements."""
        removed_count = 0

        # Remove <script>, <style>, <svg>
        for element in soup(["script", "style", "svg"]):
            element.decompose()
            removed_count += 1

        if removed_count > 0 and self.metrics:
            self.metrics.record_html_hidden_removed("style_script_svg", removed_count)

        # Remove tracking pixels
        pixel_count = 0
        for img in soup.find_all("img"):
            src = img.get("src", "")
            width = img.get("width", "")
            height = img.get("height", "")

            # Remove inline attachments (cid:)
            if src.startswith("cid:"):
                img.decompose()
                pixel_count += 1
                continue

            # Remove 1x1 tracking pixels
            try:
                if (width and int(width) <= 1) or (height and int(height) <= 1):
                    img.decompose()
                    pixel_count += 1
            except (ValueError, TypeError):
                # Non-numeric width/height, skip
                pass

        if pixel_count > 0:
            logger.debug("Removed tracking pixels", count=pixel_count)
            if self.metrics:
                self.metrics.record_html_hidden_removed("tracking_pixel", pixel_count)

    def _remove_hidden_elements(self, soup):
        """Remove elements with display:none or visibility:hidden."""
        hidden_count = 0

        # Find elements with style attribute
        for element in soup.find_all(style=True):
            style = element.get("style", "").lower()

            # Check for display:none or visibility:hidden
            if (
                "display:none" in style.replace(" ", "")
                or "display: none" in style
                or "visibility:hidden" in style.replace(" ", "")
                or "visibility: hidden" in style
            ):
                element.decompose()
                hidden_count += 1

        if hidden_count > 0:
            logger.debug("Removed hidden elements", count=hidden_count)
            if self.metrics:
                self.metrics.record_html_hidden_removed("display_none", hidden_count)

    def _convert_lists_to_markdown(self, soup):
        """Convert <ul> and <ol> to markdown format."""
        # Convert unordered lists (<ul>)
        for ul in soup.find_all("ul"):
            items = []
            for li in ul.find_all("li", recursive=False):  # Only direct children
                item_text = li.get_text().strip()
                if item_text:
                    items.append(f"- {item_text}")

            if items:
                # Replace <ul> with markdown text
                markdown_text = "\n".join(items) + "\n"
                ul.replace_with(soup.new_string(markdown_text))

        # Convert ordered lists (<ol>)
        for ol in soup.find_all("ol"):
            items = []
            for idx, li in enumerate(ol.find_all("li", recursive=False), 1):
                item_text = li.get_text().strip()
                if item_text:
                    items.append(f"{idx}. {item_text}")

            if items:
                # Replace <ol> with markdown text
                markdown_text = "\n".join(items) + "\n"
                ol.replace_with(soup.new_string(markdown_text))

    def _convert_tables_to_markdown(self, soup):
        """Convert <table> to pipe-markdown format (simplified ASCII table)."""
        for table in soup.find_all("table"):
            try:
                rows = []

                # Extract headers
                headers = []
                header_row = table.find("thead")
                if header_row:
                    for th in header_row.find_all(["th", "td"]):
                        headers.append(th.get_text().strip())

                # If no thead, try first tr
                if not headers:
                    first_row = table.find("tr")
                    if first_row:
                        for th in first_row.find_all(["th", "td"]):
                            headers.append(th.get_text().strip())

                # Extract data rows
                tbody = table.find("tbody") or table
                for tr in tbody.find_all("tr"):
                    cells = []
                    for td in tr.find_all(["td", "th"]):
                        cells.append(td.get_text().strip())
                    if cells and cells != headers:  # Skip header row if already processed
                        rows.append(cells)

                # Build markdown table
                if headers or rows:
                    markdown_lines = []

                    truncated_cells = 0

                    # Add header
                    if headers:
                        # Limit column width to 30 chars
                        headers_truncated = [h[:30] for h in headers]
                        truncated_cells += sum(1 for h in headers if len(h) > 30)
                        markdown_lines.append("| " + " | ".join(headers_truncated) + " |")
                        markdown_lines.append(
                            "|" + "|".join(["-" * (len(h) + 2) for h in headers_truncated]) + "|"
                        )

                    # Add rows (limit to first 10 rows to avoid huge tables)
                    for row in rows[:10]:
                        # Pad row to match header length
                        if headers:
                            row = row[: len(headers)]  # Trim extra columns
                            row += [""] * (len(headers) - len(row))  # Pad missing columns

                        # Truncate cells
                        row_truncated = [cell[:30] for cell in row]
                        truncated_cells += sum(1 for cell in row if len(cell) > 30)
                        markdown_lines.append("| " + " | ".join(row_truncated) + " |")

                    if len(rows) > 10:
                        markdown_lines.append(f"... ({len(rows) - 10} more rows)")
                    if truncated_cells:
                        logger.debug("html_table_cells_truncated", cells=truncated_cells, width=30)

                    markdown_text = "\n".join(markdown_lines) + "\n"
                    table.replace_with(soup.new_string(markdown_text))
                else:
                    # Empty table, just remove
                    table.decompose()

            except Exception as e:
                logger.warning("Table conversion failed", error=str(e))
                # Keep original table text
                pass

    def _clean_whitespace(self, text: str) -> str:
        """Clean up excessive whitespace."""
        # Split into lines and clean each
        lines = (line.strip() for line in text.splitlines())

        # Remove empty lines and excessive spaces
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))

        # Join with single space
        text = " ".join(chunk for chunk in chunks if chunk)

        return text

    def _normalize_unicode(self, text: str) -> str:
        """Normalize unicode characters (quotes, dashes, spaces)."""
        if not text:
            return text

        # Apply custom mappings
        for unicode_char, replacement in self.unicode_replacements.items():
            text = text.replace(unicode_char, replacement)

        # Normalize unicode form (NFC = canonical composition)
        text = unicodedata.normalize("NFC", text)

        return text

    def truncate_text(self, text: str, max_bytes: int = 200000) -> str:
        """Truncate text if it exceeds size limit."""
        if len(text.encode("utf-8")) <= max_bytes:
            return text

        # Truncate to fit within byte limit
        truncated = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")

        # Add truncation marker
        truncated += "\n[TRUNCATED]"

        logger.warning("Text truncated", original_size=len(text), truncated_size=len(truncated))

        return truncated
