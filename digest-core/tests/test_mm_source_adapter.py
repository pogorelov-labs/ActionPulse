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

import httpx
import pytest

from digest_core import run as runner
from digest_core.config import Config, MattermostSourceConfig, TimeConfig
from digest_core.evidence.split import EvidenceSplitter
from digest_core.ingest.mattermost import (
    _DEFAULT_RETRY_AFTER_S,
    _MAX_RETRY_AFTER_S,
    MattermostRateLimited,
    MattermostReadClient,
    MattermostSourceAdapter,
    _AdaptiveChannelFetcher,
    _channel_is_allowlisted,
    _ChannelResult,
    _is_system_or_bot,
    _mention_regex,
    _normalize_allowlist,
    _parse_retry_after,
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


# ---------------------------------------------------------------------------
# 7. Scan-then-% progress + per-channel resilience (the live-bug regression)
# ---------------------------------------------------------------------------


class _RecordingSink:
    """A ProgressSink stand-in that records every ``on_stage_progress`` call.

    Structural (duck-typed) — the ``emit()`` helper only calls the named method,
    so a plain recorder is enough and keeps the test offline + payload-free.
    """

    def __init__(self) -> None:
        # Each entry: (stage, done, total, unit, detail).
        self.progress: list[tuple[str, int, int, str, str]] = []

    def on_stage_progress(self, stage, done, total=None, unit="", detail=""):
        self.progress.append((stage, done, total, unit, detail))


def _multi_channel_http(skip_second: bool = False) -> "_FakeHttp":
    """Three active channels, each with one in-window @owner mention.

    When ``skip_second`` is True the 2nd channel's ``get_posts`` raises
    ``httpx.ReadTimeout`` (the live bug: a slow channel timed out at 45s). The
    1st and 3rd must still be processed and their mentions returned.
    """
    me = {"id": _OWNER_ID, "username": _OWNER_USERNAME, "email": "me@corp"}
    teams = [{"id": "team-1"}]

    def _chan(cid: str, name: str) -> dict:
        return {
            "id": cid,
            "display_name": name,
            "name": name,
            "last_post_at": _MID_DAY_MS,  # all three active within the window
        }

    # Order by last_post_at is equal; _active_channels keeps insertion-ish order
    # after a stable sort, but to make the "2nd channel" deterministic we stagger
    # last_post_at so the sort is unambiguous (newest first → c1, c2, c3).
    chans = [
        {**_chan("chan-1", "alpha"), "last_post_at": _MID_DAY_MS + 3000},
        {**_chan("chan-2", "bravo"), "last_post_at": _MID_DAY_MS + 2000},
        {**_chan("chan-3", "charlie"), "last_post_at": _MID_DAY_MS + 1000},
    ]

    def _mention_post(cid: str) -> dict:
        return {
            "id": f"p-{cid}",
            "root_id": "",
            "user_id": "author-1",
            "channel_id": cid,
            "create_at": _MID_DAY_MS,
            "delete_at": 0,
            "type": "",
            "message": f"{_OWNER_HANDLE} please review {cid}",
        }

    def _postlist(cid: str) -> dict:
        return {"order": [f"p-{cid}"], "posts": {f"p-{cid}": _mention_post(cid)}}

    routes_get = {
        "/api/v4/users/me": me,
        "/api/v4/users/me/teams": teams,
        "/api/v4/users/me/teams/team-1/channels": chans,
        "/api/v4/channels/chan-1/posts": _postlist("chan-1"),
        "/api/v4/channels/chan-2/posts": _postlist("chan-2"),
        "/api/v4/channels/chan-3/posts": _postlist("chan-3"),
    }
    routes_post = {
        "/api/v4/users/ids": [
            {"id": "author-1", "username": "alice.author", "email": "alice@corp"},
        ],
    }
    http = _FakeHttp(routes_get, routes_post)

    if skip_second:
        # Wrap .get so chan-2's PostList fetch raises a timeout (the live bug).
        original_get = http.get

        def _get_with_timeout(url, *, params=None, headers=None):
            if url.endswith("/channels/chan-2/posts"):
                http.calls.append(("GET", url))  # the attempt is still recorded
                raise httpx.ReadTimeout("simulated slow channel (45s)")
            return original_get(url, params=params, headers=headers)

        http.get = _get_with_timeout  # type: ignore[method-assign]

    return http


def _make_adapter_with_sink(http: "_FakeHttp", sink) -> MattermostSourceAdapter:
    client = MattermostReadClient(
        "https://mm.corp", "fake-pat-not-a-real-token", http_client=http, per_page=200
    )
    return MattermostSourceAdapter(
        MattermostSourceConfig(base_url="https://mm.corp"),
        _utc_time_config(),
        client=client,
        sink=sink,
    )


def test_progress_emits_one_event_per_active_channel():
    """A per-channel progress event ramps 1/N … N/N over the active denominator.

    There is a leading 0/N starting event and a trailing summary event; the
    per-channel events in between must be exactly (1,N) … (N,N) with
    unit="channels".
    """
    sink = _RecordingSink()
    adapter = _make_adapter_with_sink(_multi_channel_http(), sink)
    adapter.fetch(_DIGEST_DATE)

    n = 3
    # Per-channel events: the ramp 1/N..N/N (excludes the 0/N start + duplicate
    # final-summary emit which also reads done==N).
    ramp = [(d, t, u) for (_, d, t, u, _) in sink.progress if u == "channels" and d >= 1]
    # The per-channel events are the first N with done 1..N; the last entry is the
    # summary (also done==N). Assert the ascending 1..N prefix exists.
    per_channel = ramp[:n]
    assert [d for (d, _, _) in per_channel] == [1, 2, 3]
    assert all(t == n for (_, t, _) in per_channel)
    assert all(u == "channels" for (_, _, u) in per_channel)

    # A leading 0/N starting event was emitted (footer shows a real % at once).
    assert sink.progress[0][1] == 0 and sink.progress[0][2] == n


def test_per_channel_timeout_is_skipped_others_still_returned():
    """REGRESSION (live bug): one channel's get_posts timeout must NOT empty the
    adapter. The 1st and 3rd channels are processed; their mentions are returned;
    only the 2nd is skipped and counted."""
    sink = _RecordingSink()
    http = _multi_channel_http(skip_second=True)
    adapter = _make_adapter_with_sink(http, sink)

    messages = adapter.fetch(_DIGEST_DATE)

    kept = {m.msg_id for m in messages}
    # chan-2 timed out and was skipped; chan-1 and chan-3 still produced mentions.
    assert kept == {"mm:p-chan-1", "mm:p-chan-3"}
    assert messages, "the timeout must NOT collapse the adapter to an empty list"

    assert adapter.last_fetch_stats["channels_skipped"] == 1
    assert adapter.last_fetch_stats["channels_scanned"] == 2
    assert adapter.last_fetch_stats["mentions"] == 2

    # All three channels were ATTEMPTED (the skip did not abort the loop early).
    attempted = [
        url for (_, url) in http.calls if "/channels/chan-" in url and url.endswith("/posts")
    ]
    assert any(u.endswith("/chan-1/posts") for u in attempted)
    assert any(u.endswith("/chan-2/posts") for u in attempted)
    assert any(u.endswith("/chan-3/posts") for u in attempted)


def test_per_channel_skip_surfaced_in_progress_detail():
    """The final progress emit carries the scanned/skipped/found summary so the
    live footer shows the degrade — a skip is never invisible."""
    sink = _RecordingSink()
    adapter = _make_adapter_with_sink(_multi_channel_http(skip_second=True), sink)
    adapter.fetch(_DIGEST_DATE)

    final_detail = sink.progress[-1][4]
    assert "scanned" in final_detail
    assert "1 skipped" in final_detail
    assert "found" in final_detail


def test_last_fetch_stats_populated_full_counts():
    """No-skip happy path: last_fetch_stats carries the full count shape.

    The shape now also carries the AIMD telemetry fields (rate_limit_hits /
    retries / max_concurrency_reached). On a clean 3-channel run with no
    throttling there are no rate-limit hits and no retries; whether the ceiling
    was reached depends on the AIMD band, so it is asserted separately.
    """
    adapter = _make_adapter_with_sink(_multi_channel_http(), _RecordingSink())
    adapter.fetch(_DIGEST_DATE)
    stats = adapter.last_fetch_stats
    assert stats["channels_total"] == 3
    assert stats["channels_active"] == 3
    assert stats["channels_scanned"] == 3
    assert stats["channels_skipped"] == 0
    assert stats["mentions"] == 3
    assert stats["rate_limit_hits"] == 0
    assert stats["retries"] == 0
    assert "max_concurrency_reached" in stats


def test_nullsink_default_is_behavior_preserving():
    """With no sink the adapter behaves exactly as before (NullSink swallows
    every emit) — the 26 legacy tests rely on this."""
    adapter = _make_adapter()  # no sink passed → NullSink default
    messages = adapter.fetch(_DIGEST_DATE)
    assert len(messages) == 1  # identical to test_fetch_keeps_only_owner_mention
    # Stats are still populated even with a NullSink.
    assert adapter.last_fetch_stats["mentions"] == 1
    assert adapter.last_fetch_stats["channels_skipped"] == 0


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


# ---------------------------------------------------------------------------
# 8. Adaptive-concurrency (AIMD) parallel fetcher
#
# All offline, payload-free, and HANG-PROOF: every test uses a tiny pool, a
# fake client whose "slow" responses raise immediately (never a real 30s wait),
# and an injected/patched sleep so Retry-After back-off is instant. Each test
# asserts completion (the fetch returns), so a controller bug surfaces as a
# failed assertion, never a hung suite.
# ---------------------------------------------------------------------------


class _RateLimitedResponse:
    """A fake HTTP 429 response carrying a Retry-After header.

    Mirrors the slice of ``httpx.Response`` the client reads on the 429 path
    (``status_code`` + ``headers.get``). ``raise_for_status`` is never reached
    for a 429 because the client raises ``MattermostRateLimited`` first.
    """

    def __init__(self, retry_after: str | None = "0") -> None:
        self.status_code = 429
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}

    def raise_for_status(self):  # pragma: no cover - never reached for 429
        raise AssertionError("429 must be intercepted before raise_for_status")

    def json(self):  # pragma: no cover - never reached for 429
        raise AssertionError("429 body must not be read")


