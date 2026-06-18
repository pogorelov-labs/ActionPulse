"""Mattermost delivery target."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

import httpx
import structlog

from digest_core.assemble.labels import (
    DEFAULT_LANGUAGE,
    FYI,
    STATUS,
    confidence_text,
    display_title,
    normalize_section,
    report_strings,
    should_show_confidence,
)
from digest_core.config import MattermostDeliverConfig
from digest_core.llm.schemas import Digest

logger = structlog.get_logger()

DEFAULT_PING_TEXT = report_strings(DEFAULT_LANGUAGE)["mm_ping_text"]

# An @-mention at a word boundary: @handle / @channel / @here / @all. The
# negative lookbehind excludes a mid-word "@" (e.g. an email address local@host),
# which Mattermost does not treat as a mention anyway.
_MENTION_RE = re.compile(r"(?<![\w/])@[A-Za-z0-9][A-Za-z0-9._-]*")


def escape_mentions(text: str) -> str:
    """Neutralize Mattermost @-mentions in evidence-derived text.

    Mattermost parses ``@handle``/``@channel``/``@here``/``@all`` out of the
    message *text* at post time and notifies those users — there is no per-post
    opt-out. A digest renders LLM-extracted titles (and, in future, quoted chat),
    so quoted content like "ping @ivan before EOD" would ping a real Ivan on
    delivery. We wrap each mention token in a backtick code span: the server-side
    mention parser skips code spans, so this is true notification suppression,
    not merely a rendering change (verified at the v11.3.0 parser level; see
    docs/research/MATTERMOST_PAT_INTEGRATION.md §5). A handle cannot contain a
    backtick, so a single-backtick fence is always valid for the token. Mid-word
    "@" (email addresses) is left untouched. Idempotent: a token already inside a
    backtick span is not re-wrapped.
    """
    if not text or "@" not in text:
        return text

    def _wrap(match: re.Match) -> str:
        start = match.start()
        # Skip if the token is already opened by a backtick (already escaped).
        if start > 0 and text[start - 1] == "`":
            return match.group(0)
        return f"`{match.group(0)}`"

    return _MENTION_RE.sub(_wrap, text)


def _blen(s: str) -> int:
    """UTF-8 byte length — Mattermost limits are in bytes, not code points."""
    return len(s.encode("utf-8"))


def ping_mattermost_webhook(
    config: MattermostDeliverConfig,
    *,
    text: str | None = None,
    timeout_s: float = 20.0,
) -> int:
    """POST a single test message; returns HTTP status on success.

    Does not log the webhook URL or message body.
    """
    webhook_url = config.get_webhook_url()
    payload_text = text if text is not None else DEFAULT_PING_TEXT
    logger.info("mattermost_webhook_ping_start")
    with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
        response = client.post(webhook_url, json={"text": payload_text})
        response.raise_for_status()
    logger.info("mattermost_webhook_ping_ok", status_code=response.status_code)
    return response.status_code


class _MattermostFormatter:
    """Shared digest → Mattermost-markdown rendering for both transports.

    The webhook and API deliverers post the SAME recipient-facing message; only
    the transport differs. Keeping the rendering here — @-escaping, byte-aware
    splitting, confidence/badge formatting — guarantees the two paths stay
    byte-identical and gives the webhook regression tests a single source of
    truth to pin. Subclasses add a ``deliver_digest`` that swaps the transport.
    """

    def __init__(self, config: MattermostDeliverConfig, language: str = DEFAULT_LANGUAGE):
        self.config = config
        self.language = language
        self._s = report_strings(language)

    def _format_digest(
        self,
        digest: Digest,
        json_path: str | None = None,
        llm_budget: dict | None = None,
    ) -> str:
        blocks: List[str] = [f"## {self._s['digest_header']} — {digest.digest_date}"]

        for section in digest.sections:
            if not section.items:
                continue
            section_lines = [f"**{display_title(section.title, self.language)}**"]
            for index, item in enumerate(section.items, start=1):
                due_part = f" | {self._s['due_label'].lower()}: {item.due}" if item.due else ""
                confidence_part = ""
                if should_show_confidence(item.confidence, getattr(item, "weak_evidence", False)):
                    confidence_part = (
                        f" | {self._s['confidence_label'].lower()}:"
                        f" {self._confidence_label(item.confidence)}"
                    )
                prefix = "-" if normalize_section(section.title) in (FYI, STATUS) else f"{index}."
                title = escape_mentions(item.title)
                section_lines.append(f"{prefix} {title}{due_part}{confidence_part}")
                trace_line = self._format_trace_line(item, json_path)
                if trace_line:
                    section_lines.append(trace_line)
            blocks.append("\n".join(section_lines))

        # Empty digest: no section had any items. Surface the "no actions"
        # block so the delivered message is not a bare header (matches the .md).
        if len(blocks) == 1:
            blocks.append(self._s["no_actions"])

        # No trace footer (owner decision C5/C8): trace_id, item count and the
        # LLM budget are operator metadata, not recipient signals. They live in
        # run_meta + the structured log; the delivered message stays clean.
        return "\n\n".join(blocks)

    def _format_trace_line(self, item, json_path: str | None) -> str:
        """Per-item recipient sub-line: user-facing badges only (owner C5/C8).

        Operator metadata (the internal ``ev: <id>`` token and the local
        ``[json](...)`` filesystem link) is stripped — the recipient cannot use
        either. What remains is recipient signal: ``⚠ <weak_basis>`` when the
        evidence is weak and ``↻ <repeat>`` when the item was seen before. An
        item with neither badge gets no sub-line. ``json_path`` is accepted for
        signature compatibility but unused.
        """
        del json_path  # operator path, no longer rendered (owner C5/C8)
        badges: List[str] = []
        if getattr(item, "weak_evidence", False):
            badges.append(f"⚠ {self._s['weak_basis']}")
        if getattr(item, "seen_before", False):
            badges.append(f"↻ {self._s['repeat']}")
        if not badges:
            return ""
        return "   ↳ " + " | ".join(badges)

    def _header_blen(self, total: int) -> int:
        """Byte length of the worst-case part header for a ``total``-part split.

        The widest ``index/total`` is ``total/total`` (most digits), so we size
        against that to guarantee every prepended header fits within budget.
        """
        header = "## " + self._s["digest_part_header"].format(index=total, total=total)
        return _blen(header) + len("\n\n")

    def _split_message(self, message: str, max_length: int) -> List[str]:
        if _blen(message) <= max_length:
            return [message]

        # The message is over budget, so it will split into >= 2 parts and each
        # delivered part will carry a "## <part i/total>" header. Reserve the
        # worst-case header byte length so a near-limit chunk does not overflow
        # once the header is prepended. The header digit count depends on the
        # chunk count, which in turn depends on the reserved space, so re-split
        # until the effective limit stabilizes (bounded: digit growth is slow).
        effective = max_length
        chunks: List[str] = []
        for _ in range(8):
            chunks = self._split_into_chunks(message, effective)
            new_effective = max_length - self._header_blen(max(len(chunks), 2))
            if new_effective == effective:
                break
            effective = new_effective

        total = len(chunks)
        if total <= 1:
            return chunks

        wrapped_chunks = []
        for index, chunk in enumerate(chunks, start=1):
            header = "## " + self._s["digest_part_header"].format(index=index, total=total)
            wrapped_chunks.append(f"{header}\n\n{chunk}")
        return wrapped_chunks

    def _split_into_chunks(self, message: str, max_length: int) -> List[str]:
        """Greedily pack blocks (then lines) into chunks of <= ``max_length`` bytes."""
        blocks = message.split("\n\n")
        chunks: List[str] = []
        current: List[str] = []

        for block in blocks:
            candidate = "\n\n".join([*current, block]) if current else block
            if _blen(candidate) <= max_length:
                current.append(block)
                continue

            if current:
                chunks.append("\n\n".join(current))
                current = []
                if _blen(block) <= max_length:
                    current = [block]
                    continue
                # The lone block still overflows: split it by lines.
                chunks.extend(self._split_long_block(block, max_length))
                continue

            chunks.extend(self._split_long_block(block, max_length))

        if current:
            chunks.append("\n\n".join(current))

        return chunks

    def _split_long_block(self, block: str, max_length: int) -> List[str]:
        lines = block.splitlines()
        chunks: List[str] = []
        current: List[str] = []

        for line in lines:
            candidate = "\n".join([*current, line]) if current else line
            if _blen(candidate) <= max_length:
                current.append(line)
                continue
            if current:
                chunks.append("\n".join(current))
                current = [line]
                if _blen(line) <= max_length:
                    continue
                chunks.extend(self._split_long_line(line, max_length))
                current = []
            else:
                chunks.extend(self._split_long_line(line, max_length))
                current = []

        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _split_long_line(line: str, max_length: int) -> List[str]:
        """Split one over-budget line into <= ``max_length``-byte pieces.

        Prefers a space boundary (A5 polish); never emits a piece whose UTF-8
        byte length exceeds ``max_length``.
        """
        pieces: List[str] = []
        remaining = line
        while _blen(remaining) > max_length:
            cut = _MattermostFormatter._byte_prefix_len(remaining, max_length)
            cut = max(cut, 1)  # always make progress, even if a char > max_length
            # Prefer to break at the last space within the byte budget.
            space = remaining.rfind(" ", 1, cut)
            if space > 0:
                pieces.append(remaining[:space])
                remaining = remaining[space:].lstrip(" ")
            else:
                pieces.append(remaining[:cut])
                remaining = remaining[cut:]
        if remaining:
            pieces.append(remaining)
        return pieces

    @staticmethod
    def _byte_prefix_len(s: str, max_bytes: int) -> int:
        """Largest character count whose UTF-8 encoding is <= ``max_bytes``."""
        total = 0
        for index, ch in enumerate(s):
            total += len(ch.encode("utf-8"))
            if total > max_bytes:
                return index
        return len(s)

    def _confidence_label(self, confidence: float) -> str:
        return confidence_text(confidence, self.language)


class MattermostDeliverer(_MattermostFormatter):
    """Send digest messages to Mattermost via incoming webhook (the default)."""

    def deliver_digest(
        self,
        digest: Digest,
        json_path: str | None = None,
        llm_budget: dict | None = None,
    ) -> dict:
        """Format and send the digest to Mattermost.

        The delivered message is recipient-facing (owner decision C5/C8): it
        carries only user signals, never operator metadata. ``json_path`` and
        ``llm_budget`` are threaded for signature compatibility but no longer
        surface in the message — ``json_path`` was a local operator filesystem
        path the recipient cannot open, and the LLM budget is operator-only
        (``run_meta.llm_budget`` + structured log, the narrowed ADR-008 v2
        visibility clause). Both are still persisted in the run artifacts.
        """
        # D4 delivery guard ("guard + warn"): an incoming-webhook URL is an
        # opaque token, so the target audience is NOT derivable. When the
        # operator has not confirmed the target is a private DM/channel, emit one
        # payload-free warning and continue — never block delivery.
        if self.config.enabled and not self.config.acknowledged_private:
            logger.warning(
                "mattermost_target_privacy_unconfirmed",
                trace_id=digest.trace_id,
                hint=(
                    "Webhook target not confirmed private; the personal digest may"
                    " be visible to the channel audience. Re-run setup to confirm."
                ),
            )

        webhook_url = self.config.get_webhook_url()
        parts = self._split_message(
            self._format_digest(digest, json_path, llm_budget), self.config.max_message_length
        )

        with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
            for index, part in enumerate(parts, start=1):
                payload = {"text": part}
                response = client.post(webhook_url, json=payload)
                response.raise_for_status()
                logger.info(
                    "Mattermost delivery part sent",
                    trace_id=digest.trace_id,
                    part=index,
                    total_parts=len(parts),
                    status_code=response.status_code,
                )

        return {"status": "sent", "parts": len(parts)}


#: Per-request timeout for api-mode delivery calls (seconds). A single post is
#: fast and the find-or-create handshake is a few small GET/POSTs; matches the
#: webhook deliverer's 20s.
_API_TIMEOUT_S = 20.0


@dataclass
class _ResolvedTarget:
    """The resolved api-mode destination plus the facts the D4 guard needs."""

    channel_id: str
    channel_type: str  # "P" (private channel) or "D" (direct / self-DM)
    member_count: Optional[int]  # None when not queried
    target: str  # "private_channel" | "self_dm"
    team_id: Optional[str]
    fallback: bool  # True if a private-channel denial fell back to self-DM


class MattermostApiDeliverer(_MattermostFormatter):
    """Deliver the digest via the authenticated v4 REST API (PAT, corp-only).

    Renders the exact same message as the webhook deliverer (shared
    ``_MattermostFormatter``) but POSTs it as the owner's PAT to a provably-
    private target — a dedicated owner-only private channel (default) or the
    self-DM — and captures the per-part ``post_id``s into the receipt for the
    later EP-15 reaction-harvest pass. The authenticated API is corp-network-only
    (the edge proxy 403s external Bearer), so this path runs inside corp like
    EWS/LLM (ADR-012); ``auth_mode='webhook'`` stays the externally-reachable
    default.

    The read-only ingest client (``ingest/mattermost.py``) is left untouched —
    it deliberately makes no writes. This is the WRITE side, kept separate so
    that read-only contract is not blurred. ``http_client`` is injected by tests
    (mirroring the ``httpx.Client`` slice used here); a real client is built
    per-run and closed afterwards.
    """

    def __init__(
        self,
        config: MattermostDeliverConfig,
        language: str = DEFAULT_LANGUAGE,
        *,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(config, language)
        self._base = config.get_base_url()
        if not self._base:
            raise ValueError(
                f"Mattermost api delivery needs a base URL (set {config.base_url_env})"
            )
        self._api = f"{self._base}/api/v4"
        # Never log the token; it lives only on the Authorization header.
        self._auth = {"Authorization": f"Bearer {config.get_token()}"}
        self._http = http_client

    # -- transport ---------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._http.get(f"{self._api}{path}", params=params, headers=self._auth)
        resp.raise_for_status()
        return resp.json()

    def _get_raw(self, path: str) -> Any:
        """GET returning the raw response so the caller can branch on status (e.g. 404)."""
        return self._http.get(f"{self._api}{path}", headers=self._auth)

    def _post(self, path: str, body: object) -> Any:
        resp = self._http.post(f"{self._api}{path}", json=body, headers=self._auth)
        resp.raise_for_status()
        return resp.json()

    def _post_raw(self, path: str, body: object) -> Any:
        """POST returning the raw response so the caller can branch on status (e.g. 403)."""
        return self._http.post(f"{self._api}{path}", json=body, headers=self._auth)

    # -- delivery ----------------------------------------------------------

    def deliver_digest(
        self,
        digest: Digest,
        json_path: str | None = None,
        llm_budget: dict | None = None,
    ) -> dict:
        """Render (shared formatter) and POST the digest via the v4 REST API.

        Returns a receipt carrying ``status``/``error`` (so the
        ``_stage_deliver`` degrade-not-drop path stays backward-tolerant) plus
        api-mode fields: ``channel_id``, ``team_id``, ``post_ids`` and
        ``audience_owner_only`` (and ``target_fallback`` if a private-channel
        denial fell back to the self-DM).
        """
        owns_client = self._http is None
        if owns_client:
            self._http = httpx.Client(
                timeout=httpx.Timeout(_API_TIMEOUT_S), verify=self.config.verify_ssl
            )
        try:
            me_id = self._get("/users/me")["id"]
            target = self._resolve_target(me_id)

            # D4: a provably owner-only audience satisfies the privacy guard
            # structurally (unlike an opaque webhook URL). Warn only if a private
            # channel somehow carries other members.
            audience_owner_only = target.target == "self_dm" or (
                target.channel_type == "P" and target.member_count == 1
            )
            if not audience_owner_only:
                logger.warning(
                    "mattermost_target_privacy_unconfirmed",
                    trace_id=digest.trace_id,
                    hint=(
                        "api delivery target is not provably owner-only"
                        f" (members={target.member_count}); review the channel."
                    ),
                )

            parts = self._split_message(
                self._format_digest(digest, json_path, llm_budget),
                self.config.max_message_length,
            )
            post_ids: List[str] = []
            for index, part in enumerate(parts, start=1):
                post = self._post("/posts", {"channel_id": target.channel_id, "message": part})
                post_ids.append(post["id"])
                logger.info(
                    "Mattermost api delivery part sent",
                    trace_id=digest.trace_id,
                    part=index,
                    total_parts=len(parts),
                    target=target.target,
                )

            receipt: dict = {
                "status": "sent",
                "mode": "api",
                "target": target.target,
                "channel_id": target.channel_id,
                "team_id": target.team_id,
                "post_ids": post_ids,
                "parts": len(parts),
                "audience_owner_only": audience_owner_only,
            }
            if target.fallback:
                receipt["target_fallback"] = "self_dm"
            return receipt
        finally:
            if owns_client:
                self._http.close()
                self._http = None

    # -- target resolution -------------------------------------------------

    def _channel_slug(self, me_id: str) -> str:
        """Per-user channel slug: ``<channel_name>-<user_id>``.

        A Mattermost channel ``name`` (slug) is unique per *team*, so a fixed
        slug would collide the moment a second person on the same team runs
        ActionPulse — the first create wins and everyone else's create 400s. The
        per-user suffix keeps each owner's channel distinct on a shared team
        while the (non-unique) ``display_name`` stays friendly. ``user_id`` is a
        26-char id of slug-safe chars, so the result is always a valid name.
        """
        return f"{self.config.channel_name}-{me_id}"

    def _resolve_target(self, me_id: str) -> _ResolvedTarget:
        """Resolve (creating if needed) the api-mode delivery destination."""
        if self.config.delivery_target == "self_dm":
            return self._self_dm_target(me_id)

        team_id = self._resolve_team_id()
        slug = self._channel_slug(me_id)
        existing = self._find_channel_by_name(team_id, slug)
        if existing is not None:
            cid = existing["id"]
            stats = self._get(f"/channels/{cid}/stats")
            return _ResolvedTarget(
                channel_id=cid,
                channel_type=existing.get("type", "P"),
                member_count=stats.get("member_count"),
                target="private_channel",
                team_id=existing.get("team_id", team_id),
                fallback=False,
            )
        return self._create_private_channel(team_id, me_id, slug)

    def _self_dm_target(self, me_id: str, *, fallback: bool = False) -> _ResolvedTarget:
        """Open (idempotently) the owner's self-DM and return it as the target."""
        channel = self._post("/channels/direct", [me_id, me_id])
        return _ResolvedTarget(
            channel_id=channel["id"],
            channel_type=channel.get("type", "D"),
            member_count=1,  # a self-DM is provably the owner alone
            target="self_dm",
            team_id=None,
            fallback=fallback,
        )

    def _resolve_team_id(self) -> str:
        teams = self._get("/users/me/teams") or []
        if not teams:
            raise ValueError("Mattermost api delivery: the PAT is not a member of any team")
        want = self.config.team.strip()
        if not want:
            return teams[0]["id"]
        for team in teams:
            if want in (team.get("id"), team.get("name"), team.get("display_name")):
                return team["id"]
        raise ValueError(
            f"Mattermost api delivery: configured team {want!r} not found for this PAT"
        )

    def _find_channel_by_name(self, team_id: str, slug: str) -> Optional[dict]:
        """GET the private channel by its (per-user) slug; None on 404 (not created yet)."""
        resp = self._get_raw(f"/teams/{team_id}/channels/name/{slug}")
        if getattr(resp, "status_code", None) == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _is_name_conflict(resp: Any) -> bool:
        """True if a create failed because the channel name is already taken on the team.

        Mattermost returns this as a 400/409 carrying an ``id`` like
        ``store.sql_channel.save_channel.exists.app_error``. Per-user slugs make
        this unlikely, but if two runs race (or a stale archived channel still
        holds the slug) we degrade to the self-DM rather than skip delivery.
        """
        if getattr(resp, "status_code", None) not in (400, 409):
            return False
        try:
            body = resp.json() or {}
        except Exception:  # noqa: BLE001 - error body is best-effort
            return False
        blob = f"{body.get('id', '')} {body.get('message', '')}".lower()
        return "exist" in blob

    def _create_private_channel(self, team_id: str, me_id: str, slug: str) -> _ResolvedTarget:
        """Create the owner-only private channel; fall back to self-DM if creation is refused.

        Fallback covers both a 403 permission denial (the corp build may restrict
        ``create_private_channel``) and a name-conflict (a slug already taken) so
        delivery still lands somewhere private instead of degrading to a warning.
        """
        body = {
            "team_id": team_id,
            "name": slug,
            "display_name": self.config.channel_display_name,
            "type": "P",
            "purpose": "ActionPulse daily digest — private, owner-only.",
        }
        resp = self._post_raw("/channels", body)
        denied = getattr(resp, "status_code", None) == 403
        if self.config.fallback_to_self_dm and (denied or self._is_name_conflict(resp)):
            logger.warning(
                "mattermost_private_channel_create_refused",
                reason="permission_denied" if denied else "name_conflict",
                hint="private-channel create refused; falling back to self-DM delivery",
            )
            return self._self_dm_target(me_id, fallback=True)
        resp.raise_for_status()
        channel = resp.json()
        # The PAT creator is auto-added as the sole member (and channel admin), so
        # the audience is provably {owner} right after creation.
        return _ResolvedTarget(
            channel_id=channel["id"],
            channel_type=channel.get("type", "P"),
            member_count=1,
            target="private_channel",
            team_id=channel.get("team_id", team_id),
            fallback=False,
        )
