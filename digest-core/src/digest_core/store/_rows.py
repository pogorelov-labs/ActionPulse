"""Shared row-decoding helpers for the store retrieval / insight modules."""

from __future__ import annotations

import json
from typing import List


def decode_json_list(raw, *, lowercase: bool = False, drop_empty: bool = False) -> List[str]:
    """Decode a JSON-array DB column to a list of strings.

    Tolerant: a missing / non-string / garbage / non-list value yields ``[]`` (the column
    must never crash an insight query). ``lowercase`` lower-cases each item; ``drop_empty``
    skips falsy items. The single idiom shared by carryover/pending (recipient sets:
    lower-cased, empties dropped) and retrieve (raw lists).
    """
    if not raw:
        return []
    try:
        vals = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(vals, list):
        return []
    out: List[str] = []
    for v in vals:
        if drop_empty and not v:
            continue
        out.append(str(v).lower() if lowercase else str(v))
    return out
