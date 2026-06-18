"""``auth_mode='api'`` Mattermost delivery (PAT → owner-only private channel).

These tests exercise the WRITE path that posts the digest via the authenticated
v4 REST API to a provably-private target (a dedicated owner-only private channel
by default, the self-DM as fallback), capturing per-part ``post_id``s. The HTTP
client is injected as a fake so no network is touched — the authenticated API is
corp-only and is validated live only from inside corp (ADR-012); here we pin the
call sequence, the find-or-create idempotency, the 403→self-DM fallback, the D4
audience proof, and that the shared formatter's @-escaping carries over.
"""

import httpx
import pytest

from digest_core.config import MattermostDeliverConfig
from digest_core.deliver.mattermost import MattermostApiDeliverer
from digest_core.llm.schemas import Digest


class _Resp:
    """Minimal stand-in for ``httpx.Response`` (status + json + raise_for_status)."""

    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://mm.test/")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=req,
                response=httpx.Response(self.status_code, request=req),
            )

    def json(self):
        return self._payload


class FakeHttp:
    """Records every (verb, url[, body]) and returns canned responses via a router."""

    def __init__(self, responder):
        self._responder = responder
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, str, object]] = []
        self.closed = False

    def get(self, url, *, params=None, headers=None):
        self.calls.append(("GET", url))
        self.bodies.append(("GET", url, None))
        return self._responder("GET", url, None)

    def post(self, url, *, json=None, headers=None):
        self.calls.append(("POST", url))
        self.bodies.append(("POST", url, json))
        return self._responder("POST", url, json)

    def close(self):  # pragma: no cover - injected client is not owned, never closed
        self.closed = True


def _make_http(
    *,
    name_status: int = 200,
    create_status: int = 201,
    create_error_id: str = "",
    stats_member_count: int = 1,
    teams=None,
    me_id: str = "me1",
):
    """A router covering the api-delivery call surface; tweak via kwargs per test."""
    me = {"id": me_id, "username": "owner"}
    teams = (
        teams if teams is not None else [{"id": "team1", "name": "corp", "display_name": "Corp"}]
    )
    chan = {"id": "chanP", "type": "P", "team_id": "team1"}
    post_n = {"n": 0}

    def responder(method, url, body):
        if method == "GET" and url.endswith("/users/me"):
            return _Resp(200, me)
        if method == "GET" and url.endswith("/users/me/teams"):
            return _Resp(200, teams)
        if method == "GET" and "/channels/name/" in url:
            return _Resp(404, {}) if name_status == 404 else _Resp(200, chan)
        if method == "GET" and url.endswith("/stats"):
            return _Resp(200, {"member_count": stats_member_count})
        if method == "POST" and url.endswith("/channels/direct"):
            return _Resp(201, {"id": "dmX", "type": "D"})
        if method == "POST" and url.endswith("/channels"):
            if create_status >= 400:
                return _Resp(create_status, {"id": create_error_id} if create_error_id else {})
            return _Resp(create_status, chan)
        if method == "POST" and url.endswith("/posts"):
            post_n["n"] += 1
            return _Resp(201, {"id": f"post{post_n['n']}"})
        raise AssertionError(f"unexpected call: {method} {url}")

    return FakeHttp(responder)


def _cfg(monkeypatch, **over) -> MattermostDeliverConfig:
    monkeypatch.setenv("MM_PAT", "test-token")
    monkeypatch.setenv("MM_BASE_URL", "http://mm.test")
    return MattermostDeliverConfig(auth_mode="api", **over)


def _digest(items=None) -> Digest:
    items = (
        items
        if items is not None
        else [
            {
                "title": "Do X",
                "due": None,
                "evidence_id": "ev1",
                "confidence": 0.9,
                "source_ref": {"type": "email", "msg_id": "m1"},
            }
        ]
    )
    return Digest(
        schema_version="1.0",
        prompt_version="v1",
        digest_date="2026-03-29",
        trace_id="trace-1",
        sections=[{"title": "Мои действия", "items": items}],
    )


def _urls(http: FakeHttp) -> list[str]:
    return [u for _, u in http.calls]


# --------------------------------------------------------------------------- #
# config defaults
# --------------------------------------------------------------------------- #


def test_auth_mode_defaults_to_webhook():
    """The new api path is strictly opt-in — default behavior is unchanged."""
    cfg = MattermostDeliverConfig()
    assert cfg.auth_mode == "webhook"
    assert cfg.delivery_target == "private_channel"
    assert cfg.channel_name == "actionpulse-digest"
    assert cfg.fallback_to_self_dm is True


