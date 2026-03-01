"""Tests for MCP server tool handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pm_sim import mcp_server
from pm_sim.mcp_server import (
    buy,
    cancel_order,
    check_orders,
    get_balance,
    get_market,
    get_order_book,
    history,
    init_account,
    list_markets,
    list_orders,
    place_limit_order,
    portfolio,
    reset_account,
    resolve,
    resolve_all,
    search_markets,
    sell,
    stats,
    watch_prices,
)


@pytest.fixture(autouse=True)
def fresh_engine(tmp_path: Path):
    """Reset the global engine for each test."""
    mcp_server._engine = None
    with patch.object(Path, "home", return_value=tmp_path):
        yield
    if mcp_server._engine is not None:
        mcp_server._engine.close()
        mcp_server._engine = None


def _parse(result: str) -> dict:
    """Parse a tool result JSON string."""
    return json.loads(result)


# ---------------------------------------------------------------------------
# Account tools
# ---------------------------------------------------------------------------


class TestInitAccount:
    def test_default_balance(self):
        result = _parse(init_account())
        assert result["ok"] is True
        assert result["data"]["cash"] == 10_000.0

    def test_custom_balance(self):
        result = _parse(init_account(balance=5_000.0))
        assert result["ok"] is True
        assert result["data"]["starting_balance"] == 5_000.0


class TestGetBalance:
    def test_not_initialized(self):
        result = _parse(get_balance())
        assert result["ok"] is False
        assert result["code"] == "not_initialized"

    def test_after_init(self):
        init_account(balance=7_500.0)
        result = _parse(get_balance())
        assert result["ok"] is True
        assert result["data"]["cash"] == 7_500.0
        assert result["data"]["starting_balance"] == 7_500.0


class TestResetAccount:
    def test_reset(self):
        init_account()
        result = _parse(reset_account())
        assert result["ok"] is True
        assert result["data"]["reset"] is True


# ---------------------------------------------------------------------------
# Portfolio tools
# ---------------------------------------------------------------------------


class TestPortfolio:
    def test_empty_portfolio(self):
        init_account()
        result = _parse(portfolio())
        assert result["ok"] is True
        assert result["data"] == []


class TestHistory:
    def test_empty_history(self):
        init_account()
        result = _parse(history())
        assert result["ok"] is True
        assert result["data"] == []


# ---------------------------------------------------------------------------
# Limit order tools
# ---------------------------------------------------------------------------


class TestListOrders:
    def test_empty_orders(self):
        init_account()
        result = _parse(list_orders())
        assert result["ok"] is True
        assert result["data"] == []


class TestCancelOrder:
    def test_nonexistent(self):
        init_account()
        result = _parse(cancel_order(999))
        assert result["ok"] is False
        assert result["code"] == "not_found"


class TestCheckOrders:
    def test_no_pending(self):
        init_account()
        result = _parse(check_orders())
        assert result["ok"] is True
        assert result["data"] == []


# ---------------------------------------------------------------------------
# Analytics tools
# ---------------------------------------------------------------------------


class TestStats:
    def test_empty_account(self):
        init_account()
        result = _parse(stats())
        assert result["ok"] is True
        assert result["data"]["total_trades"] == 0
        assert result["data"]["win_rate"] == 0.0


# ---------------------------------------------------------------------------
# Resolution tools
# ---------------------------------------------------------------------------


class TestResolveAll:
    def test_no_positions(self):
        init_account()
        result = _parse(resolve_all())
        assert result["ok"] is True
        assert result["data"] == []


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


class TestErrorEnvelope:
    def test_balance_error_has_code(self):
        result = _parse(get_balance())
        assert "ok" in result
        assert result["ok"] is False
        assert "error" in result
        assert "code" in result

    def test_stats_not_initialized(self):
        result = _parse(stats())
        assert result["ok"] is False
