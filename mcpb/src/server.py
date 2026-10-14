"""MCPB entry point: launch the published pm-trader MCP server over stdio."""

from __future__ import annotations

from pm_trader.mcp_server import main

if __name__ == "__main__":  # pragma: no cover
    main()
