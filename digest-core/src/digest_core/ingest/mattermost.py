"""Read-only Mattermost SOURCE adapter — @-mentions of the owner (P1b).

This module fetches the highest-signal chat slice — posts that **@-mention the
owner** within the digest window — and maps them to ``NormalizedMessage`` rows
with ``source="mm"`` so they flow through the same pipeline as email. It is the
"MM as a source / mentions slice" design (``docs/research/
MATTERMOST_INTEGRATION_DESIGN.md`` §2.1, §2.3, and the Appendix verdict ledger),
built on the validated PAT facts in ``MATTERMOST_PAT_INTEGRATION.md``.

Read-only contract (the MM analogue of the EWS no-``.save()`` discipline):
this client issues ONLY GETs plus the two read-only POSTs that are verified
non-mutating (``/posts/search`` is not used here; ``/users/ids`` is a
POST-as-query batch read). It NEVER:

  * marks a channel read (``POST /channels/members/{id}/view`` — ViewChannel),
  * posts, edits, deletes, or reacts (any ``POST``/``PUT``/``DELETE`` /posts,
    /reactions),
  * opens a websocket (which would flip the owner perpetually online and
    suppress their own DM/@mention emails), or
  * sets presence/status/typing.

Mention detection is a CLIENT-SIDE parse of ``post.message`` (the ``/posts/
search`` "@handle as a mention feed" approach is REFUTED in the design: REST
posts carry no server-computed mention list, and token search drops dash/short/
stop-word handles and false-positives on literal text). We pull posts already
returned by ``GET /channels/{id}/posts`` and keep a post iff the owner's
``@username`` appears at a word boundary, case-insensitive.

The authenticated REST API is corp-network-only (the edge proxy 403s any
external Bearer call), so this adapter is validated OFFLINE against mocks and
exercised LIVE only from inside the corp network (ADR-012, "code outside, run
inside"). Inside corp, the offline-equivalent dry run is::

    MM_PAT=... MM_BASE_URL=https://mm.corp \\
        cli run --sources mm --dry-run

which exercises the real fetch + normalize with NO LLM call and NO delivery.

Scope is MENTIONS ONLY. General-channel ingest, DMs, the consent primitive,
reaction harvest, and the chat-extraction prompt are later phases (§8).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

import httpx
import structlog

from digest_core.config import MattermostSourceConfig, TimeConfig
from digest_core.ingest.ews import NormalizedMessage

logger = structlog.get_logger()


class _HttpClient(Protocol):
    """The slice of ``httpx.Client`` the adapter needs (so tests inject a fake)."""

    def get(self, url: str, *, params: Optional[dict] = ...) -> "httpx.Response": ...

    def post(self, url: str, *, json: object = ...) -> "httpx.Response": ...


class MattermostReadClient:
    """A thin, READ-ONLY Mattermost v4 API client.

    Only the read methods the mentions slice needs are implemented. The HTTP
    client is injected so tests can pass a fake with no network (and so the
    read-only contract is verifiable: there is no post/put/delete method here
    beyond the verified-non-mutating ``/users/ids`` batch read).
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http_client: _HttpClient,
        per_page: int = 200,
    ) -> None:
        if not base_url:
            raise ValueError("Mattermost base_url is required")
        if not token:
            raise ValueError("Mattermost token is required")
        self._base = base_url.rstrip("/")
        self._api = f"{self._base}/api/v4"
        # Never log the token; it lives only on the Authorization header.
        self._auth = {"Authorization": f"Bearer {token}"}
        self._http = http_client
        self._per_page = max(1, min(int(per_page), 200))

    # -- low-level ---------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> object:
        resp = self._http.get(f"{self._api}{path}", params=params, headers=self._auth)
        resp.raise_for_status()
        return resp.json()

    def _post_read(self, path: str, json: object) -> object:
        """POST used purely as a batch READ (``/users/ids``) — verified non-mutating.

        This is the ONLY POST the client makes; it does not create or change any
        server-side state (cf. ``POST /channels/direct``, which DOES create a DM
        and is deliberately absent here).
        """
        resp = self._http.post(f"{self._api}{path}", json=json, headers=self._auth)
        resp.raise_for_status()
        return resp.json()

    # -- read methods ------------------------------------------------------

    def get_me(self) -> dict:
        """GET /users/me — the owner's id + username (admin-free, validated)."""
        return self._get("/users/me")  # type: ignore[return-value]

    def get_my_teams(self) -> List[dict]:
        """GET /users/me/teams — teams the owner belongs to."""
        teams = self._get("/users/me/teams")
        return list(teams or [])

    def get_my_channels(self) -> List[dict]:
        """Collect the owner's channels across all their teams.

        GET /users/me/teams then GET /users/me/teams/{team_id}/channels per team.
        Channels are de-duplicated by id (a channel can appear once per team, but
        the owner could in principle see overlaps); ``last_post_at`` is preserved
        for the activity pre-gate.
        """
        channels: Dict[str, dict] = {}
        for team in self.get_my_teams():
            team_id = team.get("id")
            if not team_id:
                continue
            team_channels = self._get(f"/users/me/teams/{team_id}/channels")
            for ch in team_channels or []:
                cid = ch.get("id")
                if cid and cid not in channels:
                    channels[cid] = ch
        return list(channels.values())

    def get_posts(
        self,
        channel_id: str,
        *,
        page: int = 0,
        per_page: Optional[int] = None,
    ) -> dict:
        """GET /channels/{id}/posts — one PostList page (newest-first ``order``).

        Returns the raw ``PostList`` shape ``{"order": [...ids], "posts": {id: post}}``.
        There is NO server-side ``create_at`` filter (unlike EWS), so the caller
        windows client-side and early-stops on the ``order`` array.
        """
        pp = per_page or self._per_page
        return self._get(  # type: ignore[return-value]
            f"/channels/{channel_id}/posts",
            params={"page": page, "per_page": pp},
        )

    def get_users_by_ids(self, ids: List[str]) -> List[dict]:
        """POST /users/ids — a NON-mutating batch read of user objects.

        Despite the POST verb this is a query (the design + PAT facts confirm it
        does not mutate). Author email is best-effort: a hardened server returns a
        blank email with 200 for a non-admin PAT, so callers must tolerate "".
        """
        if not ids:
            return []
        # Stable, de-duplicated id list (deterministic request body).
        unique = sorted(set(i for i in ids if i))
        if not unique:
            return []
        users = self._post_read("/users/ids", unique)
        return list(users or [])


