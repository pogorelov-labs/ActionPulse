"""Pure logic for the Mattermost DM-scope consent UX.

The wizard (``setup_wizard.py``) and the settings menu (``ui/menu.py``) share
the same privacy ladder and the same consent rules. To keep the interactive
glue thin and testable, all the decisions live here as pure functions and the
config-file write is a single read-modify-write helper. Nothing in this module
prompts, prints, or touches a TTY.

The ladder (rungs, low → high privacy exposure):

    off → own_posts_only → selected → all

``off`` and ``own_posts_only`` never expose a colleague's authored text to the
LLM, so they never require consent. ``selected`` and ``all`` feed third-party
PII (counterparty DM text) to the LLM and are refused at config-load time
(``MattermostSourceConfig`` model validator) unless ``dm_consent_acknowledged``
is True — so this module is the *only* sanctioned way to arm them, always with
a fresh timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml

# Scope ladder, ordered low → high privacy exposure. Index = "exposure rank":
# a move to a strictly higher index that lands in a consent scope is an
# escalation. Kept here so the wizard/menu pickers and the rules stay in sync.
DM_SCOPES: tuple[str, ...] = ("off", "own_posts_only", "selected", "all")

# Scopes that feed counterparty (third-party) text to the LLM and therefore
# require an explicit, timestamped consent acknowledgement.
CONSENT_SCOPES: frozenset[str] = frozenset({"selected", "all"})

# Re-affirm consent when the recorded acknowledgement is older than this.
DM_CONSENT_STALE_DAYS = 180

# Display labels for the four-rung picker (wizard + menu share these).
DM_SCOPE_LABELS: tuple[tuple[str, str], ...] = (
    ("off", "Off — no direct messages"),
    (
        "own_posts_only",
        "My posts only — only the messages I sent",
    ),
    ("selected", "Selected partners — pick whose DMs to include"),
    ("all", "All DMs — every DM the token can read"),
)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None.

    Tolerant of a trailing ``Z`` and naive timestamps (assumed UTC). Returns
    None for missing/blank/garbage input so callers can treat unparseable
    exactly like missing (both mean "we cannot trust this ack").
    """
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def dm_consent_is_stale(
    acknowledged_at: Optional[str],
    now: datetime,
    stale_days: int = DM_CONSENT_STALE_DAYS,
) -> bool:
    """True when a recorded consent ack is too old (or absent/unparseable).

    A missing or garbage timestamp counts as stale: we cannot prove the owner
    consented recently, so the caller must re-affirm. ``now`` is injected (no
    hidden clock) so the rule is deterministic under test.
    """
    parsed = _parse_iso(acknowledged_at)
    if parsed is None:
        return True
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = now - parsed
    return age.days >= stale_days


def dm_consent_required(
    current_scope: str,
    new_scope: str,
    acknowledged: bool,
    acknowledged_at: Optional[str],
    now: datetime,
    stale_days: int = DM_CONSENT_STALE_DAYS,
) -> bool:
    """Decide whether a scope change must fire the consent panel.

    Rules (see module docstring + the consent UX spec):

    * ``new_scope`` in ('off', 'own_posts_only') → never (no third-party PII).
    * Moving INTO a consent scope ('selected'/'all') from a non-consenting
      state (current off/own_posts_only, or not currently acknowledged) →
      required.
    * Escalation selected → all → required.
    * Re-entering 'all' (current=='all', new=='all') → required (no silent
      re-arm; the menu pairs this with a default-No re-confirm).
    * Already in a consent scope, acknowledged AND fresh, not escalating →
      not required.
    * Acknowledged but STALE (or missing/unparseable while in a consent
      scope) → required (re-affirm).

    This function does NOT decide the extra "Ingest ALL DMs?" confirm — that is
    a separate gate the caller applies whenever ``new_scope == 'all'``.

    Editing the partner list within 'selected' is NOT a scope change and never
    reaches here, so it never fires consent — by design. The acknowledgement is
    for the 'selected' *exposure* ("some chosen partners' DM text reaches the
    LLM"), not for specific names; adding/removing a partner is an editorial
    change, and the per-post quote cap (``dm_max_quote_chars``) bounds every
    counterparty's verbatim text regardless of who is on the list. (A
    hand-edited config.yaml that only swaps allowlist entries under a still-fresh
    consent is therefore accepted — the model validator gates the *scope*, not
    the allowlist contents.)
    """
    if new_scope not in CONSENT_SCOPES:
        return False

    # Re-entering 'all' always re-confirms — no silent re-arm of the widest scope.
    if current_scope == "all" and new_scope == "all":
        return True

    # Escalation up the ladder into/within the consent scopes (e.g. selected → all).
    if DM_SCOPES.index(new_scope) > DM_SCOPES.index(current_scope):
        return True

    # Coming from a non-consenting state (off/own_posts_only) or an un-acked one.
    if current_scope not in CONSENT_SCOPES or not acknowledged:
        return True

    # Already armed in a consent scope at the same rung: re-affirm only if stale.
    return dm_consent_is_stale(acknowledged_at, now, stale_days=stale_days)


def normalize_partners(raw: object) -> List[str]:
    """Normalize a partner allowlist: strip, drop blanks, preserve order.

    Accepts a comma-separated string (free-text wizard/menu entry) or an
    already-split list. Whitespace is trimmed off every entry, empty entries
    are dropped, and order is preserved (no dedupe — the adapter matcher is
    idempotent and the owner may have intentional near-duplicates like a
    @username and an email for the same person).
    """
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return []
    return [p.strip() for p in parts if isinstance(p, str) and p.strip()]


def now_iso() -> str:
    """Current instant as an ISO-8601 UTC timestamp (the consent ack stamp)."""
    return datetime.now(timezone.utc).isoformat()


def update_mm_source_dm(config_path: Path, **fields: object) -> dict:
    """Read-modify-write the ``mm_source`` DM keys in ``config_path``.

    Loads the YAML (or starts from ``{}`` when the file is absent or empty),
    sets only the given ``mm_source.<key>`` values, and dumps it back —
    preserving every other section and key untouched. Returns the written
    document for convenience/tests.

    Refuses to persist an unloadable state: if the resulting ``mm_source`` would
    be a consent scope ('selected'/'all') without ``dm_consent_acknowledged``
    True, raises ``ValueError`` BEFORE writing (mirrors the config model
    validator, so the menu/wizard can never leave a config that ``Config()``
    would then reject on the next load).

    Only the ``dm_*`` keys are intended here; the helper is generic over keys
    so the same call can clear the consent fields (e.g. on a downgrade to off).
    """
    doc: dict = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            doc = loaded

    mm_source = doc.get("mm_source")
    if not isinstance(mm_source, dict):
        mm_source = {}

    for key, value in fields.items():
        mm_source[key] = value

    # Guard: never write a counterparty-exposing scope without consent — that
    # config would be unloadable (Config() raises). Validate the merged state.
    effective_scope = mm_source.get("dm_scope", "off")
    if effective_scope in CONSENT_SCOPES and not mm_source.get("dm_consent_acknowledged", False):
        raise ValueError(
            f"refusing to write mm_source.dm_scope={effective_scope!r} without "
            "dm_consent_acknowledged=true (would be unloadable)"
        )

    doc["mm_source"] = mm_source

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return doc