def _many_channel_routes(n: int) -> tuple[dict, dict, list[str]]:
    """Build routes for ``n`` active channels each with one @owner mention.

    Returns (routes_get, routes_post, channel_ids). Channels are staggered by
    ``last_post_at`` so the active-sort order is deterministic.
    """
    me = {"id": _OWNER_ID, "username": _OWNER_USERNAME, "email": "me@corp"}
    teams = [{"id": "team-1"}]
    cids = [f"chan-{i}" for i in range(n)]
    chans = [
        {
            "id": cid,
            "display_name": cid,
            "name": cid,
            "last_post_at": _MID_DAY_MS + (n - i) * 1000,
        }
        for i, cid in enumerate(cids)
    ]

    def _postlist(cid: str) -> dict:
        post = {
            "id": f"p-{cid}",
            "root_id": "",
            "user_id": "author-1",
            "channel_id": cid,
            "create_at": _MID_DAY_MS,
            "delete_at": 0,
            "type": "",
            "message": f"{_OWNER_HANDLE} please review {cid}",
        }
        return {"order": [f"p-{cid}"], "posts": {f"p-{cid}": post}}

    routes_get = {
        "/api/v4/users/me": me,
        "/api/v4/users/me/teams": teams,
        "/api/v4/users/me/teams/team-1/channels": chans,
    }
    for cid in cids:
        routes_get[f"/api/v4/channels/{cid}/posts"] = _postlist(cid)
    routes_post = {
        "/api/v4/users/ids": [
            {"id": "author-1", "username": "alice.author", "email": "alice@corp"},
        ],
    }
    return routes_get, routes_post, cids