def test_api_deliverer_requires_base_url(monkeypatch):
    monkeypatch.setenv("MM_PAT", "test-token")
    monkeypatch.delenv("MM_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="base URL"):
        MattermostApiDeliverer(MattermostDeliverConfig(auth_mode="api"), http_client=_make_http())


def test_api_deliverer_requires_token(monkeypatch):
    monkeypatch.setenv("MM_BASE_URL", "http://mm.test")
    monkeypatch.delenv("MM_PAT", raising=False)
    with pytest.raises(ValueError, match="MM_PAT"):
        MattermostApiDeliverer(MattermostDeliverConfig(auth_mode="api"), http_client=_make_http())


# --------------------------------------------------------------------------- #
# private-channel delivery
# --------------------------------------------------------------------------- #


def test_delivers_to_existing_private_channel(monkeypatch):
    http = _make_http(name_status=200, stats_member_count=1)
    deliverer = MattermostApiDeliverer(_cfg(monkeypatch), http_client=http)

    receipt = deliverer.deliver_digest(_digest())

    assert receipt["status"] == "sent"
    assert receipt["mode"] == "api"
    assert receipt["target"] == "private_channel"
    assert receipt["channel_id"] == "chanP"
    assert receipt["team_id"] == "team1"
    assert receipt["post_ids"] == ["post1"]
    assert receipt["audience_owner_only"] is True
    assert "target_fallback" not in receipt
    # Idempotent: an existing channel is reused, never re-created.
    assert not any(u.endswith("/channels") for _, u in http.calls)
    # Per-user slug: lookup is by <channel_name>-<user_id>, never the bare slug.
    assert any("/channels/name/actionpulse-digest-me1" in u for u in _urls(http))
    assert any(u.endswith("/posts") for _, u in http.calls)


def test_creates_private_channel_when_missing(monkeypatch):
    http = _make_http(name_status=404, create_status=201)
    deliverer = MattermostApiDeliverer(_cfg(monkeypatch), http_client=http)

    receipt = deliverer.deliver_digest(_digest())

    assert receipt["channel_id"] == "chanP"
    assert receipt["target"] == "private_channel"
    # The create call carries the right slug + private type.
    create = [b for (m, u, b) in http.bodies if m == "POST" and u.endswith("/channels")]
    assert len(create) == 1
    body = create[0]
    assert body["name"] == "actionpulse-digest-me1"  # per-user slug, not the bare base
    assert body["display_name"] == "ActionPulse Digest"
    assert body["type"] == "P"
    assert body["team_id"] == "team1"


def test_creation_denied_falls_back_to_self_dm(monkeypatch):
    http = _make_http(name_status=404, create_status=403)
    deliverer = MattermostApiDeliverer(_cfg(monkeypatch), http_client=http)

    receipt = deliverer.deliver_digest(_digest())

    assert receipt["target"] == "self_dm"
    assert receipt["target_fallback"] == "self_dm"
    assert receipt["channel_id"] == "dmX"
    assert receipt["audience_owner_only"] is True
    assert any(u.endswith("/channels/direct") for _, u in http.calls)


def test_creation_denied_without_fallback_raises(monkeypatch):
    http = _make_http(name_status=404, create_status=403)
    cfg = _cfg(monkeypatch, fallback_to_self_dm=False)
    deliverer = MattermostApiDeliverer(cfg, http_client=http)

    with pytest.raises(httpx.HTTPStatusError):
        deliverer.deliver_digest(_digest())


def test_warns_when_channel_not_owner_only(monkeypatch):
    # A pre-existing channel that somehow has other members must NOT be treated
    # as a provable owner-only audience (D4).
    http = _make_http(name_status=200, stats_member_count=2)
    deliverer = MattermostApiDeliverer(_cfg(monkeypatch), http_client=http)

    receipt = deliverer.deliver_digest(_digest())

    assert receipt["audience_owner_only"] is False
    assert receipt["status"] == "sent"  # still delivers; the guard warns, never blocks


def test_channel_slug_is_per_user(monkeypatch):
    # Two users on the SAME team must address DISTINCT channel slugs — a channel
    # name is unique per team, so a shared slug would collide at scale.
    slugs = []
    for uid in ("alice", "bob"):
        http = _make_http(name_status=404, create_status=201, me_id=uid)
        MattermostApiDeliverer(_cfg(monkeypatch), http_client=http).deliver_digest(_digest())
        created = [
            b["name"] for (m, u, b) in http.bodies if m == "POST" and u.endswith("/channels")
        ]
        slugs.append(created[0])
    assert slugs == ["actionpulse-digest-alice", "actionpulse-digest-bob"]
    assert slugs[0] != slugs[1]


def test_name_conflict_falls_back_to_self_dm(monkeypatch):
    # A slug already taken on the team (400 "...exists...") must degrade to the
    # self-DM, not skip delivery — even though per-user slugs make this rare.
    http = _make_http(
        name_status=404,
        create_status=400,
        create_error_id="store.sql_channel.save_channel.exists.app_error",
    )
    deliverer = MattermostApiDeliverer(_cfg(monkeypatch), http_client=http)

    receipt = deliverer.deliver_digest(_digest())

    assert receipt["target"] == "self_dm"
    assert receipt["target_fallback"] == "self_dm"
    assert receipt["status"] == "sent"
    assert any(u.endswith("/channels/direct") for _, u in http.calls)


def test_unrelated_create_400_still_raises(monkeypatch):
    # A non-conflict 400 (genuine bad request) must NOT be masked as a fallback.
    http = _make_http(
        name_status=404,
        create_status=400,
        create_error_id="api.channel.create_channel.invalid_character.app_error",
    )
    deliverer = MattermostApiDeliverer(_cfg(monkeypatch), http_client=http)

    with pytest.raises(httpx.HTTPStatusError):
        deliverer.deliver_digest(_digest())


# --------------------------------------------------------------------------- #
# self-DM target
# --------------------------------------------------------------------------- #


def test_self_dm_target_skips_channel_machinery(monkeypatch):
    http = _make_http()
    cfg = _cfg(monkeypatch, delivery_target="self_dm")
    deliverer = MattermostApiDeliverer(cfg, http_client=http)

    receipt = deliverer.deliver_digest(_digest())

    assert receipt["target"] == "self_dm"
    assert receipt["channel_id"] == "dmX"
    assert receipt["team_id"] is None
    assert receipt["audience_owner_only"] is True
    # No team lookup, no channel find/create for the self-DM path.
    assert not any("/channels/name/" in u for u in _urls(http))
    assert not any(u.endswith("/users/me/teams") for _, u in http.calls)
    assert any(u.endswith("/channels/direct") for _, u in http.calls)


# --------------------------------------------------------------------------- #
# team resolution
# --------------------------------------------------------------------------- #


def test_team_resolved_by_name(monkeypatch):
    teams = [
        {"id": "teamA", "name": "alpha", "display_name": "Alpha"},
        {"id": "teamB", "name": "beta", "display_name": "Beta"},
    ]
    http = _make_http(name_status=200, teams=teams)
    cfg = _cfg(monkeypatch, team="beta")
    deliverer = MattermostApiDeliverer(cfg, http_client=http)

    deliverer.deliver_digest(_digest())

    assert any("/teams/teamB/channels/name/" in u for u in _urls(http))


def test_unknown_team_raises(monkeypatch):
    http = _make_http()
    cfg = _cfg(monkeypatch, team="does-not-exist")
    deliverer = MattermostApiDeliverer(cfg, http_client=http)

    with pytest.raises(ValueError, match="team"):
        deliverer.deliver_digest(_digest())


# --------------------------------------------------------------------------- #
# shared-formatter carry-over
# --------------------------------------------------------------------------- #


def test_mentions_are_escaped_in_posted_message(monkeypatch):
    http = _make_http()
    deliverer = MattermostApiDeliverer(_cfg(monkeypatch), http_client=http)

    deliverer.deliver_digest(
        _digest(
            [
                {
                    "title": "ping @ivan and @here before EOD",
                    "due": None,
                    "evidence_id": "ev1",
                    "confidence": 0.9,
                    "source_ref": {"type": "email", "msg_id": "m1"},
                }
            ]
        )
    )

    posted = [b for (m, u, b) in http.bodies if m == "POST" and u.endswith("/posts")]
    assert posted, "expected at least one /posts call"
    message = posted[0]["message"]
    assert "`@ivan`" in message
    assert "`@here`" in message
    assert "@ivan " not in message.replace("`@ivan`", "")  # no un-escaped mention left


def test_multipart_captures_every_post_id(monkeypatch):
    # A tiny byte budget forces the shared splitter to emit several parts; each
    # part is its own POST and every post_id must be captured.
    http = _make_http()
    cfg = _cfg(monkeypatch, max_message_length=60)
    deliverer = MattermostApiDeliverer(cfg, http_client=http)

    items = [
        {
            "title": f"Action item number {i} with enough text to spill",
            "due": None,
            "evidence_id": f"ev{i}",
            "confidence": 0.9,
            "source_ref": {"type": "email", "msg_id": f"m{i}"},
        }
        for i in range(6)
    ]
    receipt = deliverer.deliver_digest(_digest(items))

    post_calls = [u for (m, u) in http.calls if m == "POST" and u.endswith("/posts")]
    assert receipt["parts"] == len(post_calls) > 1
    assert len(receipt["post_ids"]) == receipt["parts"]
    assert receipt["post_ids"] == [f"post{i}" for i in range(1, receipt["parts"] + 1)]
