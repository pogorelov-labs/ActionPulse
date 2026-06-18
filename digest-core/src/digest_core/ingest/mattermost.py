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

import collections
import concurrent.futures
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Deque, Dict, List, Optional, Protocol

import httpx
import structlog

from digest_core.config import MattermostSourceConfig, TimeConfig
from digest_core.ingest.ews import NormalizedMessage
from digest_core.progress import NullSink, ProgressSink, emit

logger = structlog.get_logger()

#: Fallback Retry-After when a 429 carries no parseable header (seconds). Kept
#: small so a header-less 429 still backs off but does not stall the whole run.
_DEFAULT_RETRY_AFTER_S = 1.0
#: Upper clamp on Retry-After. The sleep runs on the coordinator thread, so an
#: unbounded value (a misbehaving gateway/proxy sending Retry-After: 3600) would
#: freeze the whole MM ingest. Retries are bounded and a channel is skipped when
#: exhausted, so capping the per-sleep wait bounds worst-case wall-clock without
#: losing the back-off intent.
_MAX_RETRY_AFTER_S = 60.0


class MattermostRateLimited(Exception):
    """Raised by the read client on HTTP 429 (a rate-limit signal, not a failure).

    Carries ``retry_after`` (seconds) parsed from the ``Retry-After`` header so
    the adaptive fetcher can honor the server's back-off window. This is a
    DISTINCT exception (not a generic ``HTTPStatusError``) so the AIMD controller
    can tell a rate-limit signal — which means "slow down, then retry" — apart
    from a slow/broken channel (a timeout, which means "this channel is sick").
    """

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Mattermost rate limited (retry_after={retry_after:.1f}s)")
        self.retry_after = retry_after


def _parse_retry_after(value: object) -> float:
    """Parse a ``Retry-After`` header value into seconds (delta-seconds form).

    Mattermost emits the integer delta-seconds form (RFC 9110 §10.2.3). We accept
    a numeric string/float; anything absent or unparseable (including the rarer
    HTTP-date form) falls back to ``_DEFAULT_RETRY_AFTER_S`` so we still back off.
    A negative or zero value is clamped to the fallback (never sleep <= 0); a
    large value is clamped to ``_MAX_RETRY_AFTER_S`` so a misbehaving gateway
    cannot freeze the coordinator (the sleep runs single-threaded).
    """
    if value is None:
        return _DEFAULT_RETRY_AFTER_S
    try:
        secs = float(str(value).strip())
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER_S
    if secs <= 0:
        return _DEFAULT_RETRY_AFTER_S
    return min(secs, _MAX_RETRY_AFTER_S)


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
        self._raise_if_rate_limited(resp)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _raise_if_rate_limited(resp: object) -> None:
        """Translate an HTTP 429 into ``MattermostRateLimited`` (with Retry-After).

        A distinct rate-limit exception lets the adaptive fetcher back off and
        retry instead of treating the channel as broken. Every other status code
        (incl. other 4xx/5xx) falls through to the caller's ``raise_for_status``,
        so non-429 errors propagate exactly as before. ``status_code``/``headers``
        are read defensively: a fake response that omits them is treated as
        non-429 (preserving the legacy offline fakes that only implement
        ``raise_for_status``/``json``).
        """
        status = getattr(resp, "status_code", None)
        if status != 429:
            return
        headers = getattr(resp, "headers", None) or {}
        retry_after_raw = None
        try:
            # httpx.Headers is case-insensitive; a plain dict may not be.
            retry_after_raw = headers.get("Retry-After")
            if retry_after_raw is None and hasattr(headers, "get"):
                retry_after_raw = headers.get("retry-after")
        except Exception:  # noqa: BLE001 - header access is best-effort
            retry_after_raw = None
        raise MattermostRateLimited(_parse_retry_after(retry_after_raw))

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


#: Progress-detail channel labels are kept short (the live footer is one line).
_CHANNEL_LABEL_MAX = 32


def _short_channel(name: str) -> str:
    """Trim a channel display name for the progress detail line (footer-safe).

    The label is the owner's own channel name (not message text), but the live
    footer is a single line, so collapse whitespace and end-ellipsis past a cap.
    """
    name = " ".join((name or "").split())
    if len(name) <= _CHANNEL_LABEL_MAX:
        return name
    return name[: _CHANNEL_LABEL_MAX - 1] + "…"


