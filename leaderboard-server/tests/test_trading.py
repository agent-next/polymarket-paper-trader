"""Test trading routes: server-side buy/sell execution."""
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

CLOSED_MARKET = Market(
    condition_id="0xclosed",
    slug="closed-market",
    question="Already resolved?",
    description="A closed market",
    outcomes=["Yes", "No"],
    outcome_prices=[1.0, 0.0],
    tokens=[
        {"token_id": "tok_yes_closed", "outcome": "Yes"},
        {"token_id": "tok_no_closed", "outcome": "No"},
    ],
    active=False,
    closed=True,
    volume=1_000_000.0,
    liquidity=0.0,
    end_date="2025-01-01T00:00:00Z",
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

EMPTY_BOOK = OrderBook(bids=[], asks=[])


class MockPolymarketClient:
    """Test double for PolymarketClient with configurable responses."""

    def __init__(
        self,
        market: Market = SAMPLE_MARKET,
        book: OrderBook = SAMPLE_BOOK,
        fee_rate: int = 0,
    ):
        self._market = market
        self._book = book
        self._fee_rate = fee_rate

    def get_market(self, slug: str) -> Market:
        return self._market

    def get_order_book(self, token_id: str) -> OrderBook:
        return self._book

    def get_fee_rate(self, token_id: str) -> int:
        return self._fee_rate

    def close(self) -> None:
        pass


@pytest.fixture
def client():
    # Set mock polymarket client BEFORE lifespan creates the real one
    app.state.polymarket = MockPolymarketClient()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_fees():
    app.state.polymarket = MockPolymarketClient(fee_rate=200)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_closed_market():
    app.state.polymarket = MockPolymarketClient(market=CLOSED_MARKET)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_empty_book():
    app.state.polymarket = MockPolymarketClient(book=EMPTY_BOOK)
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


def _buy(client, api_key, account_id, amount=10.0, outcome="yes", slug="will-bitcoin-hit-100k"):
    return client.post(
        "/trade/buy",
        json={
            "account_id": account_id,
            "market_slug": slug,
            "outcome": outcome,
            "amount_usd": amount,
        },
        headers=_headers(api_key),
    )


def _sell(client, api_key, account_id, shares, outcome="yes", slug="will-bitcoin-hit-100k"):
    return client.post(
        "/trade/sell",
        json={
            "account_id": account_id,
            "market_slug": slug,
            "outcome": outcome,
            "shares": shares,
        },
        headers=_headers(api_key),
    )


# -- Buy tests --

class TestBuySuccess:
    def test_buy_creates_trade_and_updates_cash(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        trade = data["data"]["trade"]
        assert trade["side"] == "buy"
        assert trade["outcome"] == "yes"
        assert trade["market_slug"] == "will-bitcoin-hit-100k"
        assert trade["shares"] > 0
        assert trade["avg_price"] > 0
        assert trade["fee"] == 0.0  # no fees in default mock

        updated_account = data["data"]["account"]
        assert updated_account["cash"] < 10000.0
        assert updated_account["cash"] == pytest.approx(10000.0 - trade["amount_usd"])

    def test_buy_creates_position(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        assert resp.status_code == 200

        trade = resp.json()["data"]["trade"]
        # Verify position was created by buying more on top
        resp2 = _buy(client, user["api_key"], account["id"], amount=5.0)
        assert resp2.status_code == 200
        # Cash should have gone down by both amounts
        final_account = resp2.json()["data"]["account"]
        first_cost = trade["amount_usd"]
        second_cost = resp2.json()["data"]["trade"]["amount_usd"]
        assert final_account["cash"] == pytest.approx(10000.0 - first_cost - second_cost)

    def test_buy_fak_partial_fill(self, client):
        """FAK order type allows partial fills."""
        user = _register(client)
        account = _create_account(client, user["api_key"])

        # Buy a large amount that exceeds book liquidity with FAK
        resp = client.post(
            "/trade/buy",
            json={
                "account_id": account["id"],
                "market_slug": "will-bitcoin-hit-100k",
                "outcome": "yes",
                "amount_usd": 50000.0,  # more than book can fill
                "order_type": "fak",
            },
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        trade = resp.json()["data"]["trade"]
        assert trade["is_partial"] == 1  # SQLite stores bool as int


class TestBuyWithFees:
    def test_buy_fee_deducted(self, client_with_fees):
        user = _register(client_with_fees)
        account = _create_account(client_with_fees, user["api_key"])

        resp = _buy(client_with_fees, user["api_key"], account["id"], amount=100.0)
        assert resp.status_code == 200
        data = resp.json()
        trade = data["data"]["trade"]

        assert trade["fee"] > 0
        assert trade["fee_rate_bps"] == 200
        # Cash should be reduced by cost + fee
        updated_account = data["data"]["account"]
        total_outflow = trade["amount_usd"] + trade["fee"]
        assert updated_account["cash"] == pytest.approx(10000.0 - total_outflow)


class TestBuyInsufficientBalance:
    def test_buy_insufficient_balance(self):
        """Use a tiny-balance account so even a small fill exceeds cash."""
        # Book asks: 80@0.66 = $52.80 cost.  A $10 buy fits in the book but
        # exceeds an account with only $5 cash.
        tiny_book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=150.0)],
            asks=[OrderBookLevel(price=0.66, size=80.0)],
        )
        app.state.polymarket = MockPolymarketClient(book=tiny_book)
        with TestClient(app) as client:
            user = _register(client)
            account = _create_account(client, user["api_key"])
            # Drain almost all cash: buy $9995 worth (book has plenty at 0.66)
            # Instead, update cash directly via a second smaller approach:
            # We'll just buy with amount > starting balance.
            # Actually: starting balance is 10000, book ask is 80 shares @ 0.66 = $52.80 max.
            # A $10 buy fills fine. We need cash < fill cost.
            # So reduce cash first: buy $52 with FAK (fills the whole book),
            # then try again with a new book.
            # Simplest: set cash via DB directly.
            db = app.state.db
            db.update_cash(account["id"], 5.0)  # only $5 cash

            resp = _buy(client, user["api_key"], account["id"], amount=10.0)
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert detail["code"] == "INSUFFICIENT_BALANCE"


class TestBuyMarketClosed:
    def test_buy_closed_market(self, client_closed_market):
        user = _register(client_closed_market)
        account = _create_account(client_closed_market, user["api_key"])

        resp = _buy(client_closed_market, user["api_key"], account["id"], amount=10.0)
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "MARKET_CLOSED"


class TestBuyFokRejected:
    def test_buy_fok_empty_book(self, client_empty_book):
        user = _register(client_empty_book)
        account = _create_account(client_empty_book, user["api_key"])

        resp = _buy(client_empty_book, user["api_key"], account["id"], amount=10.0)
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "ORDER_REJECTED"


# -- Sell tests --

class TestSellSuccess:
    def test_sell_after_buy(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        # Buy first
        buy_resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        assert buy_resp.status_code == 200
        buy_trade = buy_resp.json()["data"]["trade"]
        shares_bought = buy_trade["shares"]
        cash_after_buy = buy_resp.json()["data"]["account"]["cash"]

        # Sell all shares
        sell_resp = _sell(client, user["api_key"], account["id"], shares=shares_bought)
        assert sell_resp.status_code == 200
        data = sell_resp.json()
        assert data["ok"] is True

        sell_trade = data["data"]["trade"]
        assert sell_trade["side"] == "sell"
        assert sell_trade["shares"] == pytest.approx(shares_bought)

        # Cash should increase after sell
        updated_account = data["data"]["account"]
        assert updated_account["cash"] > cash_after_buy

    def test_sell_partial_position(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        # Buy shares
        buy_resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        shares_bought = buy_resp.json()["data"]["trade"]["shares"]

        # Sell half
        half = shares_bought / 2
        sell_resp = _sell(client, user["api_key"], account["id"], shares=half)
        assert sell_resp.status_code == 200


class TestSellNoPosition:
    def test_sell_without_position(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        resp = _sell(client, user["api_key"], account["id"], shares=10.0)
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "NO_POSITION"


class TestSellMoreThanHeld:
    def test_sell_excess_shares(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])

        # Buy a small amount
        buy_resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        shares_bought = buy_resp.json()["data"]["trade"]["shares"]

        # Try to sell more than held
        resp = _sell(client, user["api_key"], account["id"], shares=shares_bought + 100)
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "ORDER_REJECTED"


class TestSellWithFees:
    def test_sell_fee_deducted(self, client_with_fees):
        user = _register(client_with_fees)
        account = _create_account(client_with_fees, user["api_key"])

        # Buy first
        buy_resp = _buy(client_with_fees, user["api_key"], account["id"], amount=100.0)
        shares_bought = buy_resp.json()["data"]["trade"]["shares"]
        cash_after_buy = buy_resp.json()["data"]["account"]["cash"]

        # Sell all
        sell_resp = _sell(client_with_fees, user["api_key"], account["id"], shares=shares_bought)
        assert sell_resp.status_code == 200
        sell_trade = sell_resp.json()["data"]["trade"]

        assert sell_trade["fee"] > 0
        assert sell_trade["fee_rate_bps"] == 200
        # Net proceeds = total_cost - fee
        net_proceeds = sell_trade["amount_usd"] - sell_trade["fee"]
        updated_account = sell_resp.json()["data"]["account"]
        assert updated_account["cash"] == pytest.approx(cash_after_buy + net_proceeds)


# -- Account ownership --

class TestAccountOwnership:
    def test_user2_cannot_trade_user1_account(self, client):
        user1 = _register(client, "bot1")
        user2 = _register(client, "bot2")
        account1 = _create_account(client, user1["api_key"])

        # user2 tries to buy on user1's account
        resp = _buy(client, user2["api_key"], account1["id"], amount=10.0)
        assert resp.status_code == 403

    def test_user2_cannot_sell_user1_account(self, client):
        user1 = _register(client, "bot1")
        user2 = _register(client, "bot2")
        account1 = _create_account(client, user1["api_key"])

        # user2 tries to sell on user1's account
        resp = _sell(client, user2["api_key"], account1["id"], shares=10.0)
        assert resp.status_code == 403