# Word-boundary @handle parser. ``(?<![\w])`` so "foo@me" does NOT match (an
# email-ish local part), ``\b`` after so "@meeting" does NOT match "@me". The
# handle is regex-escaped because Mattermost usernames allow ``.-_`` which are
# regex metacharacters. Case-insensitive (handles are lowercased server-side but
# quoted text may not be).
def _mention_regex(handle: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w@])@" + re.escape(handle) + r"\b", re.IGNORECASE)


def _is_system_or_bot(post: dict) -> bool:
    """True for system posts (``type`` startswith "system_") and bot posts."""
    ptype = post.get("type") or ""
    if isinstance(ptype, str) and ptype.startswith("system_"):
        return True
    props = post.get("props") or {}
    if isinstance(props, dict) and props.get("from_bot"):
        # ``from_bot`` is the string "true" or a bool depending on build.
        val = props.get("from_bot")
        if val is True or (isinstance(val, str) and val.lower() == "true"):
            return True
    return False


def _has_file_metadata(post: dict) -> bool:
    meta = post.get("metadata") or {}
    files = meta.get("files") if isinstance(meta, dict) else None
    return bool(files)


class MattermostSourceAdapter:
    """A ``SourceAdapter`` that surfaces @-mentions of the owner from Mattermost.

    Conforms structurally to ``ingest.source_adapter.SourceAdapter`` (the
    runtime-checkable two-member Protocol: ``name`` + ``fetch``).
    """

    name = "mm"

    def __init__(
        self,
        config: MattermostSourceConfig,
        time_config: TimeConfig,
        *,
        client: Optional[MattermostReadClient] = None,
        http_client: Optional[_HttpClient] = None,
    ) -> None:
        """Build the adapter.

        ``client`` (a ready ``MattermostReadClient``) or ``http_client`` (a raw
        httpx-like client the adapter wraps) may be injected for tests. In
        production neither is passed and the adapter constructs a real
        ``httpx.Client`` from the config (base_url + PAT from ENV).
        """
        self._config = config
        self._time_config = time_config
        if client is not None:
            self._client = client
        else:
            base_url = config.get_base_url()
            token = config.get_token()  # raises if MM_PAT unset
            http = http_client
            if http is None:
                http = httpx.Client(
                    timeout=httpx.Timeout(config.timeout_s),
                    verify=config.verify_ssl,
                )
            self._client = MattermostReadClient(
                base_url,
                token,
                http_client=http,
                per_page=config.per_page,
            )

    # -- time window -------------------------------------------------------

    def _window_ms(self, digest_date: str) -> tuple[int, int]:
        """[start_ms, end_ms) for the digest window, mirroring EWS semantics.

        Uses the shared pure ``compute_time_window`` (calendar_day vs rolling_24h,
        user/runner timezones) so MM and email share one window, then converts the
        aware-UTC bounds to int64 ms-epoch (MM timestamps are ms since epoch). This
        is a pure function — it does NOT construct an ``EWSIngest`` (whose
        ``__init__`` mutates exchangelib's global SSL context).
        """
        from digest_core.ingest.ews import compute_time_window

        start_utc, end_utc = compute_time_window(digest_date, self._time_config, lookback_hours=24)
        return int(start_utc.timestamp() * 1000), int(end_utc.timestamp() * 1000)

    # -- the SourceAdapter contract ----------------------------------------

    def fetch(self, digest_date: str) -> List[NormalizedMessage]:
        """Fetch the owner's @-mentions in the digest window.

        Flow (design §2.1 / §2.3):
          a. Resolve the owner once (GET /users/me → id + username).
          b. List the owner's channels across teams; PRE-GATE on
             ``Channel.last_post_at`` within the window (skip dead channels) and
             cap at ``max_channels`` ordered most-recent-first.
          c. Per active channel, page GET /channels/{id}/posts over the window:
             iterate the newest-first ``order`` array, EARLY-STOP when
             ``create_at < start_ms``; paginate by page-length < per_page ⇒ done.
             Skip ``delete_at>0`` tombstones and system/bot posts.
          d. Keep a post iff ``post.message`` @-mentions the owner (client-side
             word-boundary parse).
          e. Map each kept post → ``NormalizedMessage(source="mm")``; resolve
             author usernames via the batch ``/users/ids`` read.
        """
        start_ms, end_ms = self._window_ms(digest_date)

        me = self._client.get_me()
        owner_id = me.get("id") or ""
        owner_username = (me.get("username") or "").strip()
        if not owner_username:
            logger.warning("Mattermost owner has no username; no mentions can be parsed")
            return []
        owner_handle = f"@{owner_username}"
        owner_email = (me.get("email") or "").strip()  # best-effort; may be blank
        mention_re = _mention_regex(owner_username)

        channels = self._client.get_my_channels()
        active = self._active_channels(channels, start_ms, end_ms)
        logger.info(
            "Mattermost channels pre-gated",
            total=len(channels),
            active=len(active),
        )

        kept_posts: List[tuple[dict, dict]] = []  # (post, channel)
        author_ids: set[str] = set()
        for channel in active:
            channel_id = channel.get("id")
            if not channel_id:
                continue
            for post in self._iter_window_posts(channel_id, start_ms, end_ms):
                message = post.get("message") or ""
                if not mention_re.search(message):
                    continue
                kept_posts.append((post, channel))
                uid = post.get("user_id")
                if uid:
                    author_ids.add(uid)

        authors = self._resolve_authors(list(author_ids))

        messages: List[NormalizedMessage] = []
        for post, channel in kept_posts:
            messages.append(
                self._to_normalized_message(
                    post,
                    channel,
                    authors,
                    owner_id=owner_id,
                    owner_handle=owner_handle,
                    owner_email=owner_email,
                )
            )
        logger.info(
            "Mattermost mentions fetched",
            channels_active=len(active),
            mentions=len(messages),
        )
        return messages

    # -- helpers -----------------------------------------------------------

    def _active_channels(self, channels: List[dict], start_ms: int, end_ms: int) -> List[dict]:
        """Pre-gate channels on ``Channel.last_post_at`` within the window.

        Skips channels with no activity in the window (avoids paging dead
        channels — the owner can be a member of ~998). Orders the survivors
        most-recent-first and caps at ``max_channels`` so the cap keeps the
        freshest activity (design §2.3).
        """
        active = []
        for ch in channels:
            last = ch.get("last_post_at") or 0
            try:
                last = int(last)
            except (TypeError, ValueError):
                last = 0
            # Within [start_ms, end_ms): a channel whose newest post predates the
            # window has nothing for us; one whose newest post is after end_ms may
            # still have in-window posts, so keep it (client-side windowing per
            # page handles the upper bound).
            if last >= start_ms:
                active.append((last, ch))
        active.sort(key=lambda t: t[0], reverse=True)
        capped = [ch for _, ch in active[: self._config.max_channels]]
        if len(active) > self._config.max_channels:
            logger.info(
                "Mattermost channel cap hit; keeping most-recent",
                seen=len(active),
                cap=self._config.max_channels,
            )
        return capped

    def _iter_window_posts(self, channel_id: str, start_ms: int, end_ms: int):
        """Yield in-window, non-tombstone, non-system/bot posts for a channel.

        Pages newest-first; iterates the ``order`` array and EARLY-STOPS as soon
        as a post's ``create_at`` is before ``start_ms`` (the rest of the page —
        and all later pages — are older). Pagination is driven by page length
        (``len(order) < per_page ⇒ done``), NOT ``has_next``.
        """
        page = 0
        per_page = self._config.per_page
        while True:
            postlist = self._client.get_posts(channel_id, page=page, per_page=per_page)
            order = (postlist or {}).get("order") or []
            posts = (postlist or {}).get("posts") or {}
            stop = False
            for post_id in order:
                post = posts.get(post_id)
                if not post:
                    continue
                create_at = post.get("create_at") or 0
                try:
                    create_at = int(create_at)
                except (TypeError, ValueError):
                    create_at = 0
                if create_at < start_ms:
                    # order is newest-first: everything from here on is older.
                    stop = True
                    break
                if create_at >= end_ms:
                    # Newer than the window (channel still active later); skip but
                    # keep scanning toward older in-window posts.
                    continue
                if int(post.get("delete_at") or 0) > 0:
                    continue  # soft-deleted tombstone (privacy: never surfaced)
                if _is_system_or_bot(post):
                    continue
                yield post
            if stop or len(order) < per_page:
                break
            page += 1

    def _resolve_authors(self, author_ids: List[str]) -> Dict[str, dict]:
        """Batch-resolve author user objects by id (best-effort; never crashes)."""
        if not author_ids:
            return {}
        try:
            users = self._client.get_users_by_ids(author_ids)
        except Exception as exc:  # noqa: BLE001 - author resolution degrades, never fatal
            logger.warning("Mattermost author resolution failed", error=str(exc))
            return {}
        return {u.get("id"): u for u in users if u.get("id")}

    def _to_normalized_message(
        self,
        post: dict,
        channel: dict,
        authors: Dict[str, dict],
        *,
        owner_id: str,
        owner_handle: str,
        owner_email: str,
    ) -> NormalizedMessage:
        """Map a kept post → ``NormalizedMessage(source="mm")`` (design §2.1).

        addressed-to-me is wired through the SAME mechanism the email path uses:
        the owner's identity is placed in ``to_recipients`` so the splitter's
        ``addressed_to_me`` derivation (alias-in-recipients) and the ranker's
        ``user_in_to`` fire honestly — a post that @-mentions the owner genuinely
        IS addressed to the owner. (We do not invent a flag the pipeline never
        reads; we do not touch the LLM evidence header.)
        """
        post_id = post.get("id") or ""
        root_id = post.get("root_id") or ""
        conversation_id = root_id or post_id
        create_at = int(post.get("create_at") or 0)
        dt = datetime.fromtimestamp(create_at / 1000, tz=timezone.utc)

        author = authors.get(post.get("user_id") or "", {})
        author_username = (author.get("username") or "").strip()
        author_from = f"@{author_username}" if author_username else (post.get("user_id") or "")
        author_email = (author.get("email") or "").strip()  # best-effort, may be ""

        channel_name = channel.get("display_name") or channel.get("name") or channel.get("id") or ""

        # Owner identity in to_recipients (the real addressed-to-me signal). Both
        # the @handle and a resolved email (if available) are included so the
        # splitter alias match fires regardless of whether the operator configured
        # ews.user_aliases with the handle or the email.
        to_recipients = [owner_handle]
        if owner_email:
            to_recipients.append(owner_email)
        if owner_id:
            to_recipients.append(owner_id)

        return NormalizedMessage(
            msg_id=f"mm:{post_id}",
            conversation_id=conversation_id,
            datetime_received=dt,
            sender_email=author_email,
            subject=channel_name,
            text_body=post.get("message") or "",
            to_recipients=to_recipients,
            cc_recipients=[],
            importance="Normal",
            is_flagged=False,
            has_attachments=_has_file_metadata(post),
            attachment_types=[],
            from_email=author_email,
            from_name=author_from,
            source="mm",
        )