class _ProgrammableHttp(_FakeHttp):
    """A fake http whose per-channel GET can be programmed to 429 / timeout.

    ``rate_limit_plan`` maps a channel id → number of leading 429s before the
    real PostList is served. ``timeout_channels`` is a set of channel ids whose
    GET always raises ``httpx.ReadTimeout`` (simulated instantly — no real wait).
    A thread-safe counter records how many times each channel was attempted.
    """

    def __init__(
        self,
        routes_get: dict,
        routes_post: dict,
        *,
        rate_limit_plan: dict[str, int] | None = None,
        timeout_channels: set[str] | None = None,
        retry_after: str | None = "0",
    ) -> None:
        super().__init__(routes_get, routes_post)
        self._rl_plan = dict(rate_limit_plan or {})
        self._timeouts = set(timeout_channels or set())
        self._retry_after = retry_after
        self._lock = __import__("threading").Lock()
        self.channel_attempts: dict[str, int] = {}

    def get(self, url, *, params=None, headers=None):
        # Identify a per-channel posts GET.
        for cid in list(self._rl_plan) + list(self._timeouts):
            if url.endswith(f"/channels/{cid}/posts"):
                with self._lock:
                    self.calls.append(("GET", url))
                    self.channel_attempts[cid] = self.channel_attempts.get(cid, 0) + 1
                    remaining_429 = self._rl_plan.get(cid, 0)
                    if remaining_429 > 0:
                        self._rl_plan[cid] = remaining_429 - 1
                        return _RateLimitedResponse(self._retry_after)
                if cid in self._timeouts:
                    raise httpx.ReadTimeout("simulated slow channel")
                # Fall through to the normal route for a served PostList.
                break
        return super().get(url, params=params, headers=headers)


def _adapter_from_http(http: _FakeHttp, **cfg_overrides) -> MattermostSourceAdapter:
    client = MattermostReadClient(
        "https://mm.corp", "fake-pat-not-a-real-token", http_client=http, per_page=200
    )
    return MattermostSourceAdapter(
        MattermostSourceConfig(base_url="https://mm.corp", **cfg_overrides),
        _utc_time_config(),
        client=client,
    )


# -- 8a. Retry-After header parsing (pure unit) -----------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5", 5.0),
        ("0.5", 0.5),
        ("  3  ", 3.0),
        (None, _DEFAULT_RETRY_AFTER_S),  # absent → fallback
        ("not-a-number", _DEFAULT_RETRY_AFTER_S),  # HTTP-date / garbage → fallback
        ("0", _DEFAULT_RETRY_AFTER_S),  # zero clamps to the fallback (never sleep 0)
        ("-2", _DEFAULT_RETRY_AFTER_S),  # negative clamps to the fallback
        ("30", 30.0),  # under the ceiling → unchanged
        ("3600", _MAX_RETRY_AFTER_S),  # huge value clamps to the ceiling (no coordinator freeze)
    ],
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


def test_client_raises_rate_limited_on_429():
    """A 429 response → MattermostRateLimited(retry_after) from the client."""

    class _Http429:
        def get(self, url, *, params=None, headers=None):
            return _RateLimitedResponse("7")

        def post(self, url, *, json=None, headers=None):  # pragma: no cover
            return _FakeResponse([])

    client = MattermostReadClient("https://mm.corp", "fake", http_client=_Http429())
    with pytest.raises(MattermostRateLimited) as ei:
        client.get_posts("chan-x")
    assert ei.value.retry_after == 7.0


# -- 8b. Correctness / equivalence to the sequential path -------------------


def _sequential_mention_ids(routes_get, routes_post) -> set[str]:
    """Compute the mention set the way the OLD sequential loop would, via a
    single-worker, no-retry adaptive fetch (concurrency=1 is the sequential
    degenerate case) — the reference set for the equivalence assertion."""
    http = _FakeHttp(dict(routes_get), dict(routes_post))
    adapter = _adapter_from_http(http, min_concurrency=1, max_concurrency=1)
    return {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}


