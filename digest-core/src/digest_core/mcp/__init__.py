"""The ActionPulse MCP server package (``actionpulse-mcp``).

Exposes the local InboxAPI over stdio to AI coding CLIs. Importing this package is
light (no MCP SDK); ``main`` is the console-script entry point and surfaces a friendly
hint when the optional ``mcp`` extra is missing.
"""

from __future__ import annotations


def main() -> None:
    """Console-script entry point: run the stdio MCP server."""
    import sys

    try:
        from digest_core.mcp.server import run_server

        run_server()
    except ModuleNotFoundError as exc:
        # The MCP SDK is the only optional dep imported (lazily, in _build_app).
        if (getattr(exc, "name", "") or "").split(".")[0] == "mcp":
            print(
                "The ActionPulse MCP server needs the 'mcp' extra (and 'store'):\n"
                "  uv sync --extra mcp --extra store",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


__all__ = ["main"]
