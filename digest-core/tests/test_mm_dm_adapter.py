"""P3: offline Mattermost DM (direct-message) ingestion adapter tests.

Everything here is synthetic, fully offline, and payload-free. A FAKE http
client returns hand-built v4 API JSON; nothing touches the corp network. The
real adapter is validated LIVE only from inside the corp network (ADR-012).

What is asserted here (design §2.2 DMs + §6 privacy ladder):
  * dm_scope='off' — D/G channels contribute NOTHING and are NEVER paged; a
    group-DM with an @owner mention yields ZERO messages (the pre-existing
    mention-slice leak is CLOSED). OP-channel mentions/allowlist still work.
  * dm_scope='own_posts_only' — only the owner's OWN DM posts are kept
    (addressed_to_me=False); counterparty posts dropped entirely.
  * dm_scope='selected' — only D/G channels whose counterparty matches the
    dm_allowlist are paged (allowlist-BEFORE-GET); counterparty posts are kept
    (addressed_to_me=True), owner posts kept — both in full.
  * member-match by user_id / @username / bare username / email (case-insens).
  * 'D' counterparty derived from the channel-name split; 'G' members via
    get_channel_members; group-DM kept iff ANY non-owner member matches.
  * full harvest: DM text (counterparty + owner) is kept in full — no quote cap.
  * dm_scope='all' — every active D/G ingested (full text).
  * mm_channel_type set to 'D'/'G'/'O'/'P'; None for email; NOT in the hash.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core.config import MattermostSourceConfig, TimeConfig
from digest_core.ingest.mattermost import (
    MattermostReadClient,
    MattermostSourceAdapter,
    _channel_kind,
    _dm_counterparty_ids_from_name,
    _is_dm_channel,
    _is_self_dm,
    _normalize_dm_allowlist,
    _user_identity_tokens,
)

# ---------------------------------------------------------------------------
# Window + owner identity (a fixed UTC calendar day for deterministic ms math).
# ---------------------------------------------------------------------------

_DIGEST_DATE = "2026-03-29"
_MID_DAY_MS = int(datetime(2026, 3, 29, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
_BEFORE_WINDOW_MS = int(datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

_OWNER_ID = "owner-id-1"
_OWNER_USERNAME = "me.owner"
_OWNER_HANDLE = f"@{_OWNER_USERNAME}"

_PARTNER_ID = "partner-id-1"
_PARTNER_USERNAME = "carol.partner"
_OTHER_ID = "other-id-9"
_OTHER_USERNAME = "dave.other"


def _utc_time_config() -> TimeConfig:
    return TimeConfig(user_timezone="UTC", mailbox_tz="UTC", runner_tz="UTC", window="calendar_day")


# ---------------------------------------------------------------------------
# Fake HTTP layer — records every call so allowlist-before-GET is provable.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeHttp:
    """A fake httpx-like client. Records (verb, url) for the read-only audit."""

    def __init__(self, routes_get: dict, routes_post: dict):
        self._routes_get = routes_get
        self._routes_post = routes_post
        self.calls: list[tuple[str, str]] = []

    def _match(self, table: dict, url: str):
        for path, payload in sorted(table.items(), key=lambda kv: -len(kv[0])):
            if url.endswith(path):
                return payload
        raise AssertionError(f"unexpected URL: {url}")

    def get(self, url, *, params=None, headers=None):
        self.calls.append(("GET", url))
        payload = self._match(self._routes_get, url)
        if callable(payload):
            return _FakeResponse(payload(params or {}))
        return _FakeResponse(payload)

    def post(self, url, *, json=None, headers=None):
        self.calls.append(("POST", url))
        return _FakeResponse(self._match(self._routes_post, url))

    # -- convenience accessors for assertions -------------------------------

    def posts_calls_for(self, channel_id: str) -> list[str]:
        """All posts GETs issued against ``channel_id`` (allowlist-before-GET)."""
        suffix = f"/channels/{channel_id}/posts"
        return [url for (v, url) in self.calls if v == "GET" and url.endswith(suffix)]


def _make_adapter(http: _FakeHttp, **cfg_overrides) -> MattermostSourceAdapter:
    client = MattermostReadClient(
        "https://mm.corp", "fake-pat-not-a-real-token", http_client=http, per_page=200
    )
    return MattermostSourceAdapter(
        MattermostSourceConfig(base_url="https://mm.corp", **cfg_overrides),
        _utc_time_config(),
        client=client,
    )


# ---------------------------------------------------------------------------
# Synthetic fixtures: posts, channels, users.
# ---------------------------------------------------------------------------


def _post(pid: str, user_id: str, message: str, *, create_at: int | None = None) -> dict:
    return {
        "id": pid,
        "root_id": "",
        "user_id": user_id,
        "create_at": create_at if create_at is not None else _MID_DAY_MS,
        "delete_at": 0,
        "type": "",
        "message": message,
    }


def _postlist(posts: list[dict]) -> dict:
    # newest-first order
    ordered = sorted(posts, key=lambda p: p["create_at"], reverse=True)
    return {"order": [p["id"] for p in ordered], "posts": {p["id"]: p for p in ordered}}


def _users_payload() -> list[dict]:
    return [
        {"id": _OWNER_ID, "username": _OWNER_USERNAME, "email": "me@corp"},
        {"id": _PARTNER_ID, "username": _PARTNER_USERNAME, "email": "carol@corp"},
        {"id": _OTHER_ID, "username": _OTHER_USERNAME, "email": "dave@corp"},
        {"id": "author-1", "username": "alice.author", "email": "alice@corp"},
    ]


def _dm_channel_name(a: str, b: str) -> str:
    """A 'D' channel name is the two member ids joined by '__'."""
    return f"{a}__{b}"


# A full fixture with: one OP channel (with an @owner mention + a general post),
# one 1:1 DM with the allowlisted partner, one 1:1 DM with a non-matching user,
# and one group-DM containing an @owner mention. Used across scope tests.
def _full_http() -> _FakeHttp:
    me = {"id": _OWNER_ID, "username": _OWNER_USERNAME, "email": "me@corp"}
    teams = [{"id": "team-1"}]

    op_channel = {
        "id": "chan-op",
        "type": "O",
        "display_name": "release-train",
        "name": "release-train",
        "last_post_at": _MID_DAY_MS + 4000,
    }
    dm_partner = {
        "id": "dm-partner",
        "type": "D",
        "name": _dm_channel_name(_OWNER_ID, _PARTNER_ID),
        "last_post_at": _MID_DAY_MS + 3000,
    }
    dm_other = {
        "id": "dm-other",
        "type": "D",
        "name": _dm_channel_name(_OWNER_ID, _OTHER_ID),
        "last_post_at": _MID_DAY_MS + 2000,
    }
    group_dm = {
        "id": "gdm-1",
        "type": "G",
        "name": "opaque-group-hash",  # 'G' name is an opaque hash, not id1__id2
        "last_post_at": _MID_DAY_MS + 1000,
    }

    chans = [op_channel, dm_partner, dm_other, group_dm]

    op_posts = _postlist(
        [
            _post(
                "p-op-mention",
                "author-1",
                f"{_OWNER_HANDLE} please review the PR",
                create_at=_MID_DAY_MS + 200,
            ),
            _post(
                "p-op-general", "author-1", "deploy window confirmed", create_at=_MID_DAY_MS + 100
            ),
        ]
    )
    # DM with the partner: one owner post + one counterparty (partner) post.
    dm_partner_posts = _postlist(
        [
            _post(
                "p-dmp-owner",
                _OWNER_ID,
                "I'll get to it this afternoon",
                create_at=_MID_DAY_MS + 300,
            ),
            _post(
                "p-dmp-cp",
                _PARTNER_ID,
                "can you approve the budget by EOD? " + ("x" * 400),
                create_at=_MID_DAY_MS + 200,
            ),
        ]
    )
    # DM with a non-matching user (must NEVER be paged under 'selected').
    dm_other_posts = _postlist(
        [
            _post("p-dmo-owner", _OWNER_ID, "noted, thanks", create_at=_MID_DAY_MS + 300),
            _post("p-dmo-cp", _OTHER_ID, "ping " + ("y" * 400), create_at=_MID_DAY_MS + 200),
        ]
    )
    # Group-DM containing an @owner MENTION from a counterparty (the leak case).
    group_dm_posts = _postlist(
        [
            _post(
                "p-gdm-mention",
                _PARTNER_ID,
                f"{_OWNER_HANDLE} can you join the call? " + ("z" * 400),
                create_at=_MID_DAY_MS + 200,
            ),
            _post("p-gdm-owner", _OWNER_ID, "on my way", create_at=_MID_DAY_MS + 100),
        ]
    )

    routes_get = {
        "/api/v4/users/me": me,
        "/api/v4/users/me/teams": teams,
        "/api/v4/users/me/teams/team-1/channels": chans,
        "/api/v4/channels/chan-op/posts": op_posts,
        "/api/v4/channels/dm-partner/posts": dm_partner_posts,
        "/api/v4/channels/dm-other/posts": dm_other_posts,
        "/api/v4/channels/gdm-1/posts": group_dm_posts,
        # Group-DM members (owner + partner + other). 'D' channels need no members
        # call (derived from name), so only the 'G' channel registers one.
        "/api/v4/channels/gdm-1/members": [
            {"user_id": _OWNER_ID},
            {"user_id": _PARTNER_ID},
            {"user_id": _OTHER_ID},
        ],
    }
    routes_post = {"/api/v4/users/ids": _users_payload()}
    return _FakeHttp(routes_get, routes_post)


# ---------------------------------------------------------------------------
# 1. Pure-unit helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel,expected",
    [
        ({"type": "O"}, False),
        ({"type": "P"}, False),
        ({"type": "D"}, True),
        ({"type": "G"}, True),
        ({}, False),  # absent type → treated as op (not a DM)
    ],
)
def test_is_dm_channel(channel, expected):
    assert _is_dm_channel(channel) is expected


@pytest.mark.parametrize(
    "channel,expected",
    [
        ({"type": "O"}, "op"),
        ({"type": "P"}, "op"),
        ({"type": "D"}, "dm"),
        ({"type": "G"}, "gm"),
        ({}, "op"),
    ],
)
def test_channel_kind(channel, expected):
    assert _channel_kind(channel) == expected


def test_dm_counterparty_ids_from_name():
    ch = {"type": "D", "name": _dm_channel_name(_OWNER_ID, _PARTNER_ID)}
    assert _dm_counterparty_ids_from_name(ch, _OWNER_ID) == [_PARTNER_ID]
    # owner second in the name still resolves the counterparty
    ch2 = {"type": "D", "name": _dm_channel_name(_PARTNER_ID, _OWNER_ID)}
    assert _dm_counterparty_ids_from_name(ch2, _OWNER_ID) == [_PARTNER_ID]
    # self-DM (id1 == id2 == owner) → no counterparty
    ch3 = {"type": "D", "name": _dm_channel_name(_OWNER_ID, _OWNER_ID)}
    assert _dm_counterparty_ids_from_name(ch3, _OWNER_ID) == []
    # malformed name (no '__') → empty
    assert _dm_counterparty_ids_from_name({"type": "D", "name": "weird"}, _OWNER_ID) == []


def test_is_self_dm():
    self_dm = {"type": "D", "name": _dm_channel_name(_OWNER_ID, _OWNER_ID)}
    assert _is_self_dm(self_dm, _OWNER_ID) is True
    # a normal 1:1 DM is not a self-DM
    partner = {"type": "D", "name": _dm_channel_name(_OWNER_ID, _PARTNER_ID)}
    assert _is_self_dm(partner, _OWNER_ID) is False
    # a group-DM ('G') is never a self-DM here, even if oddly named
    assert (
        _is_self_dm({"type": "G", "name": _dm_channel_name(_OWNER_ID, _OWNER_ID)}, _OWNER_ID)
        is False
    )
    # blank owner id → fail closed (not classified as self)
    assert _is_self_dm(self_dm, "") is False


@pytest.mark.parametrize("scope", ["own_posts_only", "all"])
def test_self_dm_excluded_by_default_included_when_opted_in(scope):
    """The owner's notes-to-self DM is dropped by default under every ingesting
    scope (it's personal scratch space), and re-included only with
    dm_include_self=True. Exercises the selection chokepoint directly — no HTTP
    is issued for own_posts_only/all selection."""
    self_dm = {"id": "dm-self", "type": "D", "name": _dm_channel_name(_OWNER_ID, _OWNER_ID)}
    partner_dm = {"id": "dm-partner", "type": "D", "name": _dm_channel_name(_OWNER_ID, _PARTNER_ID)}
    http = _FakeHttp({}, {})

    default = _make_adapter(http, dm_scope=scope, dm_consent_acknowledged=True)
    kept = default._select_dm_channels([self_dm, partner_dm], scope, _OWNER_ID)
    assert [c["id"] for c in kept] == ["dm-partner"]  # self-DM dropped

    opted = _make_adapter(http, dm_scope=scope, dm_include_self=True, dm_consent_acknowledged=True)
    kept_in = opted._select_dm_channels([self_dm, partner_dm], scope, _OWNER_ID)
    assert {c["id"] for c in kept_in} == {"dm-self", "dm-partner"}  # self-DM kept


def test_normalize_dm_allowlist_trims_lowercases_drops_blanks():
    norm = _normalize_dm_allowlist(["  Carol@Corp ", "@Dave.Other", "", "   "])
    assert norm == frozenset({"carol@corp", "@dave.other"})


def test_user_identity_tokens():
    user = {"id": _PARTNER_ID, "username": _PARTNER_USERNAME, "email": "Carol@Corp"}
    tokens = _user_identity_tokens(user)
    assert _PARTNER_ID in tokens
    assert _PARTNER_USERNAME in tokens
    assert f"@{_PARTNER_USERNAME}" in tokens
    assert "carol@corp" in tokens  # lowercased


# ---------------------------------------------------------------------------
# 2. dm_scope='off' (default) — the leak is closed
# ---------------------------------------------------------------------------


def test_off_default_drops_all_dm_channels_and_closes_group_dm_mention_leak():
    """REGRESSION (the leak): a group-DM containing an @owner mention yields ZERO
    messages under dm_scope='off' (the default). The op-channel mention still
    survives; D/G channels are NEVER paged for posts."""
    http = _full_http()
    adapter = _make_adapter(http)  # dm_scope defaults to 'off'
    messages = adapter.fetch(_DIGEST_DATE)
    kept = {m.msg_id for m in messages}

    # ONLY the op-channel mention survives — no DM/group-DM post at all.
    assert kept == {"mm:p-op-mention"}
    # CRITICAL: the group-DM @owner mention is NOT ingested (leak closed).
    assert "mm:p-gdm-mention" not in kept

    # D/G channels are NEVER paged for posts under 'off'.
    assert http.posts_calls_for("dm-partner") == []
    assert http.posts_calls_for("dm-other") == []
    assert http.posts_calls_for("gdm-1") == []
    # The op channel WAS paged (mentions slice unchanged).
    assert http.posts_calls_for("chan-op")

    stats = adapter.last_fetch_stats
    assert stats["dm_scope"] == "off"
    assert stats["dm_channels_scanned"] == 0
    assert stats["dm_messages"] == 0


def test_off_preserves_op_channel_allowlist_equivalence():
    """Under 'off' the OP-channel allowlist path is byte-identical: an allowlisted
    op channel ingests its general post as context (the #136 behavior intact)."""
    http = _full_http()
    adapter = _make_adapter(http, channel_allowlist=["release-train"])
    by_id = {m.msg_id: m for m in adapter.fetch(_DIGEST_DATE)}

    assert "mm:p-op-mention" in by_id
    assert "mm:p-op-general" in by_id  # general post ingested (allowlisted)
    # mention addressed-to-me; general post is context.
    assert by_id["mm:p-op-mention"].to_recipients[0] == _OWNER_HANDLE
    assert by_id["mm:p-op-general"].to_recipients == []
    # mm_channel_type is 'O' for the op channel.
    assert by_id["mm:p-op-mention"].mm_channel_type == "O"


# ---------------------------------------------------------------------------
# 3. dm_scope='own_posts_only' — owner posts only, counterparty dropped
# ---------------------------------------------------------------------------


def test_own_posts_only_keeps_owner_drops_counterparty():
    """own_posts_only: only owner-authored DM posts kept (addressed_to_me=False,
    uncapped); counterparty posts are dropped before they become messages."""
    http = _full_http()
    adapter = _make_adapter(http, dm_scope="own_posts_only")
    by_id = {m.msg_id: m for m in adapter.fetch(_DIGEST_DATE)}

    # Owner posts from BOTH DMs + the group-DM are kept (no member filter).
    assert "mm:p-dmp-owner" in by_id
    assert "mm:p-dmo-owner" in by_id
    assert "mm:p-gdm-owner" in by_id
    # Counterparty posts are DROPPED (no third-party text reaches the LLM).
    assert "mm:p-dmp-cp" not in by_id
    assert "mm:p-dmo-cp" not in by_id
    assert "mm:p-gdm-mention" not in by_id  # counterparty mention also dropped

    owner_dm = by_id["mm:p-dmp-owner"]
    assert owner_dm.to_recipients == []  # owner's own statement, not addressed-to-me
    assert owner_dm.subject == ""  # DMs have no subject
    assert owner_dm.mm_channel_type == "D"
    # Owner text is harvested in full.
    assert owner_dm.text_body == "I'll get to it this afternoon"

    # No consent required for own_posts_only (config constructs fine, no error).
    stats = adapter.last_fetch_stats
    assert stats["dm_scope"] == "own_posts_only"
    assert stats["dm_channels_scanned"] == 3  # all active D/G fetched


# ---------------------------------------------------------------------------
# 4. dm_scope='selected' — per-partner allowlist, before-GET enforcement
# ---------------------------------------------------------------------------


def _selected_adapter(http: _FakeHttp, allowlist: list[str], **kw) -> MattermostSourceAdapter:
    return _make_adapter(
        http,
        dm_scope="selected",
        dm_allowlist=allowlist,
        dm_consent_acknowledged=True,
        **kw,
    )


def test_selected_matching_partner_dm_kept_in_full():
    """A matching partner's 1:1 DM: counterparty posts kept (addressed_to_me=True),
    owner posts kept — both harvested in full (no quote cap)."""
    http = _full_http()
    adapter = _selected_adapter(http, [_PARTNER_USERNAME])
    by_id = {m.msg_id: m for m in adapter.fetch(_DIGEST_DATE)}

    # The partner DM is fully ingested.
    assert "mm:p-dmp-owner" in by_id
    assert "mm:p-dmp-cp" in by_id

    cp = by_id["mm:p-dmp-cp"]
    assert cp.to_recipients[0] == _OWNER_HANDLE  # a DM to you IS addressed to you
    assert cp.mm_channel_type == "D"
    assert cp.subject == ""
    # Counterparty text is kept in full — no truncation.
    assert cp.text_body == "can you approve the budget by EOD? " + ("x" * 400)

    owner = by_id["mm:p-dmp-owner"]
    assert owner.to_recipients == []  # owner's own statement
    assert owner.text_body == "I'll get to it this afternoon"


def test_selected_non_matching_partner_dm_never_paged():
    """allowlist-BEFORE-GET: a NON-matching partner's DM is never paged for posts
    (assert the fake client's get_posts is NOT called for it)."""
    http = _full_http()
    adapter = _selected_adapter(http, [_PARTNER_USERNAME])
    kept = {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}

    # The non-matching DM contributes nothing AND was never paged.
    assert "mm:p-dmo-owner" not in kept
    assert "mm:p-dmo-cp" not in kept
    assert http.posts_calls_for("dm-other") == [], "non-matching DM must not be paged"
    # The matching partner DM WAS paged.
    assert http.posts_calls_for("dm-partner")


def test_selected_group_dm_kept_iff_any_member_matches():
    """A group-DM is kept iff ANY non-owner member matches; text is harvested in
    full. Members are resolved via get_channel_members ('G')."""
    http = _full_http()
    # The partner is a member of the group-DM → it is selected.
    adapter = _selected_adapter(http, [_PARTNER_USERNAME])
    by_id = {m.msg_id: m for m in adapter.fetch(_DIGEST_DATE)}

    # Group-DM ingested (partner matched among its members).
    assert "mm:p-gdm-mention" in by_id
    assert "mm:p-gdm-owner" in by_id
    gdm = by_id["mm:p-gdm-mention"]
    assert gdm.mm_channel_type == "G"
    assert gdm.to_recipients[0] == _OWNER_HANDLE  # counterparty post → addressed-to-me
    assert gdm.text_body == f"{_OWNER_HANDLE} can you join the call? " + ("z" * 400)  # full
    # The 'G' member list WAS resolved (metadata, before posts GET).
    assert any(url.endswith("/channels/gdm-1/members") for (_, url) in http.calls)


def test_selected_group_dm_dropped_when_no_member_matches():
    """A group-DM whose non-owner members DON'T match the allowlist is never paged."""
    http = _full_http()
    # Allowlist a user who is NOT in the group-DM and NOT the matched 1:1 partner.
    adapter = _selected_adapter(http, ["someone.absent"])
    kept = {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}
    assert not any(mid.startswith("mm:p-gdm-") for mid in kept)
    assert http.posts_calls_for("gdm-1") == []


def test_selected_empty_allowlist_is_effective_off():
    """dm_scope='selected' with an empty dm_allowlist → no DM fetched (graceful)."""
    http = _full_http()
    adapter = _make_adapter(
        http, dm_scope="selected", dm_allowlist=[], dm_consent_acknowledged=True
    )
    kept = {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}
    # Only the op-channel mention; no DM paged.
    assert kept == {"mm:p-op-mention"}
    assert http.posts_calls_for("dm-partner") == []
    assert http.posts_calls_for("gdm-1") == []


# -- member-match by each identity token form (case-insensitive) ------------


@pytest.mark.parametrize(
    "entry",
    [
        _PARTNER_ID,  # by user_id
        f"@{_PARTNER_USERNAME}",  # by @username
        _PARTNER_USERNAME,  # by bare username
        "carol@corp",  # by email
        "CAROL@CORP",  # email, upper-case (case-insensitive)
        _PARTNER_USERNAME.upper(),  # bare username, upper-case
    ],
)
def test_selected_member_match_by_each_token_form(entry):
    """A 1:1 DM partner matches by user_id, @username, bare username, or email
    (case-insensitive)."""
    http = _full_http()
    adapter = _selected_adapter(http, [entry])
    kept = {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}
    assert "mm:p-dmp-cp" in kept, f"entry {entry!r} should match the partner DM"
    assert http.posts_calls_for("dm-partner")


def test_selected_d_counterparty_derived_from_name_no_members_call():
    """A 'D' channel's counterparty is derived from the channel-name split — no
    get_channel_members call is issued for a 1:1 DM."""
    http = _full_http()
    adapter = _selected_adapter(http, [_PARTNER_USERNAME])
    adapter.fetch(_DIGEST_DATE)
    # No members endpoint hit for the 'D' channels (only the 'G' channel).
    d_member_calls = [
        url
        for (_, url) in http.calls
        if url.endswith("/channels/dm-partner/members")
        or url.endswith("/channels/dm-other/members")
    ]
    assert d_member_calls == []


# ---------------------------------------------------------------------------
# 5. dm_scope='all' — every active D/G ingested
# ---------------------------------------------------------------------------


def test_all_ingests_every_active_dm_in_full():
    """dm_scope='all': every active D/G is ingested (no member filter); all text
    — counterparty and owner — is harvested in full."""
    http = _full_http()
    adapter = _make_adapter(http, dm_scope="all", dm_consent_acknowledged=True)
    by_id = {m.msg_id: m for m in adapter.fetch(_DIGEST_DATE)}

    # Both DMs + the group-DM are ingested (every counterparty post present).
    for mid in (
        "mm:p-dmp-owner",
        "mm:p-dmp-cp",
        "mm:p-dmo-owner",
        "mm:p-dmo-cp",
        "mm:p-gdm-mention",
        "mm:p-gdm-owner",
    ):
        assert mid in by_id, mid

    # Counterparty posts are addressed-to-me + kept in full.
    assert by_id["mm:p-dmp-cp"].to_recipients[0] == _OWNER_HANDLE
    assert by_id["mm:p-dmp-cp"].text_body == "can you approve the budget by EOD? " + ("x" * 400)
    assert by_id["mm:p-dmo-cp"].text_body == "ping " + ("y" * 400)
    # Owner posts in full.
    assert by_id["mm:p-dmp-owner"].text_body == "I'll get to it this afternoon"

    stats = adapter.last_fetch_stats
    assert stats["dm_scope"] == "all"
    assert stats["dm_channels_scanned"] == 3
    assert stats["dm_messages"] == 6  # 2 + 2 + 2


def test_all_dm_channel_types_set_correctly():
    """mm_channel_type is the channel's raw type ('D' / 'G' / 'O') across sources."""
    http = _full_http()
    adapter = _make_adapter(http, dm_scope="all", dm_consent_acknowledged=True)
    by_id = {m.msg_id: m for m in adapter.fetch(_DIGEST_DATE)}
    assert by_id["mm:p-op-mention"].mm_channel_type == "O"
    assert by_id["mm:p-dmp-owner"].mm_channel_type == "D"
    assert by_id["mm:p-gdm-owner"].mm_channel_type == "G"


# ---------------------------------------------------------------------------
# 7. Stats payload-free + read-only contract under DM scopes
# ---------------------------------------------------------------------------


def test_dm_fetch_read_only_contract():
    """Under dm_scope='all' the adapter still issues ONLY GET + the /users/ids
    batch-read POST (no view/post/reactions/websocket)."""
    http = _full_http()
    adapter = _make_adapter(http, dm_scope="all", dm_consent_acknowledged=True)
    adapter.fetch(_DIGEST_DATE)
    posts_made = [(v, url) for (v, url) in http.calls if v == "POST"]
    assert all(url.endswith("/api/v4/users/ids") for (_, url) in posts_made)
    forbidden = ("/view", "/reactions", "/channels/direct", "/websocket")
    for verb, url in http.calls:
        assert not any(f in url for f in forbidden), f"forbidden endpoint: {verb} {url}"


# ---------------------------------------------------------------------------
# 9. Robustness: a blank owner id must fail CLOSED (no DM ingestion)
# ---------------------------------------------------------------------------


def test_blank_owner_id_disables_dm_ingestion_fail_closed():
    """If get_me returns a blank id, owner/counterparty classification is unsound.
    The adapter must fail CLOSED — disable DMs entirely (never page D/G), so no
    post is misclassified as a counterparty. Op-channel mentions still work."""
    http = _full_http()
    http._routes_get["/api/v4/users/me"] = {
        "id": "",  # blank — the guarded case
        "username": _OWNER_USERNAME,
        "email": "me@corp",
    }
    adapter = _make_adapter(
        http,
        enabled=True,
        dm_scope="selected",
        dm_allowlist=[f"@{_PARTNER_USERNAME}"],
        dm_consent_acknowledged=True,
        dm_consent_acknowledged_at="2026-03-29T00:00:00+00:00",
    )
    messages = adapter.fetch(_DIGEST_DATE)
    # No DM/group-DM channel is ever paged for posts (DMs disabled).
    assert http.posts_calls_for("dm-partner") == []
    assert http.posts_calls_for("dm-other") == []
    assert http.posts_calls_for("gdm-1") == []
    # The op-channel @owner mention still survives (mentions key on username).
    assert any(m.mm_channel_type == "O" for m in messages)