class _ChannelResult:
    """The kept posts + author ids from one channel's successful fetch."""

    __slots__ = ("kept_posts", "author_ids")

    def __init__(self, kept_posts: List[tuple[dict, dict]], author_ids: set[str]) -> None:
        self.kept_posts = kept_posts
        self.author_ids = author_ids


class _FetchOutcome:
    """The aggregate result of an adaptive fetch run (returned to ``fetch()``)."""

    __slots__ = (
        "kept_posts",
        "author_ids",
        "channels_scanned",
        "channels_skipped",
        "done",
        "rate_limit_hits",
        "retries",
        "max_concurrency_reached",
        "limit_history",
    )

    def __init__(self) -> None:
        self.kept_posts: List[tuple[dict, dict]] = []
        self.author_ids: set[str] = set()
        self.channels_scanned = 0
        self.channels_skipped = 0
        self.done = 0
        self.rate_limit_hits = 0
        self.retries = 0
        self.max_concurrency_reached = False
        #: The value of ``limit`` recorded after each completion-driven AIMD
        #: adjustment (observability — used by tests to assert the ramp/back-off
        #: shape). Not surfaced in ``last_fetch_stats`` (it is debug-only).
        self.limit_history: List[int] = []


#: After a 429, suppress additive-increase for this many subsequent successful
#: completions, so the controller does not immediately re-overshoot the limit it
#: just halved. A small cooldown is the standard AIMD "don't grow into the wall
#: you just hit" guard.
_INCREASE_COOLDOWN_COMPLETIONS = 3


