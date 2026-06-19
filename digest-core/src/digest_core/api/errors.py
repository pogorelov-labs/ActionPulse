"""Errors raised by the InboxAPI facade."""

from __future__ import annotations


class ApiError(RuntimeError):
    """The API could not be opened or a verb could not be served."""


class GatewayUnavailable(ApiError):
    """A gateway-backed verb (semantic search / ask / embeddings) could not reach the
    corp LLM gateway. Raised instead of hanging; offline-pure verbs are unaffected."""


class CorpOnlyError(ApiError):
    """A source operation (live EWS/MM fetch, channels, reactions) needs the corp
    network and could not run from here."""
