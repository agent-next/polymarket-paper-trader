"""Bridge between benchmark runner and leaderboard Agent SDK.

Handles Agent creation, lifecycle, and graceful fallback when the
leaderboard SDK is not installed or the server is unreachable.
"""
from __future__ import annotations

import time
from typing import Any


class AgentBridgeError(Exception):
    """Raised when agent creation or connection fails."""


def create_agent(
    base_url: str,
    model: str,
    *,
    agent_name: str | None = None,
    account_name: str = "default",
    api_key: str | None = None,
) -> Any:
    """Create or reconnect a leaderboard Agent.

    If api_key is provided, reconnects to an existing agent.
    Otherwise registers a new agent with an auto-generated name.

    Returns:
        Agent instance (from polymarket-leaderboard-client).

    Raises:
        AgentBridgeError: if SDK not installed or connection fails.
    """
    try:
        from pm_leaderboard_client import Agent
    except ImportError:
        raise AgentBridgeError(
            "pm_leaderboard_client not installed. "
            "Install it from this repo: pip install -e leaderboard-client"
        )

    name = agent_name or _auto_name(model)

    try:
        if api_key:
            return Agent(
                base_url,
                api_key=api_key,
                account_name=account_name,
            )
        return Agent(
            base_url,
            name=name,
            model=model,
            account_name=account_name,
        )
    except Exception as e:
        raise AgentBridgeError(f"Failed to create agent: {e}") from e


def _auto_name(model: str) -> str:
    """Generate a unique agent name from model string + timestamp."""
    # "claude-opus-4" → "bench-claude-opus-4-1709251200"
    safe_model = model.replace("/", "-").replace(":", "-")[:30]
    ts = int(time.time())
    return f"bench-{safe_model}-{ts}"
