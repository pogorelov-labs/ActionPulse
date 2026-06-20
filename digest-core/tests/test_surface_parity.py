"""C1 — surface parity: keep InboxAPI ↔ MCP ↔ CLI from drifting apart.

The facade (`InboxAPI`) is the canonical surface; the MCP server re-exposes its verbs as
tools, and the CLI offers the user-facing subset. This test fails if a verb is added to one
surface but not wired to the others — the drift that prompted C1 (e.g. `history` used to live
only on the CLI). Intentional exceptions are explicit and documented here.
"""

from __future__ import annotations

import inspect

from digest_core.api.inbox import InboxAPI

# InboxAPI members that are lifecycle/plumbing, not query/tool verbs — not expected as MCP tools.
_API_NON_VERBS = {"open", "close", "store"}
# InboxAPI verbs deliberately NOT exposed over MCP (internal ops). Documented exceptions.
_API_ONLY = {"checkpoint"}  # a SQLite WAL checkpoint — operator plumbing, not an agent verb


def _api_verbs() -> set[str]:
    """Public query/maintenance/source verbs on the InboxAPI facade."""
    verbs: set[str] = set()
    for name, member in inspect.getmembers(InboxAPI):
        if name.startswith("_") or name in _API_NON_VERBS:
            continue
        if callable(member) or isinstance(member, property):
            verbs.add(name)
    return verbs


def _mcp_tool_names() -> set[str]:
    """Every tool name the MCP server can register (always-on + source + gated)."""
    from digest_core.mcp import server

    names = {name for _fn, name in server._TOOLS + server._SOURCE_TOOLS + server._MAINTENANCE_TOOLS}
    names.add("fetch_source")  # gated tool registered on its own (ACTIONPULSE_MCP_ENABLE_FETCH)
    return names


def test_every_inbox_api_verb_is_an_mcp_tool_and_vice_versa():
    """The core drift guard: the set of InboxAPI verbs == the set of MCP tools (minus the
    documented `_API_ONLY` exceptions). Adding a verb to one surface without the other fails here.
    """
    api_verbs = _api_verbs() - _API_ONLY
    mcp_tools = _mcp_tool_names()

    missing_tools = api_verbs - mcp_tools  # an InboxAPI verb with no MCP tool → drift
    extra_tools = mcp_tools - api_verbs  # an MCP tool with no InboxAPI verb → drift
    assert not missing_tools, f"InboxAPI verbs missing an MCP tool: {sorted(missing_tools)}"
    assert not extra_tools, f"MCP tools with no InboxAPI verb: {sorted(extra_tools)}"


def test_known_retrieval_verbs_are_present():
    """Sanity anchor — guard against the parity test passing on two empty/garbage sets."""
    api_verbs = _api_verbs()
    mcp_tools = _mcp_tool_names()
    anchor = {"history", "search", "ask", "related", "pending", "get_thread", "list_recent"}
    assert anchor <= api_verbs, f"missing from InboxAPI: {sorted(anchor - api_verbs)}"
    assert anchor <= mcp_tools, f"missing from MCP tools: {sorted(anchor - mcp_tools)}"


def test_history_reachable_on_all_three_surfaces():
    """`history` was the C1 poster child for drift — assert it now reaches every surface."""
    # InboxAPI (the facade — for MCP / a future bot, store-on consumers)
    assert callable(getattr(InboxAPI, "history", None))
    # MCP tool
    assert "history" in _mcp_tool_names()
    # CLI command (the store-free path — works even when the store is off, by design)
    from digest_core import cli

    assert callable(getattr(cli, "history", None))


def test_user_facing_retrieval_verbs_reach_the_cli():
    """The headline retrieval verbs are reachable from the terminal (a curated subset — the
    CLI intentionally does NOT expose every store primitive as its own command)."""
    from digest_core import cli

    for name in ("search", "ask", "history", "read"):
        assert callable(getattr(cli, name, None)), f"CLI missing command: {name}"
