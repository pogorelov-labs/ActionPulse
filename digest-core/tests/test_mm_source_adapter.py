"""P1b: offline Mattermost mentions SOURCE adapter (no network).

Everything here is synthetic, fully offline, and payload-free. A FAKE http
client returns hand-built v4 API JSON; nothing touches the corp network. The
real adapter is validated LIVE only from inside the corp network (ADR-012,
"code outside, run inside"):

    MM_PAT=... MM_BASE_URL=https://mm.corp cli run --sources mm --dry-run

What is asserted here:
  * the read-only contract (only GET + the /users/ids batch-read POST are made;
    never ViewChannel / posts / reactions / websocket);
  * the ``last_post_at`` activity pre-gate skips a stale channel;
  * client-side @-mention parsing keeps exactly the owner-mention, non-system,
    non-bot, non-tombstone post and drops the rest;
  * the field map (``mm:`` namespaced id, conversation_id == root_id, channel
    display name as subject, author @username as from);
  * addressed-to-me fires through the REAL pipeline (splitter + ranker), via
    the owner's identity in ``to_recipients`` (not an invented flag);
  * pagination early-stops at the window boundary on the ``order`` array;
  * the adapter satisfies the runtime-checkable ``SourceAdapter`` Protocol;
  * the run-wiring puts mm in the LENIENT group; unconfigured mm raises;
  * an end-to-end-ish offline pass through ``_normalize_messages`` +
    ``ThreadBuilder`` keeps the markdown body un-truncated and threads natively.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from digest_core import run as runner
from digest_core.config import Config, MattermostSourceConfig, TimeConfig
from digest_core.evidence.split import EvidenceSplitter
from digest_core.ingest.mattermost import (
    MattermostReadClient,
    MattermostSourceAdapter,
    _is_system_or_bot,
    _mention_regex,
)
from digest_core.ingest.source_adapter import SourceAdapter
from digest_core.threads.build import ThreadBuilder

# ---------------------------------------------------------------------------
# Window: a fixed calendar day in UTC so ms boundaries are deterministic.
# ---------------------------------------------------------------------------

_DIGEST_DATE = "2026-03-29"
# calendar_day window in UTC for the date above (user_timezone=UTC below).
_MID_DAY_MS = int(datetime(2026, 3, 29, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
_BEFORE_WINDOW_MS = int(datetime(2026, 3, 28, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

_OWNER_ID = "owner-id-1"
_OWNER_USERNAME = "me.owner"  # contains a dot to exercise regex-escaping
_OWNER_HANDLE = f"@{_OWNER_USERNAME}"


def _utc_time_config() -> TimeConfig:
    """A UTC calendar-day window so ms math is exact and tz-independent."""
    return TimeConfig(
        user_timezone="UTC",
        mailbox_tz="UTC",
        runner_tz="UTC",
        window="calendar_day",
    )


# ---------------------------------------------------------------------------
# Fake HTTP layer — records every call so we can prove read-only behavior.
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
        # Longest-suffix match against the registered API paths.
        for path, payload in sorted(table.items(), key=lambda kv: -len(kv[0])):
            if url.endswith(path):
                return payload
        raise AssertionError(f"unexpected URL: {url}")

    def get(self, url, *, params=None, headers=None):  # noqa: D401 - fake
        self.calls.append(("GET", url))
        payload = self._match(self._routes_get, _strip_query(url, params))
        if callable(payload):
            return _FakeResponse(payload(params or {}))
        return _FakeResponse(payload)

    def post(self, url, *, json=None, headers=None):  # noqa: D401 - fake
        self.calls.append(("POST", url))
        return _FakeResponse(self._match(self._routes_post, url))


def _strip_query(url, params):
    # Our routes are registered by path; params are passed separately by the
    # client, so the URL itself has no query string. Return as-is.
    return url


# ---------------------------------------------------------------------------
# Synthetic API fixtures
# ---------------------------------------------------------------------------


def _build_fake_http() -> _FakeHttp:
    """One owner, two teams worth of channels (one active, one stale), a PostList
    mixing the relevant post kinds, and a /users/ids response."""

    me = {"id": _OWNER_ID, "username": _OWNER_USERNAME, "email": "me@corp"}
    teams = [{"id": "team-1"}, {"id": "team-2"}]

    active_channel = {
        "id": "chan-active",
        "display_name": "release-train",
        "name": "release-train",
        "last_post_at": _MID_DAY_MS,  # active within the window
    }
    stale_channel = {
        "id": "chan-stale",
        "display_name": "old-archive",
        "name": "old-archive",
        "last_post_at": _BEFORE_WINDOW_MS,  # predates the window → pre-gated out
    }

    # PostList for the active channel: newest-first ``order``.
    #   p_mention   — @owner mention (KEEP)
    #   p_other     — mentions someone else (DROP)
    #   p_system    — system_join_channel (DROP)
    #   p_bot       — from_bot (DROP)
    #   p_tombstone — delete_at>0 (DROP)
    #   p_old       — create_at before window (EARLY-STOP boundary)
    posts = {
        "p_mention": {
            "id": "p_mention",
            "root_id": "root-thread-1",
            "user_id": "author-1",
            "channel_id": "chan-active",
            "create_at": _MID_DAY_MS,
            "delete_at": 0,
            "type": "",
            "message": f"{_OWNER_HANDLE} can you confirm the release before 15:00? See the **PR**.",
            "metadata": {"files": [{"id": "f1"}]},
        },
        "p_other": {
            "id": "p_other",
            "root_id": "",
            "user_id": "author-2",
            "create_at": _MID_DAY_MS + 1000,
            "delete_at": 0,
            "type": "",
            "message": "@someone.else please take a look",
        },
        "p_system": {
            "id": "p_system",
            "root_id": "",
            "user_id": "author-2",
            "create_at": _MID_DAY_MS + 2000,
            "delete_at": 0,
            "type": "system_join_channel",
            "message": f"{_OWNER_HANDLE} joined the channel",
        },
        "p_bot": {
            "id": "p_bot",
            "root_id": "",
            "user_id": "bot-1",
            "create_at": _MID_DAY_MS + 3000,
            "delete_at": 0,
            "type": "",
            "props": {"from_bot": "true"},
            "message": f"CI says {_OWNER_HANDLE} the build is green",
        },
        "p_tombstone": {
            "id": "p_tombstone",
            "root_id": "",
            "user_id": "author-2",
            "create_at": _MID_DAY_MS + 4000,
            "delete_at": _MID_DAY_MS + 5000,
            "type": "",
            "message": f"{_OWNER_HANDLE} ignore this, deleted",
        },
        "p_old": {
            "id": "p_old",
            "root_id": "",
            "user_id": "author-2",
            "create_at": _BEFORE_WINDOW_MS,  # before window → triggers early-stop
            "delete_at": 0,
            "type": "",
            "message": f"{_OWNER_HANDLE} stale mention from yesterday",
        },
    }
    # order is newest-first; p_old is last so the early-stop fires after the
    # in-window posts are scanned.
    order = ["p_tombstone", "p_bot", "p_system", "p_other", "p_mention", "p_old"]
    postlist = {"order": order, "posts": posts}

    routes_get = {
        "/api/v4/users/me": me,
        "/api/v4/users/me/teams": teams,
        "/api/v4/users/me/teams/team-1/channels": [active_channel, stale_channel],
        "/api/v4/users/me/teams/team-2/channels": [],  # no channels in team-2
        "/api/v4/channels/chan-active/posts": postlist,
        # chan-stale is pre-gated out, so its posts are never requested — but
        # register an empty PostList so an accidental fetch is visible/asserted.
        "/api/v4/channels/chan-stale/posts": {"order": [], "posts": {}},
    }
    routes_post = {
        "/api/v4/users/ids": [
            {"id": "author-1", "username": "alice.author", "email": "alice@corp"},
            {"id": "author-2", "username": "bob.author", "email": ""},
        ],
    }
    return _FakeHttp(routes_get, routes_post)


def _make_adapter(http: _FakeHttp | None = None) -> MattermostSourceAdapter:
    http = http or _build_fake_http()
    client = MattermostReadClient(
        "https://mm.corp", "fake-pat-not-a-real-token", http_client=http, per_page=200
    )
    return MattermostSourceAdapter(
        MattermostSourceConfig(base_url="https://mm.corp"),
        _utc_time_config(),
        client=client,
    )


# ---------------------------------------------------------------------------
# 1. Mention parsing edge cases (pure unit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("@me.owner can you check?", True),  # plain mention
        ("hey @me.owner!", True),  # trailing punctuation = word boundary
        ("line1\n@me.owner please", True),  # start-of-line
        ("ping @ME.OWNER now", True),  # case-insensitive
        ("@me.ownerly is someone else", False),  # \b stops a longer handle
        ("email foo@me.owner is not a mention", False),  # (?<![\w@]) blocks local part
        ("contact me.owner without the at", False),  # needs the @
        ("@meowner (no dot) differs", False),  # exact handle only
    ],
)
def test_mention_regex_edge_cases(message, expected):
    assert bool(_mention_regex(_OWNER_USERNAME).search(message)) is expected


def test_system_and_bot_detection():
    assert _is_system_or_bot({"type": "system_join_channel"}) is True
    assert _is_system_or_bot({"type": "", "props": {"from_bot": "true"}}) is True
    assert _is_system_or_bot({"type": "", "props": {"from_bot": True}}) is True
    assert _is_system_or_bot({"type": "", "message": "normal"}) is False


# ---------------------------------------------------------------------------
# 2. fetch() — the core behavior
# ---------------------------------------------------------------------------


def test_fetch_keeps_only_owner_mention():
    """Exactly the @owner, non-system, non-bot, non-tombstone post survives."""
    adapter = _make_adapter()
    messages = adapter.fetch(_DIGEST_DATE)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.msg_id == "mm:p_mention"
    assert msg.source == "mm"
    assert msg.conversation_id == "root-thread-1"  # root_id, native threading
    assert msg.subject == "release-train"  # channel display_name
    assert msg.from_name == "@alice.author"  # author @username in the from slot
    assert msg.sender_email == "alice@corp"
    assert msg.has_attachments is True  # post.metadata.files present
    assert "**PR**" in msg.text_body  # raw markdown preserved on the message


def test_fetch_conversation_id_falls_back_to_post_id_for_root():
    """A root post (empty root_id) gets conversation_id == post.id."""
    http = _build_fake_http()
    http._routes_get["/api/v4/channels/chan-active/posts"]["posts"]["p_mention"]["root_id"] = ""
    adapter = _make_adapter(http)
    msg = adapter.fetch(_DIGEST_DATE)[0]
    assert msg.conversation_id == "p_mention"


def test_stale_channel_pre_gated_out():
    """The stale channel (last_post_at before the window) is never paged."""
    http = _build_fake_http()
    adapter = _make_adapter(http)
    adapter.fetch(_DIGEST_DATE)
    paged = [url for (verb, url) in http.calls if url.endswith("/channels/chan-stale/posts")]
    assert paged == [], "stale channel must be skipped by the last_post_at pre-gate"
    # the active channel WAS paged
    assert any(url.endswith("/channels/chan-active/posts") for (_, url) in http.calls)


def test_read_only_contract_no_mutating_calls():
    """The adapter issues only GET + the /users/ids batch-read POST.

    No ViewChannel, no post create, no reactions, no websocket — the MM analogue
    of the EWS no-.save() discipline.
    """
    http = _build_fake_http()
    adapter = _make_adapter(http)
    adapter.fetch(_DIGEST_DATE)
    posts_made = [(verb, url) for (verb, url) in http.calls if verb == "POST"]
    # The ONLY POST allowed is the non-mutating /users/ids batch read.
    assert all(url.endswith("/api/v4/users/ids") for (_, url) in posts_made)
    forbidden = ("/view", "/reactions", "/channels/direct", "/websocket")
    for verb, url in http.calls:
        assert not any(f in url for f in forbidden), f"forbidden endpoint touched: {verb} {url}"
    # No POST to /posts (delivery) either.
    assert not any(url.endswith("/api/v4/posts") for (_, url) in http.calls)


def test_pagination_early_stops_at_window_boundary():
    """When a full page ends inside the window, the next page is requested; when
    a post older than the window appears in ``order``, scanning stops."""
    # Build a 2-page channel: page 0 is a full page (per_page=2) of in-window
    # posts, page 1 contains one in-window + one before-window (early-stop).
    in_a = {
        "id": "a",
        "root_id": "",
        "user_id": "author-1",
        "create_at": _MID_DAY_MS + 2000,
        "delete_at": 0,
        "type": "",
        "message": f"{_OWNER_HANDLE} A",
    }
    in_b = {
        "id": "b",
        "root_id": "",
        "user_id": "author-1",
        "create_at": _MID_DAY_MS + 1000,
        "delete_at": 0,
        "type": "",
        "message": f"{_OWNER_HANDLE} B",
    }
    in_c = {
        "id": "c",
        "root_id": "",
        "user_id": "author-1",
        "create_at": _MID_DAY_MS,
        "delete_at": 0,
        "type": "",
        "message": f"{_OWNER_HANDLE} C",
    }
    old_d = {
        "id": "d",
        "root_id": "",
        "user_id": "author-1",
        "create_at": _BEFORE_WINDOW_MS,
        "delete_at": 0,
        "type": "",
        "message": f"{_OWNER_HANDLE} D-old",
    }

    def posts_route(params):
        page = int(params.get("page", 0))
        if page == 0:
            return {"order": ["a", "b"], "posts": {"a": in_a, "b": in_b}}
        if page == 1:
            return {"order": ["c", "d"], "posts": {"c": in_c, "d": old_d}}
        raise AssertionError("must early-stop before page 2")

    http = _build_fake_http()
    http._routes_get["/api/v4/channels/chan-active/posts"] = posts_route
    # per_page=2 so page 0 (len 2) is "full" → keep paging; page 1 hits old_d.
    client = MattermostReadClient("https://mm.corp", "fake", http_client=http, per_page=2)
    adapter = MattermostSourceAdapter(
        MattermostSourceConfig(base_url="https://mm.corp", per_page=2),
        _utc_time_config(),
        client=client,
    )
    messages = adapter.fetch(_DIGEST_DATE)
    kept = {m.msg_id for m in messages}
    assert kept == {"mm:a", "mm:b", "mm:c"}  # d-old excluded by early-stop
    page_calls = [url for (v, url) in http.calls if url.endswith("/chan-active/posts")]
    assert len(page_calls) == 2  # page 0 + page 1, then stop (no page 2)


def test_fetch_no_username_returns_empty():
    """If /users/me has no username, no mention can be parsed → empty (no crash)."""
    http = _build_fake_http()
    http._routes_get["/api/v4/users/me"] = {"id": _OWNER_ID, "username": "", "email": ""}
    adapter = _make_adapter(http)
    assert adapter.fetch(_DIGEST_DATE) == []


def test_author_resolution_blank_email_tolerated():
    """A blank author email (hardened server policy) never crashes; from is the @handle."""
    http = _build_fake_http()
    # author-1 now returns a blank email.
    http._routes_post["/api/v4/users/ids"] = [
        {"id": "author-1", "username": "alice.author", "email": ""},
    ]
    adapter = _make_adapter(http)
    msg = adapter.fetch(_DIGEST_DATE)[0]
    assert msg.sender_email == ""
    assert msg.from_name == "@alice.author"


# ---------------------------------------------------------------------------
# 3. Protocol conformance
# ---------------------------------------------------------------------------


def test_adapter_satisfies_source_adapter_protocol():
    adapter = _make_adapter()
    assert isinstance(adapter, SourceAdapter)
    assert adapter.name == "mm"


# ---------------------------------------------------------------------------
# 4. addressed-to-me through the REAL pipeline (splitter + ranker)
# ---------------------------------------------------------------------------

_MENTION_CTA = (
    "@me.owner can you please review the release checklist and confirm the "
    "**deployment** plan before the 15:00 cutoff today? We still need sign-off "
    "on the database migration step and the rollback procedure. Let me know if "
    "anything is unclear or if you want me to walk through the staging results "
    "first; otherwise I will proceed and post the final go/no-go decision."
)


def test_addressed_to_me_fires_through_splitter():
    """The owner @-mention is treated as addressed-to-me by the REAL splitter.

    The adapter puts the owner identity into ``to_recipients``; the splitter's
    ``addressed_to_me`` derivation (alias-in-recipients) then fires — the same
    mechanism the email path uses. The operator's owner alias is ``ews.user_aliases``.
    """
    http = _build_fake_http()
    http._routes_get["/api/v4/channels/chan-active/posts"]["posts"]["p_mention"][
        "message"
    ] = _MENTION_CTA
    adapter = _make_adapter(http)
    messages = adapter.fetch(_DIGEST_DATE)
    assert messages and messages[0].to_recipients[0] == _OWNER_HANDLE

    builder = ThreadBuilder()
    threads = builder.build_threads(messages)
    # The owner's configured alias is the @handle (what the adapter writes into
    # to_recipients). Mirrors ews.user_aliases on the email path.
    splitter = EvidenceSplitter(user_aliases=[_OWNER_HANDLE], user_timezone="UTC")
    chunks = splitter.split_evidence(threads)
    assert chunks, "expected at least one evidence chunk from the mention CTA"
    assert any(getattr(c, "addressed_to_me", False) for c in chunks)
    # And source_ref is authoritatively mm with chat locators.
    ref = chunks[0].source_ref
    assert ref["type"] == "mm"
    assert ref["post_id"] == "p_mention"


def test_addressed_to_me_fires_via_email_alias():
    """If the operator configured the alias as the owner EMAIL, it still fires.

    The adapter also writes the resolved owner email into to_recipients, so an
    email-keyed ``user_aliases`` matches too (robust to either config style).
    """
    http = _build_fake_http()
    http._routes_get["/api/v4/channels/chan-active/posts"]["posts"]["p_mention"][
        "message"
    ] = _MENTION_CTA
    adapter = _make_adapter(http)
    messages = adapter.fetch(_DIGEST_DATE)
    builder = ThreadBuilder()
    threads = builder.build_threads(messages)
    splitter = EvidenceSplitter(user_aliases=["me@corp"], user_timezone="UTC")
    chunks = splitter.split_evidence(threads)
    assert any(getattr(c, "addressed_to_me", False) for c in chunks)


# ---------------------------------------------------------------------------
# 5. run.py wiring — lenient group + unconfigured error
# ---------------------------------------------------------------------------


def test_build_source_adapters_mm_in_lenient_group(monkeypatch):
    """A configured "mm" source lands in the LENIENT group (degrade-not-drop),
    while EWS stays strict."""
    monkeypatch.setenv("MM_PAT", "fake-pat")
    cfg = Config()
    cfg.mm_source.base_url = "https://mm.corp"
    strict, lenient = runner._build_source_adapters(["ews", "mm"], object(), cfg)
    assert [a.name for a in strict] == ["ews"]
    assert [a.name for a in lenient] == ["mm"]


def test_build_source_adapters_mm_only(monkeypatch):
    """`--sources mm` alone → no strict adapter, one lenient mm adapter."""
    monkeypatch.setenv("MM_PAT", "fake-pat")
    cfg = Config()
    cfg.mm_source.base_url = "https://mm.corp"
    strict, lenient = runner._build_source_adapters(["mm"], object(), cfg)
    assert strict == []
    assert [a.name for a in lenient] == ["mm"]


def test_build_source_adapters_mm_unconfigured_no_token(monkeypatch):
    """mm selected with a base_url but MM_PAT unset → actionable error."""
    monkeypatch.delenv("MM_PAT", raising=False)
    cfg = Config()
    cfg.mm_source.base_url = "https://mm.corp"
    with pytest.raises(ValueError, match="PAT is not set"):
        runner._build_source_adapters(["mm"], object(), cfg)


def test_build_source_adapters_mm_unconfigured_no_base_url(monkeypatch):
    """mm selected with no base_url → actionable error (before the run starts)."""
    monkeypatch.setenv("MM_PAT", "fake-pat")
    monkeypatch.delenv("MM_BASE_URL", raising=False)
    cfg = Config()
    cfg.mm_source.base_url = ""
    with pytest.raises(ValueError, match="no base URL is set"):
        runner._build_source_adapters(["mm"], object(), cfg)


def test_build_source_adapters_mattermost_alias(monkeypatch):
    """`mattermost` is an alias for the mm source."""
    monkeypatch.setenv("MM_PAT", "fake-pat")
    cfg = Config()
    cfg.mm_source.base_url = "https://mm.corp"
    _, lenient = runner._build_source_adapters(["mattermost"], object(), cfg)
    assert [a.name for a in lenient] == ["mm"]


# ---------------------------------------------------------------------------
# 6. End-to-end-ish offline: adapter -> _normalize_messages -> ThreadBuilder
# ---------------------------------------------------------------------------


def test_end_to_end_offline_normalize_and_threading():
    """The adapter's messages survive normalize (no quote truncation) and thread
    natively (P1a guarantees), proving the slice composes with the core pipeline."""
    http = _build_fake_http()
    # A body that quotes a prior post with a leading ``>`` line — the email
    # quote-cleaner would truncate it; the mm branch must keep it.
    http._routes_get["/api/v4/channels/chan-active/posts"]["posts"]["p_mention"]["message"] = (
        "> earlier: who owns the release tonight?\n"
        f"{_OWNER_HANDLE} I'll own it — please review the **PR** before 15:00 today "
        "and confirm the rollback plan so we can sign off the go/no-go decision."
    )
    adapter = _make_adapter(http)
    messages = adapter.fetch(_DIGEST_DATE)

    config = Config()
    assert config.email_cleaner.enabled is True  # the truncating path is active
    normalized = runner._normalize_messages(messages, config)
    body = normalized[0].text_body
    assert "I'll own it" in body  # NOT truncated at the leading > line
    assert "**PR**" in body  # markdown preserved (no html_to_text)
    assert normalized[0].source == "mm"

    builder = ThreadBuilder()
    threads = builder.build_threads(normalized)
    assert len(threads) == 1
    assert threads[0].conversation_id == "conv_root-thread-1"  # native root_id thread
    assert builder.stats["threads_merged_by_semantic"] == 0  # no subject-merge for mm


def test_read_client_low_level_uses_bearer_header():
    """The client sends Authorization: Bearer and never logs/embeds the token."""
    captured = {}

    class _CaptureHttp:
        def get(self, url, *, params=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return _FakeResponse({"id": _OWNER_ID, "username": _OWNER_USERNAME})

        def post(self, url, *, json=None, headers=None):  # pragma: no cover
            return _FakeResponse([])

    client = MattermostReadClient("https://mm.corp/", "secret-token", http_client=_CaptureHttp())
    me = client.get_me()
    assert me["username"] == _OWNER_USERNAME
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["url"] == "https://mm.corp/api/v4/users/me"
