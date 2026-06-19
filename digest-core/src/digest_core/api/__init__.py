"""The ActionPulse local API surface (InboxAPI) over the encrypted message store.

A single facade that wraps the store's retrieval, search, insight, and (in the
gateway-verbs phase) reasoning capabilities — the surface the MCP server and CLI
build on. Import is light: opening the store (and the gateway backend) is lazy.
"""

from __future__ import annotations

from digest_core.api.errors import ApiError, CorpOnlyError, GatewayUnavailable
from digest_core.api.inbox import CompareResult, InboxAPI

__all__ = ["InboxAPI", "CompareResult", "ApiError", "GatewayUnavailable", "CorpOnlyError"]