class _AdaptiveChannelFetcher:
    """Adaptive-concurrency (AIMD) parallel fetcher for the channel scan.

    **Why.** The sequential scan paid the full per-channel latency in series (a
    live dry-run: 6m51s over 67 channels, 9 skipped on 15s timeouts). This fetches
    channels in PARALLEL on a thread pool while *self-tuning* the in-flight limit
    to the maximum throughput the corp gateway tolerates — classic AIMD: additive
    increase on success, multiplicative decrease on HTTP 429.

    **Design.** One COORDINATOR thread owns the control loop; a
    ``ThreadPoolExecutor(max_workers=max_concurrency)`` runs the per-channel
    workers. The coordinator submits work while ``in_flight < limit`` and there is
    work (a primary work deque + a retry deque), then blocks on
    ``concurrent.futures.wait(..., FIRST_COMPLETED)`` and reacts to each finished
    future:

      * **success** → record the channel's posts; *additive increase*
        ``limit = min(max_concurrency, limit + 1)`` (unless a recent 429 cooldown
        is active); count toward progress and emit one ``on_stage_progress``.
      * **MattermostRateLimited** → *multiplicative decrease*
        ``limit = max(min_concurrency, limit // 2)``; start the increase cooldown;
        ``time.sleep(retry_after)`` (the server's window, honored); REQUEUE the
        channel (up to ``max_retries_per_channel``). The limit change does NOT
        touch the pool size (``max_workers`` is fixed at ``max_concurrency``) — it
        gates how many we keep in flight, so shrinking is immediate and free.
      * **timeout / other transient error** → NOT a rate signal, so ``limit`` is
        left UNCHANGED; requeue (up to ``max_retries_per_channel``); after retries
        are exhausted the channel is *skipped + counted* (the existing resilience:
        its mentions are lost, the run continues).

    **Thread-safety.** Every mutation of shared control state (``limit``, the
    deques, the counters, the in-flight map) happens under one ``threading.Lock``
    held by the coordinator. Worker threads ONLY run ``_fetch_channel`` (pure
    per-channel reads) and never touch the lock, the counters, or the sink — so
    the progress sink is written from a single thread and cannot race.

    **Termination (no deadlock).** The loop ends exactly when the work deque AND
    the retry deque are empty AND ``in_flight == 0``. It can never wedge: the
    coordinator never blocks except in ``futures.wait`` while ``in_flight > 0``,
    and every future resolves (success OR exception) — a stuck worker would surface
    as a hung future, but the per-request ``httpx`` timeout bounds that, after
    which the future raises and the coordinator skips+counts the channel. The
    cooldown ``sleep`` happens on the coordinator only AFTER a future resolved, so
    it cannot delay draining the pool. (A ``sleep`` injection point is provided so
    tests run instantly.)
    """

    def __init__(
        self,
        *,
        channels: List[dict],
        total_active: int,
        fetch_channel: Callable[[dict], "_ChannelResult"],
        sink: ProgressSink,
        min_concurrency: int,
        max_concurrency: int,
        max_retries_per_channel: int,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._channels = channels
        self._total_active = total_active
        self._fetch_channel = fetch_channel
        self._sink = sink
        # Clamp the AIMD band so a misconfig (min>max) can never deadlock submit.
        self._max_concurrency = max(1, int(max_concurrency))
        self._min_concurrency = max(1, min(int(min_concurrency), self._max_concurrency))
        self._max_retries = max(0, int(max_retries_per_channel))
        # Resolved at call time (not as a default arg) so a test that monkeypatches
        # ``mattermost.time.sleep`` reliably makes the back-off instant.
        self._sleep = sleep if sleep is not None else time.sleep

        self._lock = threading.Lock()
        #: Dynamic in-flight ceiling (the AIMD variable). Starts modest.
        self._limit = self._min_concurrency
        #: Suppress additive-increase for N completions after a 429.
        self._increase_cooldown = 0
        #: Primary work + requeue deques (channel, attempt_count).
        self._work: Deque[tuple[dict, int]] = collections.deque(
            (ch, 0) for ch in channels if ch.get("id")
        )
        self._retry: Deque[tuple[dict, int]] = collections.deque()
        #: Malformed channels (no id) still count toward the % denominator.
        self._malformed = sum(1 for ch in channels if not ch.get("id"))

        self._outcome = _FetchOutcome()

    # -- internal helpers (all called under self._lock or single-threaded) --

    def _next_work(self) -> Optional[tuple[dict, int]]:
        """Pop the next unit (retries first so a requeued channel resumes soon)."""
        if self._retry:
            return self._retry.popleft()
        if self._work:
            return self._work.popleft()
        return None

    def _emit_progress(self, channel_name: str, found_this_channel: int) -> None:
        """Emit one per-channel progress event (COORDINATOR thread only)."""
        emit(
            self._sink,
            "on_stage_progress",
            "ingest",
            self._outcome.done,
            self._total_active,
            "channels",
            # Live detail shows the channel, its found-count, and the live limit
            # (``↑Nw``) so the footer makes the self-tuning visible.
            detail=(
                f"#{_short_channel(channel_name)} · "
                f"{found_this_channel} found · ↑{self._limit}w"
            ),
        )

    def run(self) -> "_FetchOutcome":
        """Drive the AIMD control loop to completion and return the outcome."""
        # Malformed channels carry no work but still advance the denominator.
        self._outcome.done += self._malformed

        # The pool is sized to the hard ceiling; the dynamic ``limit`` (<= max)
        # gates how many we keep IN FLIGHT, so the pool never blocks on submit.
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self._max_concurrency,
            thread_name_prefix="mm-chan",
        ) as pool:
            in_flight: Dict[concurrent.futures.Future, tuple[dict, int]] = {}

            while True:
                # 1) Fill up to the current dynamic limit while work remains.
                while len(in_flight) < self._limit:
                    unit = self._next_work()
                    if unit is None:
                        break
                    channel, attempt = unit
                    fut = pool.submit(self._fetch_channel, channel)
                    in_flight[fut] = (channel, attempt)

                # 2) Termination: nothing running and nothing queued ⇒ done.
                if not in_flight:
                    break

                # 3) Block until at least one future resolves (never hang: every
                #    future resolves, bounded by the per-request httpx timeout).
                finished, _ = concurrent.futures.wait(
                    in_flight, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for fut in finished:
                    channel, attempt = in_flight.pop(fut)
                    self._handle_completion(fut, channel, attempt)

        return self._outcome

    def _handle_completion(
        self,
        fut: concurrent.futures.Future,
        channel: dict,
        attempt: int,
    ) -> None:
        """React to one finished future: AIMD adjust, record/requeue, progress.

        Runs ONLY on the coordinator thread (the single caller of ``run``), so the
        counters, deques, ``limit`` and sink are all touched single-threaded. The
        lock still guards ``limit``/the deques because a future's worker thread is
        gone by the time we are here, but a defensive lock keeps the invariant
        explicit and cheap.
        """
        channel_name = channel.get("display_name") or channel.get("name") or channel.get("id")
        try:
            result = fut.result()
        except MattermostRateLimited as rl:
            # Rate signal: multiplicative DECREASE + cooldown, then honor
            # Retry-After and requeue (does not consume a "skip").
            with self._lock:
                self._outcome.rate_limit_hits += 1
                self._limit = max(self._min_concurrency, self._limit // 2)
                self._increase_cooldown = _INCREASE_COOLDOWN_COMPLETIONS
                self._outcome.limit_history.append(self._limit)
            logger.warning(
                "Mattermost channel rate-limited; backing off",
                channel_id=str(channel.get("id") or "")[:8],  # truncated — payload-free
                retry_after_s=round(rl.retry_after, 2),
                new_limit=self._limit,
            )
            # Sleep the server's window on the coordinator (after the future
            # resolved, so it never delays draining the rest of the pool).
            if rl.retry_after > 0:
                self._sleep(rl.retry_after)
            self._requeue_or_skip(channel, attempt, channel_name, is_rate_limit=True)
            return
        except Exception as exc:  # noqa: BLE001 - timeout / transient: NOT a rate signal
            # A slow/broken channel. Do NOT change the limit (it is not gateway
            # back-pressure). Retry the channel; skip+count when exhausted.
            self._requeue_or_skip(channel, attempt, channel_name, is_rate_limit=False, error=exc)
            return

        # Success → record + additive INCREASE (unless a recent 429 cools it).
        with self._lock:
            self._outcome.kept_posts.extend(result.kept_posts)
            self._outcome.author_ids |= result.author_ids
            self._outcome.channels_scanned += 1
            self._outcome.done += 1
            if self._increase_cooldown > 0:
                self._increase_cooldown -= 1
            elif self._limit < self._max_concurrency:
                self._limit += 1
            if self._limit >= self._max_concurrency:
                self._outcome.max_concurrency_reached = True
            self._outcome.limit_history.append(self._limit)
            found_this_channel = len(result.kept_posts)
        self._emit_progress(channel_name, found_this_channel)

    def _requeue_or_skip(
        self,
        channel: dict,
        attempt: int,
        channel_name: str,
        *,
        is_rate_limit: bool,
        error: Optional[BaseException] = None,
    ) -> None:
        """Requeue a channel for another attempt, or skip+count when exhausted."""
        if attempt < self._max_retries:
            with self._lock:
                self._outcome.retries += 1
                self._retry.append((channel, attempt + 1))
            if not is_rate_limit:
                logger.info(
                    "Mattermost channel transient error; retrying",
                    channel_id=str(channel.get("id") or "")[:8],  # truncated
                    error_type=type(error).__name__ if error else "RateLimited",
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                )
            return
        # Retries exhausted → skip + count (resilience: run continues).
        with self._lock:
            self._outcome.channels_skipped += 1
            self._outcome.done += 1
        logger.warning(
            "Mattermost channel skipped (retries exhausted); continuing",
            channel_id=str(channel.get("id") or "")[:8],  # truncated id — payload-free
            error_type=(
                "RateLimited" if is_rate_limit else (type(error).__name__ if error else "Unknown")
            ),
        )
        self._emit_progress(channel_name, 0)


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
        sink: ProgressSink = NullSink(),
    ) -> None:
        """Build the adapter.

        ``client`` (a ready ``MattermostReadClient``) or ``http_client`` (a raw
        httpx-like client the adapter wraps) may be injected for tests. In
        production neither is passed and the adapter constructs a real
        ``httpx.Client`` from the config (base_url + PAT from ENV).

        ``sink`` receives intra-stage progress events (``on_stage_progress``) so
        the active-channel scan reports a live % (emitted from the adaptive
        fetcher's coordinator thread only); it defaults to ``NullSink()`` so
        nothing renders unless ``run.py`` threads in a real sink
        (behavior-preserving for every existing caller).
        """
        self._config = config
        self._time_config = time_config
        self._sink = sink
        #: Outcome of the last ``fetch()`` — mirrors ``EWSIngest.last_fetch_stats``
        #: so a degrade (skipped channels) is never invisible. Populated by fetch().
        self.last_fetch_stats: dict = {}
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
        total_active = len(active)
        logger.info(
            "Mattermost channels pre-gated",
            total=len(channels),
            active=total_active,
        )
        # Starting event: the scan denominator (``total_active``) is now known, so
        # the live footer can render a real percentage (0/N) before the first
        # channel is fetched — a large channel scan no longer feels hung.
        emit(
            self._sink,
            "on_stage_progress",
            "ingest",
            0,
            total_active,
            "channels",
            detail="scanning mentions",
        )

        # Adaptive-concurrency fetch: the channels are paged in PARALLEL under a
        # self-tuning (AIMD) in-flight limit. This replaces the old sequential
        # loop (a live dry-run took 6m51s over 67 channels, skipping 9 on 15s
        # timeouts). The per-channel work, resilience, and progress contract are
        # preserved exactly — only HOW channels are visited changed. Progress is
        # emitted from the COORDINATOR thread only (worker threads never touch the
        # sink), so there is no sink race.
        fetcher = _AdaptiveChannelFetcher(
            channels=active,
            total_active=total_active,
            fetch_channel=lambda channel: self._fetch_channel(
                channel, start_ms, end_ms, mention_re
            ),
            sink=self._sink,
            min_concurrency=self._config.min_concurrency,
            max_concurrency=self._config.max_concurrency,
            max_retries_per_channel=self._config.max_retries_per_channel,
        )
        result = fetcher.run()
        kept_posts = result.kept_posts
        author_ids = result.author_ids
        channels_scanned = result.channels_scanned
        channels_skipped = result.channels_skipped
        done = result.done

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

        # Record the outcome so a degrade is never invisible (mirrors EWSIngest).
        # The AIMD fields (rate_limit_hits / retries / max_concurrency_reached)
        # surface how hard the controller had to work — a high rate_limit_hits
        # means the gateway throttled us; max_concurrency_reached means we found
        # headroom and the cap (not the gateway) was the bound.
        self.last_fetch_stats = {
            "channels_total": len(channels),
            "channels_active": total_active,
            "channels_scanned": channels_scanned,
            "channels_skipped": channels_skipped,
            "mentions": len(messages),
            "rate_limit_hits": result.rate_limit_hits,
            "retries": result.retries,
            "max_concurrency_reached": result.max_concurrency_reached,
        }
        # Final progress emit carries the summary so the live footer shows skips.
        emit(
            self._sink,
            "on_stage_progress",
            "ingest",
            done,
            total_active,
            "channels",
            detail=(
                f"{channels_scanned}/{total_active} scanned · "
                f"{channels_skipped} skipped · {len(messages)} found"
            ),
        )
        # Prominent final summary: WARNING when any channel was skipped (a degrade
        # must be loud), info otherwise. Payload-free (counts only).
        log = logger.warning if channels_skipped else logger.info
        log(
            "Mattermost mentions fetched",
            channels_active=total_active,
            channels_scanned=channels_scanned,
            channels_skipped=channels_skipped,
            mentions=len(messages),
            rate_limit_hits=result.rate_limit_hits,
            retries=result.retries,
            max_concurrency_reached=result.max_concurrency_reached,
        )
        return messages

    # -- per-channel worker ------------------------------------------------

    def _fetch_channel(
        self,
        channel: dict,
        start_ms: int,
        end_ms: int,
        mention_re: "re.Pattern[str]",
    ) -> "_ChannelResult":
        """Fetch + parse ONE channel's in-window @owner mentions (worker body).

        This is the exact per-channel work the old sequential loop did, lifted
        into a function so it can run on a worker thread: page the window with
        ``_iter_window_posts`` (unchanged), keep posts that @-mention the owner,
        and collect their author ids. Returns a ``_ChannelResult`` on success;
        **raises** on any fetch error (``MattermostRateLimited`` for a 429,
        ``httpx.ReadTimeout``/other for a slow or broken channel) so the
        coordinator — not the worker — decides retry vs. skip vs. back-off. The
        worker NEVER touches shared state or the progress sink (thread-safety).
        """
        kept: List[tuple[dict, dict]] = []
        author_ids: set[str] = set()
        for post in self._iter_window_posts(channel["id"], start_ms, end_ms):
            message = post.get("message") or ""
            if not mention_re.search(message):
                continue
            kept.append((post, channel))
            uid = post.get("user_id")
            if uid:
                author_ids.add(uid)
        return _ChannelResult(kept_posts=kept, author_ids=author_ids)

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