def test_adaptive_returns_same_mention_set_as_sequential():
    """Concurrency changes HOW fast, never WHAT: the parallel fetch returns the
    SAME set of mentions as the single-worker (sequential) path."""
    routes_get, routes_post, cids = _many_channel_routes(8)

    reference = _sequential_mention_ids(routes_get, routes_post)
    assert reference == {f"mm:p-{cid}" for cid in cids}  # sanity: all 8 found

    http = _FakeHttp(dict(routes_get), dict(routes_post))
    adapter = _adapter_from_http(http, min_concurrency=2, max_concurrency=6)
    parallel = {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}

    assert parallel == reference


# -- 8c. AIMD additive-increase ramp ----------------------------------------


def test_aimd_ramps_up_toward_max_concurrency_on_all_success():
    """With every channel succeeding, the limit ADDITIVELY increases over time
    and reaches the ceiling (additive-increase, no decrease without a 429)."""
    routes_get, routes_post, _ = _many_channel_routes(30)
    http = _FakeHttp(routes_get, routes_post)
    fetcher = _AdaptiveChannelFetcher(
        channels=http._routes_get["/api/v4/users/me/teams/team-1/channels"],
        total_active=30,
        fetch_channel=lambda ch: _ChannelResult(kept_posts=[], author_ids=set()),
        sink=_RecordingSink(),
        min_concurrency=2,
        max_concurrency=8,
        max_retries_per_channel=2,
        sleep=lambda _s: None,
    )
    outcome = fetcher.run()

    assert outcome.channels_scanned == 30
    assert outcome.max_concurrency_reached is True
    # Strictly non-decreasing ramp from the floor toward the ceiling, capped.
    hist = outcome.limit_history
    assert hist[0] == 3  # first success: 2 -> 3 (additive +1 from the floor)
    assert max(hist) == 8  # reaches the ceiling
    assert hist == sorted(hist)  # monotonic non-decreasing (no 429 ⇒ no decrease)
    assert all(v <= 8 for v in hist)  # never exceeds the cap


# -- 8d. 429 multiplicative-decrease + Retry-After honored + retry success ---


def test_aimd_429_backs_off_and_retries_to_success():
    """A channel that 429s (with Retry-After) → the limit DROPS multiplicatively,
    the hit is counted, the channel is retried (Retry-After honored via injected
    sleep), and the final result still includes that channel's mention."""
    routes_get, routes_post, cids = _many_channel_routes(12)
    # The first 11 channels succeed (ramp the limit up), then chan-11 429s once
    # before succeeding — so a decrease is observable against a raised limit.
    http = _ProgrammableHttp(
        routes_get,
        routes_post,
        rate_limit_plan={"chan-11": 1},
        retry_after="0.01",  # tiny; sleep is patched to a no-op below anyway
    )
    slept: list[float] = []
    fetcher = _AdaptiveChannelFetcher(
        channels=http._routes_get["/api/v4/users/me/teams/team-1/channels"],
        total_active=12,
        fetch_channel=lambda ch: _fetch_via_client(http, ch),
        sink=_RecordingSink(),
        min_concurrency=2,
        max_concurrency=8,
        max_retries_per_channel=2,
        sleep=slept.append,  # record the back-off; never actually sleep
    )
    outcome = fetcher.run()

    assert outcome.rate_limit_hits == 1
    assert outcome.retries == 1
    # Retry-After was honored (the controller slept the parsed window once).
    assert slept == [0.01]
    # Multiplicative decrease is visible: some completion halved the limit, so the
    # history is NOT globally monotonic (unlike the all-success ramp).
    hist = outcome.limit_history
    assert any(hist[i + 1] < hist[i] for i in range(len(hist) - 1)), hist
    # chan-11 was attempted twice (429 then success) and ultimately fetched.
    assert http.channel_attempts["chan-11"] == 2
    kept_ids = {p.get("id") for p, _ in outcome.kept_posts}
    assert "p-chan-11" in kept_ids
    assert len(outcome.kept_posts) == 12  # every channel's mention present


