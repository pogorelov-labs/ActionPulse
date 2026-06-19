"""store/_rows.py decode_json_list — the shared JSON-array column decoder (pure; no extras)."""

from digest_core.store._rows import decode_json_list


def test_decode_json_list_variants():
    assert decode_json_list('["A", "b"]') == ["A", "b"]
    assert decode_json_list('["A", "b"]', lowercase=True) == ["a", "b"]  # recipients lower-case
    assert decode_json_list('["a", ""]', drop_empty=True) == ["a"]  # recipients drop empties


def test_decode_json_list_tolerant():
    # A missing / garbage / non-list column must never crash an insight query.
    for bad in ("", None, "{not json", '"a string"', '{"k": 1}'):
        assert decode_json_list(bad) == []
