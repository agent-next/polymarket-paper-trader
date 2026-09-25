"""Shared test fixtures and helpers.

Single source of truth for MockPolymarketClient, market/book constants,
client fixtures, and test helper functions.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.adapters.polymarket import (
    Market,
    OrderBook,
    OrderBookLevel,
)
from server.app import app


# ---------------------------------------------------------------------------
# Market / book constants
# ---------------------------------------------------------------------------

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

E2E_MARKET = Market(
    condition_id="0xe2e",
    slug="e2e-test-market",
    question="Will this E2E test pass?",
    description="",
    outcomes=["Yes", "No"],
    outcome_prices=[0.70, 0.30],
    tokens=[
        {"token_id": "tok_yes_e2e", "outcome": "Yes"},
        {"token_id": "tok_no_e2e", "outcome": "No"},
    ],
    active=True,
    closed=False,
    volume=1_000_000,
    liquidity=100_000,
    end_date="2026-12-31",
    fee_rate_bps=200,
    tick_size=0.01,
)

E2E_BOOK = OrderBook(
    bids=[
        OrderBookLevel(price=0.69, size=5000),
        OrderBookLevel(price=0.68, size=5000),
        OrderBookLevel(price=0.67, size=5000),
    ],
    asks=[
        OrderBookLevel(price=0.71, size=5000),
        OrderBookLevel(price=0.72, size=5000),
        OrderBookLevel(price=0.73, size=5000),
    ],
)


# ---------------------------------------------------------------------------
# MockPolymarketClient — unified test double
# ---------------------------------------------------------------------------

class MockPolymarketClient:
    """Test double for PolymarketClient with configurable responses."""

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


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_polymarket():
    """Return the MockPolymarketClient class for custom instantiation."""
    return MockPolymarketClient


@pytest.fixture
def client():
    """TestClient with default mock (no fees, open market, standard book)."""
    app.state.polymarket = MockPolymarketClient()
    app.state.scheduler = None  # Skip scheduler in tests
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_fees():
    app.state.polymarket = MockPolymarketClient(fee_rate=200)
    app.state.scheduler = None
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_closed_market():
    app.state.polymarket = MockPolymarketClient(market=CLOSED_MARKET)
    app.state.scheduler = None
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_empty_book():
    app.state.polymarket = MockPolymarketClient(book=EMPTY_BOOK)
    app.state.scheduler = None
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_e2e():
    """TestClient with E2E market (fees=200bps, deep book, midpoint=0.70)."""
    app.state.polymarket = MockPolymarketClient(
        market=E2E_MARKET, book=E2E_BOOK, fee_rate=200, midpoint=0.70,
    )
    app.state.scheduler = None
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _register(client, name="test-bot"):
    resp = client.post("/auth/register", json={"agent_name": name})
    return resp.json()["data"]


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def _create_account(client, api_key, name="default"):
    resp = client.post(
        "/accounts", json={"name": name}, headers=_headers(api_key)
    )
    return resp.json()["data"]


def _buy(client, api_key, account_id, amount=10.0, outcome="yes",
         slug="will-bitcoin-hit-100k", order_type="fok"):
    return client.post(
        "/trade/buy",
        json={
            "account_id": account_id,
            "market_slug": slug,
            "outcome": outcome,
            "amount_usd": amount,
            "order_type": order_type,
        },
        headers=_headers(api_key),
    )


def _sell(client, api_key, account_id, shares, outcome="yes",
          slug="will-bitcoin-hit-100k", order_type="fok"):
    return client.post(
        "/trade/sell",
        json={
            "account_id": account_id,
            "market_slug": slug,
            "outcome": outcome,
            "shares": shares,
            "order_type": order_type,
        },
        headers=_headers(api_key),
    )


def _register_and_create_account(client, name="test-bot"):
    """Register + create account in one call. Returns (user, account, headers)."""
    user = _register(client, name)
    headers = _headers(user["api_key"])
    resp = client.post("/accounts", json={"name": "default"}, headers=headers)
    account = resp.json()["data"]
    return user, account, headers


def _insert_trades(client, account_id, count=10):
    """Insert trades directly via DB to qualify for leaderboard."""
    db = client.app.state.db
    for _ in range(count):
        db.insert_trade(
            account_id=account_id,
            market_condition_id="0xabc",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            side="buy",
            order_type="fok",
            avg_price=0.5,
            amount_usd=100,
            shares=200,
            fee_rate_bps=0,
            fee=0,
            slippage=0,
            levels_filled=1,
            is_partial=False,
            book_snapshot_id=None,
        )