def _fetch_via_client(http: _FakeHttp, channel: dict) -> _ChannelResult:
    """Worker that exercises the REAL client path (so 429 detection fires).

    Mirrors ``MattermostSourceAdapter._fetch_channel`` but standalone for the
    direct-fetcher tests: pages one channel via the real client and keeps the
    owner mention. The client raises ``MattermostRateLimited`` on a 429.
    """
    client = MattermostReadClient("https://mm.corp", "fake", http_client=http, per_page=200)
    mention_re = _mention_regex(_OWNER_USERNAME)
    start_ms = int(datetime(2026, 3, 29, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(2026, 3, 29, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000) + 1
    kept: list = []
    authors: set = set()
    page = 0
    while True:
        pl = client.get_posts(channel["id"], page=page, per_page=200)
        order = (pl or {}).get("order") or []
        posts = (pl or {}).get("posts") or {}
        for pid in order:
            post = posts.get(pid) or {}
            ca = int(post.get("create_at") or 0)
            if ca < start_ms or ca >= end_ms:
                continue
            if mention_re.search(post.get("message") or ""):
                kept.append((post, channel))
                if post.get("user_id"):
                    authors.add(post["user_id"])
        if len(order) < 200:
            break
        page += 1
    return _ChannelResult(kept_posts=kept, author_ids=authors)


def test_aimd_429_through_full_adapter_fetch():
    """End-to-end through ``adapter.fetch()``: a 429 channel is retried to success
    and its mention lands in the returned messages (Retry-After patched fast)."""
    routes_get, routes_post, cids = _many_channel_routes(5)
    http = _ProgrammableHttp(
        routes_get, routes_post, rate_limit_plan={"chan-3": 1}, retry_after="0.01"
    )
    adapter = _adapter_from_http(http, min_concurrency=2, max_concurrency=4)
    messages = adapter.fetch(_DIGEST_DATE)

    kept = {m.msg_id for m in messages}
    assert kept == {f"mm:p-{cid}" for cid in cids}  # all 5, incl. the 429'd one
    stats = adapter.last_fetch_stats
    assert stats["rate_limit_hits"] == 1
    assert stats["retries"] == 1
    assert stats["channels_skipped"] == 0
    assert stats["channels_scanned"] == 5


def test_aimd_429_exhausts_retries_then_skips():
    """A channel that 429s MORE than max_retries is eventually skipped+counted
    (the rate-limit path also honors the retry budget, not infinite retry)."""
    routes_get, routes_post, cids = _many_channel_routes(4)
    # chan-2 429s on every attempt (1 initial + 2 retries = 3 attempts) → skip.
    http = _ProgrammableHttp(
        routes_get, routes_post, rate_limit_plan={"chan-2": 99}, retry_after="0.01"
    )
    adapter = _adapter_from_http(
        http, min_concurrency=2, max_concurrency=4, max_retries_per_channel=2
    )
    messages = adapter.fetch(_DIGEST_DATE)

    kept = {m.msg_id for m in messages}
    assert "mm:p-chan-2" not in kept  # exhausted → skipped
    assert len(kept) == 3  # the other three still returned
    stats = adapter.last_fetch_stats
    assert stats["channels_skipped"] == 1
    assert stats["rate_limit_hits"] == 3  # 1 initial + 2 retry attempts all 429'd
    assert http.channel_attempts["chan-2"] == 3  # initial + 2 retries


# -- 8e. Timeout resilience (regression: slow channel ≠ rate signal) ---------


def test_aimd_timeout_retried_then_skipped_others_fetched():
    """A channel that always times out is retried ``max_retries`` then SKIPPED;
    the limit is NOT decreased (a timeout is not back-pressure); other channels
    are still fetched and the result is non-empty."""
    routes_get, routes_post, cids = _many_channel_routes(6)
    http = _ProgrammableHttp(routes_get, routes_post, timeout_channels={"chan-2"})
    adapter = _adapter_from_http(
        http, min_concurrency=2, max_concurrency=4, max_retries_per_channel=2
    )
    messages = adapter.fetch(_DIGEST_DATE)

    kept = {m.msg_id for m in messages}
    assert "mm:p-chan-2" not in kept  # timed out on every attempt → skipped
    assert len(kept) == 5  # the other five still produced mentions
    assert messages, "a timeout must NOT collapse the adapter to empty"
    stats = adapter.last_fetch_stats
    assert stats["channels_skipped"] == 1
    assert stats["channels_scanned"] == 5
    assert stats["rate_limit_hits"] == 0  # a timeout is NOT a rate-limit hit
    assert stats["retries"] == 2  # chan-2 retried twice before the skip
    assert http.channel_attempts["chan-2"] == 3  # initial + 2 retries


def test_aimd_timeout_does_not_decrease_limit():
    """Directly assert the AIMD invariant: a timeout leaves ``limit`` unchanged
    (only a 429 triggers multiplicative-decrease)."""

    def _worker(ch):
        if ch["id"] == "chan-3":
            raise httpx.ReadTimeout("slow")
        return _ChannelResult(kept_posts=[], author_ids=set())

    routes_get, routes_post, _ = _many_channel_routes(20)
    fetcher = _AdaptiveChannelFetcher(
        channels=routes_get["/api/v4/users/me/teams/team-1/channels"],
        total_active=20,
        fetch_channel=_worker,
        sink=_RecordingSink(),
        min_concurrency=2,
        max_concurrency=8,
        max_retries_per_channel=2,
        sleep=lambda _s: None,
    )
    outcome = fetcher.run()
    # No 429 anywhere ⇒ the limit history is monotonic non-decreasing despite the
    # timeout+retries on chan-3 (the timeout never shrank the limit).
    assert outcome.limit_history == sorted(outcome.limit_history)
    assert outcome.rate_limit_hits == 0
    assert outcome.channels_skipped == 1


# -- 8f. No deadlock: mixed success / 429 / timeout, more chans than workers --


def test_aimd_no_deadlock_mixed_workload():
    """A mixed workload (success + 429 + timeout) across MORE channels than
    ``max_concurrency`` completes and returns within the test — proving the
    coordinator loop terminates (work+retry empty AND in_flight==0) and never
    hangs. A controller bug would surface here as a hung future / failed
    completion assertion, not a frozen suite."""
    routes_get, routes_post, cids = _many_channel_routes(40)  # >> max_concurrency
    http = _ProgrammableHttp(
        routes_get,
        routes_post,
        rate_limit_plan={"chan-5": 1, "chan-17": 2, "chan-31": 1},
        timeout_channels={"chan-9", "chan-22"},
        retry_after="0.001",
    )
    adapter = _adapter_from_http(
        http, min_concurrency=2, max_concurrency=6, max_retries_per_channel=2
    )
    messages = adapter.fetch(_DIGEST_DATE)

    kept = {m.msg_id for m in messages}
    # 2 channels time out on every attempt → skipped; the other 38 are fetched
    # (the 429'd ones recover within their retry budget).
    assert "mm:p-chan-9" not in kept
    assert "mm:p-chan-22" not in kept
    assert len(kept) == 38
    stats = adapter.last_fetch_stats
    assert stats["channels_skipped"] == 2
    assert stats["channels_scanned"] == 38
    assert stats["rate_limit_hits"] == 4  # 1 + 2 + 1 across the three 429 channels
    # done covers every active channel exactly once (numerator == denominator).
    assert stats["channels_scanned"] + stats["channels_skipped"] == 40


def test_aimd_empty_channel_set_terminates():
    """Degenerate input: zero active channels → the loop terminates immediately
    with an empty result (no work, no in-flight, no hang)."""
    fetcher = _AdaptiveChannelFetcher(
        channels=[],
        total_active=0,
        fetch_channel=lambda ch: _ChannelResult(kept_posts=[], author_ids=set()),
        sink=_RecordingSink(),
        min_concurrency=2,
        max_concurrency=8,
        max_retries_per_channel=2,
        sleep=lambda _s: None,
    )
    outcome = fetcher.run()
    assert outcome.kept_posts == []
    assert outcome.channels_scanned == 0
    assert outcome.done == 0


def test_aimd_malformed_channel_counted_not_fetched():
    """A channel object with no id still advances the % denominator (done) but is
    never submitted as work (mirrors the old loop's malformed-channel handling)."""
    good = {"id": "chan-0", "display_name": "ok", "name": "ok", "last_post_at": _MID_DAY_MS}
    bad = {"display_name": "no-id", "last_post_at": _MID_DAY_MS}  # missing id
    calls: list[str] = []

    def _worker(ch):
        calls.append(ch["id"])
        return _ChannelResult(kept_posts=[], author_ids=set())

    fetcher = _AdaptiveChannelFetcher(
        channels=[good, bad],
        total_active=2,
        fetch_channel=_worker,
        sink=_RecordingSink(),
        min_concurrency=2,
        max_concurrency=4,
        max_retries_per_channel=2,
        sleep=lambda _s: None,
    )
    outcome = fetcher.run()
    assert calls == ["chan-0"]  # only the well-formed channel was fetched
    assert outcome.done == 2  # but BOTH count toward the denominator
    assert outcome.channels_scanned == 1


# ---------------------------------------------------------------------------
# 9. Allowlisted channels — general-post ingest on top of mentions (§2.3)
#
# All offline + payload-free. The channels phase is OFF by default (empty
# allowlist), so the headline test is the EQUIVALENCE guard: with no allowlist
# the ingested set is identical to the mentions-only behavior. The rest cover
# general-post ingest, id/name/display matching, and the per-channel cap.
# ---------------------------------------------------------------------------


def _allowlist_channel_http() -> "_FakeHttp":
    """Two active channels, each carrying ONE @owner mention + general posts.

    ``chan-allow`` (display_name "Release Train", name "release-train") and
    ``chan-other`` (display_name "Watercooler", name "watercooler"). Each channel
    has, in its window: one @owner mention, one general post mentioning someone
    else, one plain general post. System/bot/tombstone posts are NOT added here
    (their filtering is covered by the existing suite); this fixture isolates the
    mention-vs-general distinction.
    """
    me = {"id": _OWNER_ID, "username": _OWNER_USERNAME, "email": "me@corp"}
    teams = [{"id": "team-1"}]

    def _chan(cid: str, display: str, name: str, last: int) -> dict:
        return {"id": cid, "display_name": display, "name": name, "last_post_at": last}

    chans = [
        _chan("chan-allow", "Release Train", "release-train", _MID_DAY_MS + 2000),
        _chan("chan-other", "Watercooler", "watercooler", _MID_DAY_MS + 1000),
    ]

    def _posts(cid: str) -> dict:
        # newest-first order: mention, then two general posts.
        mention = {
            "id": f"p-{cid}-mention",
            "root_id": "",
            "user_id": "author-1",
            "channel_id": cid,
            "create_at": _MID_DAY_MS + 300,
            "delete_at": 0,
            "type": "",
            "message": f"{_OWNER_HANDLE} please review {cid}",
        }
        general_other = {
            "id": f"p-{cid}-gen-other",
            "root_id": "",
            "user_id": "author-2",
            "channel_id": cid,
            "create_at": _MID_DAY_MS + 200,
            "delete_at": 0,
            "type": "",
            "message": "@someone.else can you take the on-call rotation tonight?",
        }
        general_plain = {
            "id": f"p-{cid}-gen-plain",
            "root_id": "",
            "user_id": "author-2",
            "channel_id": cid,
            "create_at": _MID_DAY_MS + 100,
            "delete_at": 0,
            "type": "",
            "message": "Deploy window confirmed for 15:00, rollback plan attached.",
        }
        order = [mention["id"], general_other["id"], general_plain["id"]]
        return {
            "order": order,
            "posts": {
                mention["id"]: mention,
                general_other["id"]: general_other,
                general_plain["id"]: general_plain,
            },
        }

    routes_get = {
        "/api/v4/users/me": me,
        "/api/v4/users/me/teams": teams,
        "/api/v4/users/me/teams/team-1/channels": chans,
        "/api/v4/channels/chan-allow/posts": _posts("chan-allow"),
        "/api/v4/channels/chan-other/posts": _posts("chan-other"),
    }
    routes_post = {
        "/api/v4/users/ids": [
            {"id": "author-1", "username": "alice.author", "email": "alice@corp"},
            {"id": "author-2", "username": "bob.author", "email": "bob@corp"},
        ],
    }
    return _FakeHttp(routes_get, routes_post)


def _make_allowlist_adapter(http: "_FakeHttp", **cfg_overrides) -> MattermostSourceAdapter:
    client = MattermostReadClient(
        "https://mm.corp", "fake-pat-not-a-real-token", http_client=http, per_page=200
    )
    return MattermostSourceAdapter(
        MattermostSourceConfig(base_url="https://mm.corp", **cfg_overrides),
        _utc_time_config(),
        client=client,
    )


# -- 9a. Normalization + matching (pure unit) -------------------------------


def test_normalize_allowlist_trims_lowercases_drops_blanks():
    """Entries are trimmed + lowercased once; blank/empty entries are dropped."""
    norm = _normalize_allowlist(["  Release-Train ", "WATERCOOLER", "", "   "])
    assert norm == frozenset({"release-train", "watercooler"})


@pytest.mark.parametrize(
    "channel,allowlist,expected",
    [
        # id exact (case-insensitive)
        ({"id": "Chan-Allow"}, frozenset({"chan-allow"}), True),
        # name match
        ({"id": "x", "name": "release-train"}, frozenset({"release-train"}), True),
        # display_name match (case-insensitive, the operator typed lowercase)
        ({"id": "x", "display_name": "Release Train"}, frozenset({"release train"}), True),
        # no match
        ({"id": "x", "name": "watercooler"}, frozenset({"release-train"}), False),
        # empty allowlist never matches (channels phase OFF)
        ({"id": "chan-allow", "name": "release-train"}, frozenset(), False),
    ],
)
def test_channel_is_allowlisted_matching(channel, allowlist, expected):
    assert _channel_is_allowlisted(channel, allowlist) is expected


# -- 9b. Empty allowlist = behavior-preserving (the regression guard) --------


def test_empty_allowlist_ingests_exactly_mentions():
    """REGRESSION GUARD: with an empty allowlist the ingested SET is identical to
    today's mentions-only behavior — exactly the @owner posts, no general posts."""
    http = _allowlist_channel_http()
    adapter = _make_allowlist_adapter(http)  # channel_allowlist defaults to []
    messages = adapter.fetch(_DIGEST_DATE)

    kept = {m.msg_id for m in messages}
    # Exactly the two mentions (one per channel); NO general post ingested.
    assert kept == {"mm:p-chan-allow-mention", "mm:p-chan-other-mention"}
    # And each is addressed-to-me (the mention semantics are unchanged).
    assert all(m.to_recipients and m.to_recipients[0] == _OWNER_HANDLE for m in messages)


# -- 9c. Allowlisted channel ingests general posts (as context) --------------


def test_allowlisted_channel_ingests_general_posts_as_context():
    """An allowlisted channel ingests ALL its in-window posts: the @mention is
    addressed-to-me (owner in to_recipients), the general posts are CONTEXT
    (to_recipients empty). A non-allowlisted channel still yields ONLY its
    mention."""
    http = _allowlist_channel_http()
    adapter = _make_allowlist_adapter(http, channel_allowlist=["release-train"])
    messages = adapter.fetch(_DIGEST_DATE)
    by_id = {m.msg_id: m for m in messages}

    # chan-allow: all three posts ingested (mention + two general).
    assert "mm:p-chan-allow-mention" in by_id
    assert "mm:p-chan-allow-gen-other" in by_id
    assert "mm:p-chan-allow-gen-plain" in by_id
    # chan-other (NOT allowlisted): only its mention.
    assert "mm:p-chan-other-mention" in by_id
    assert "mm:p-chan-other-gen-other" not in by_id
    assert "mm:p-chan-other-gen-plain" not in by_id

    # The mention is addressed-to-me (owner identity in to_recipients).
    assert by_id["mm:p-chan-allow-mention"].to_recipients[0] == _OWNER_HANDLE
    # The general posts are context: to_recipients EMPTY (FYI, not "My actions").
    assert by_id["mm:p-chan-allow-gen-other"].to_recipients == []
    assert by_id["mm:p-chan-allow-gen-plain"].to_recipients == []
    # Field map is otherwise identical for a general post (mm: id, channel subject).
    gen = by_id["mm:p-chan-allow-gen-plain"]
    assert gen.source == "mm"
    assert gen.subject == "Release Train"  # channel display_name
    assert gen.from_name == "@bob.author"


def test_allowlist_matches_by_id_and_display_name_case_insensitive():
    """The allowlist matches by id, name, or display_name (case-insensitive).

    Allowlist ``chan-allow`` (the id) + ``WATERCOOLER`` (the name, upper-cased) →
    BOTH channels are allowlisted, so both ingest their general posts."""
    http = _allowlist_channel_http()
    adapter = _make_allowlist_adapter(http, channel_allowlist=["chan-allow", "WATERCOOLER"])
    kept = {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}
    # Both channels fully ingested (mentions + general posts).
    assert kept == {
        "mm:p-chan-allow-mention",
        "mm:p-chan-allow-gen-other",
        "mm:p-chan-allow-gen-plain",
        "mm:p-chan-other-mention",
        "mm:p-chan-other-gen-other",
        "mm:p-chan-other-gen-plain",
    }


def test_allowlist_matches_by_display_name():
    """A display_name allowlist entry (with a space, case-insensitive) matches."""
    http = _allowlist_channel_http()
    adapter = _make_allowlist_adapter(http, channel_allowlist=["release train"])
    kept = {m.msg_id for m in adapter.fetch(_DIGEST_DATE)}
    assert "mm:p-chan-allow-gen-plain" in kept  # general post ingested → matched
    assert "mm:p-chan-other-gen-plain" not in kept  # other channel untouched


# -- 9d. Per-channel cap on general posts (mentions exempt) ------------------


def _capped_channel_http(n_general: int) -> "_FakeHttp":
    """One allowlisted channel with ``n_general`` general posts AND one @mention
    placed LAST in order (oldest), so a too-tight cap would exclude it unless
    mentions are exempt from the cap."""
    me = {"id": _OWNER_ID, "username": _OWNER_USERNAME, "email": "me@corp"}
    teams = [{"id": "team-1"}]
    chan = {
        "id": "chan-busy",
        "display_name": "busy",
        "name": "busy",
        "last_post_at": _MID_DAY_MS + 10_000,
    }

    posts: dict = {}
    order: list[str] = []
    # General posts newest-first (highest create_at first).
    for i in range(n_general):
        pid = f"p-gen-{i}"
        posts[pid] = {
            "id": pid,
            "root_id": "",
            "user_id": "author-2",
            "channel_id": "chan-busy",
            "create_at": _MID_DAY_MS + 5000 - i,  # strictly decreasing, in-window
            "delete_at": 0,
            "type": "",
            "message": f"status update number {i}",
        }
        order.append(pid)
    # The @mention is the OLDEST in-window post (last in order) — past the cap.
    mention_id = "p-mention-late"
    posts[mention_id] = {
        "id": mention_id,
        "root_id": "",
        "user_id": "author-1",
        "channel_id": "chan-busy",
        "create_at": _MID_DAY_MS + 1,  # still in-window, but oldest
        "delete_at": 0,
        "type": "",
        "message": f"{_OWNER_HANDLE} ping at the end of the page",
    }
    order.append(mention_id)

    routes_get = {
        "/api/v4/users/me": me,
        "/api/v4/users/me/teams": teams,
        "/api/v4/users/me/teams/team-1/channels": [chan],
        "/api/v4/channels/chan-busy/posts": {"order": order, "posts": posts},
    }
    routes_post = {
        "/api/v4/users/ids": [
            {"id": "author-1", "username": "alice.author", "email": "alice@corp"},
            {"id": "author-2", "username": "bob.author", "email": "bob@corp"},
        ],
    }
    return _FakeHttp(routes_get, routes_post)


def _capture_cap_logs(adapter):
    """Run ``adapter.fetch`` with structlog pinned to stdlib routing and capture
    the cap log line(s).

    The cap log is asserted via stdlib ``logging`` (structlog → ``LoggerFactory``)
    so the capture is deterministic regardless of test-suite ordering — some other
    test configures structlog globally, so pinning it here makes this test
    self-contained. Returns the concatenated rendered log text (payload-free).
    """
    import logging

    from digest_core.observability.logs import _configure_structlog

    _configure_structlog()  # pin structlog → stdlib JSON renderer (idempotent)

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        messages = adapter.fetch(_DIGEST_DATE)
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)
    rendered = "\n".join(r.getMessage() for r in records)
    return messages, rendered


def test_allowlisted_channel_caps_general_posts_but_keeps_mentions():
    """An allowlisted channel with > max_posts_per_channel general posts caps the
    GENERAL posts at the limit (newest kept), but the @mention beyond the cap is
    STILL kept, and the cap is logged once (payload-free)."""
    http = _capped_channel_http(n_general=10)
    adapter = _make_allowlist_adapter(
        http, channel_allowlist=["chan-busy"], max_posts_per_channel=3
    )
    messages, log_text = _capture_cap_logs(adapter)
    kept = {m.msg_id for m in messages}

    # Exactly 3 general posts (the newest: gen-0, gen-1, gen-2) survive the cap.
    general_kept = {mid for mid in kept if mid.startswith("mm:p-gen-")}
    assert general_kept == {"mm:p-gen-0", "mm:p-gen-1", "mm:p-gen-2"}
    # The @mention is kept DESPITE being past the cap (mentions are exempt).
    assert "mm:p-mention-late" in kept
    # Total = 3 general + 1 mention.
    assert len(kept) == 4

    # The cap was logged, payload-free: counts + truncated channel id, no text.
    assert "general posts capped" in log_text
    assert '"dropped": 7' in log_text and '"kept": 3' in log_text
    assert '"channel_id": "chan-bus"' in log_text  # truncated id (8 chars)
    # No message text leaks into the log.
    assert "status update number" not in log_text


def test_cap_not_applied_when_under_limit():
    """Under the cap, every general post is kept and NO cap log is emitted."""
    http = _capped_channel_http(n_general=2)
    adapter = _make_allowlist_adapter(
        http, channel_allowlist=["chan-busy"], max_posts_per_channel=5
    )
    messages, log_text = _capture_cap_logs(adapter)
    kept = {m.msg_id for m in messages}
    assert {"mm:p-gen-0", "mm:p-gen-1", "mm:p-mention-late"} <= kept
    assert "general posts capped" not in log_text
