"""Test read-only portfolio routes: portfolio, balance, history, stats."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.adapters.polymarket import (
    Market,
    OrderBook,
    OrderBookLevel,
)
from server.app import app


# -- Fixtures --

SAMPLE_MARKET = Market(
    condition_id="0xabc123",
    slug="will-bitcoin-hit-100k",
    question="Will Bitcoin hit $100k by end of 2026?",
    description="Test market",
    outcomes=["Yes", "No"],
    outcome_prices=[0.65, 0.35],
    tokens=[
        {"token_id": "tok_yes_btc", "outcome": "Yes"},
        {"token_id": "tok_no_btc", "outcome": "No"},
    ],
    active=True,
    closed=False,
    volume=5_000_000.0,
    liquidity=250_000.0,
    end_date="2026-12-31T23:59:59Z",
    fee_rate_bps=0,
    tick_size=0.01,
)

SAMPLE_BOOK = OrderBook(
    bids=[
        OrderBookLevel(price=0.64, size=150.0),
        OrderBookLevel(price=0.63, size=200.0),
    ],
    asks=[
        OrderBookLevel(price=0.66, size=80.0),
        OrderBookLevel(price=0.67, size=120.0),
        OrderBookLevel(price=0.68, size=200.0),
    ],
)


class MockPolymarketClient:
    """Test double with get_midpoint support for portfolio routes."""

    def __init__(
        self,
        market: Market = SAMPLE_MARKET,
        book: OrderBook = SAMPLE_BOOK,
        fee_rate: int = 0,
        midpoint: float = 0.65,
    ):
        self._market = market
        self._book = book
        self._fee_rate = fee_rate
        self._midpoint = midpoint

    def get_market(self, slug: str) -> Market:
        return self._market

    def get_order_book(self, token_id: str) -> OrderBook:
        return self._book

    def get_fee_rate(self, token_id: str) -> int:
        return self._fee_rate

    def get_midpoint(self, token_id: str) -> float:
        return self._midpoint

    def close(self) -> None:
        pass


@pytest.fixture
def client():
    app.state.polymarket = MockPolymarketClient()
    with TestClient(app) as c:
        yield c


# -- Helpers --

def _register(client, name="test-bot"):
    resp = client.post("/auth/register", json={"agent_name": name})
    return resp.json()["data"]


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def _create_account(client, api_key):
    resp = client.post(
        "/accounts", json={"name": "default"}, headers=_headers(api_key)
    )
    return resp.json()["data"]


def _buy(client, api_key, account_id, amount=10.0, outcome="yes"):
    return client.post(
        "/trade/buy",
        json={
            "account_id": account_id,
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": outcome,
            "amount_usd": amount,
        },
        headers=_headers(api_key),
    )


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
