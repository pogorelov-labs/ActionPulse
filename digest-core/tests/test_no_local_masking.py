"""ADR-006: contact details are NOT masked in local artifacts.

The digest is a local, privacy-first artifact: email addresses stay visible
because they are non-sensitive in a corporate context and masking adds noise.
Phones/IDs are masked **in structured logs only** (see `test_log_redaction.py`),
never in the digest the user reads.

This invariant used to be covered by `test_end2end_no_pii.py`, which exercised it
through `gateway.process_digest` — a path `run.py` never called. That whole v2
surface is gone, so the assertions live here instead, against the assembler the
live pipeline actually uses (`run.py` -> `MarkdownAssembler.write_digest`).

Without this, a well-meaning "sanitize the output" change would silently reverse a
documented architecture decision and no test would object.
"""

from __future__ import annotations

from pathlib import Path

from digest_core.assemble.markdown import MarkdownAssembler
from digest_core.llm.schemas import Digest, Item, Section

CONTACTS = {
    "email": "ivan.petrov@corp.ru",
    "phone": "+7 495 123-45-67",
    "sender": "Ivan Petrov <ivan.petrov@corp.ru>",
}


def _digest() -> Digest:
    return Digest(
        prompt_version="extract_actions.en.v2",
        digest_date="2026-03-29",
        trace_id="t-adr006",
        sections=[
            Section(
                title="My actions",
                items=[
                    Item(
                        title=f"Call {CONTACTS['email']} on {CONTACTS['phone']}",
                        evidence_id="ev-1",
                        confidence=0.9,
                        source_ref={"type": "email", "msg_id": "msg-1"},
                        source_from=CONTACTS["sender"],
                        source_subject="Contract sign-off",
                        evidence_spans=[
                            {
                                "msg_id": "msg-1",
                                "quote": f"reach me at {CONTACTS['phone']}",
                            }
                        ],
                    )
                ],
            )
        ],
    )


def test_markdown_keeps_contact_details_verbatim(tmp_path: Path):
    out = tmp_path / "digest-2026-03-29.md"
    MarkdownAssembler().write_digest(_digest(), out)
    rendered = out.read_text(encoding="utf-8")

    # Only what the markdown actually renders: the item title (and the subject line).
    # `source_from` is reader-only enrichment and is deliberately not in the markdown —
    # the JSON artifact carries it, which the second test covers.
    assert CONTACTS["email"] in rendered
    assert CONTACTS["phone"] in rendered

    # A masking implementation would leave one of these behind.
    for marker in ("[REDACTED]", "[MASKED]", "x@x", "@***"):
        assert marker not in rendered, f"found masking marker {marker!r} in the digest"


def test_artifact_roundtrip_keeps_contact_details_verbatim():
    # The JSON artifact is what the reader, history browser and store all consume;
    # it must carry the same unmasked values as the markdown.
    payload = _digest().model_dump(exclude_none=True)
    item = payload["sections"][0]["items"][0]

    assert CONTACTS["email"] in item["title"]
    assert CONTACTS["phone"] in item["title"]
    assert item["source_from"] == CONTACTS["sender"]
    assert CONTACTS["phone"] in item["evidence_spans"][0]["quote"]
