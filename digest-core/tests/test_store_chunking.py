"""Driver-independent tests for offset-aware store chunking."""

from __future__ import annotations

from digest_core.store.chunking import chunk_id, chunk_text


def test_empty_body_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_short_body_single_chunk_offsets_roundtrip():
    body = "approve the budget by Friday"
    chunks = chunk_text(body)
    assert len(chunks) == 1
    c = chunks[0]
    assert body[c.char_start : c.char_end] == c.text == body
    assert c.token_count > 0


def test_paragraphs_split_and_offsets_are_exact():
    body = "para one here.\n\n" + ("word " * 600) + "\n\npara three."
    chunks = chunk_text(body, max_tokens=50)
    assert len(chunks) >= 2
    # Every chunk's offsets map back to its exact text.
    for c in chunks:
        assert body[c.char_start : c.char_end] == c.text
    # Offsets are non-overlapping and ascending.
    for a, b in zip(chunks, chunks[1:]):
        assert a.char_end <= b.char_start


def test_long_single_sentence_is_hard_sliced():
    # Many words, no sentence/paragraph delimiters → one over-long "sentence"
    # that must be hard-sliced into bounded pieces covering the whole text.
    body = "alpha " * 600
    chunks = chunk_text(body, max_tokens=50)
    assert len(chunks) > 1
    assert "".join(c.text for c in chunks) == body


def test_chunk_id_deterministic_and_index_sensitive():
    a = chunk_id("urn:email:x", 0, "hello")
    assert a == chunk_id("urn:email:x", 0, "hello")
    assert a != chunk_id("urn:email:x", 1, "hello")  # index disambiguates
    assert a != chunk_id("urn:email:y", 0, "hello")
    assert a.startswith("ch_") and len(a) == 19
