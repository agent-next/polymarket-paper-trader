"""Test read-only portfolio routes: portfolio, balance, history, stats."""
from __future__ import annotations

import pytest

from tests.conftest import _register, _headers, _create_account, _buy


# -- Portfolio tests --

class TestPortfolio:
    def test_portfolio_empty(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        resp = client.get(
            f"/accounts/{account['id']}/portfolio",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"] == []

    def test_portfolio_with_position(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        # Buy to create a position
        buy_resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        assert buy_resp.status_code == 200
        buy_trade = buy_resp.json()["data"]["trade"]

        resp = client.get(
            f"/accounts/{account['id']}/portfolio",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        positions = data["data"]
        assert len(positions) == 1

        pos = positions[0]
        assert pos["market_slug"] == "will-bitcoin-hit-100k"
        assert pos["outcome"] == "yes"
        assert pos["shares"] == pytest.approx(buy_trade["shares"])
        assert pos["live_price"] == pytest.approx(0.65)
        assert pos["current_value"] == pytest.approx(pos["shares"] * 0.65)
        assert "unrealized_pnl" in pos


# -- Balance tests --

class TestBalance:
    def test_balance_initial(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        resp = client.get(
            f"/accounts/{account['id']}/balance",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["cash"] == pytest.approx(10000.0)
        assert data["positions_value"] == pytest.approx(0.0)
        assert data["total_value"] == pytest.approx(10000.0)
        assert data["starting_balance"] == pytest.approx(10000.0)
        assert data["pnl"] == pytest.approx(0.0)
        assert data["roi_pct"] == pytest.approx(0.0)

    def test_balance_after_buy(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        buy_resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        assert buy_resp.status_code == 200
        buy_trade = buy_resp.json()["data"]["trade"]

        resp = client.get(
            f"/accounts/{account['id']}/balance",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        data = resp.json()["data"]

        # Cash reduced by trade cost
        assert data["cash"] == pytest.approx(10000.0 - buy_trade["amount_usd"])
        # Positions value = shares * live_price (0.65)
        expected_pos_value = buy_trade["shares"] * 0.65
        assert data["positions_value"] == pytest.approx(expected_pos_value)
        # Total = cash + positions
        assert data["total_value"] == pytest.approx(data["cash"] + data["positions_value"])
        assert data["starting_balance"] == pytest.approx(10000.0)


# -- History tests --

class TestHistory:
    def test_history_empty(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        resp = client.get(
            f"/accounts/{account['id']}/history",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"] == []

    def test_history_after_trades(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        # Buy twice
        _buy(client, user["api_key"], account["id"], amount=10.0)
        _buy(client, user["api_key"], account["id"], amount=20.0)

        resp = client.get(
            f"/accounts/{account['id']}/history",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        trades = resp.json()["data"]
        assert len(trades) == 2
        # Newest first (higher id first)
        assert trades[0]["id"] > trades[1]["id"]


# -- Stats tests --

class TestStats:
    def test_stats_after_trades(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        # Buy a few times to generate trade data
        _buy(client, user["api_key"], account["id"], amount=10.0)
        _buy(client, user["api_key"], account["id"], amount=20.0)
        _buy(client, user["api_key"], account["id"], amount=30.0)

        resp = client.get(
            f"/accounts/{account['id']}/stats",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        stats = data["data"]
        expected_keys = {
            "starting_balance", "cash", "positions_value", "total_value",
            "pnl", "roi_pct", "total_trades", "buy_count", "sell_count",
            "win_rate", "sharpe_ratio", "max_drawdown", "total_fees",
            "avg_trade_size",
        }
        assert expected_keys.issubset(set(stats.keys()))
        assert stats["total_trades"] == 3
        assert stats["buy_count"] == 3
        assert stats["sell_count"] == 0
        assert stats["starting_balance"] == pytest.approx(10000.0)
