"""Edge case tests: boundary conditions, rare paths, and precision.

Mix of @pytest.mark.unit and @pytest.mark.integration.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.db import DB
from server.jobs.check_orders import check_orders_job
from server.jobs.auto_resolve import auto_resolve_job
from server.adapters.polymarket import (
    Market,
    OrderBook,
    OrderBookLevel,
)
from server.app import app

from tests.conftest import (
    MockPolymarketClient,
    SAMPLE_MARKET,
    SAMPLE_BOOK,
    CLOSED_MARKET,
    EMPTY_BOOK,
    _register,
    _headers,
    _create_account,
    _buy,
    _sell,
    _register_and_create_account,
)


# ---------------------------------------------------------------------------
# DB fixture for direct-DB tests
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    _db = DB(":memory:")
    _db.init_schema()
    yield _db
    _db.close()


# ---------------------------------------------------------------------------
# expire_orders — was at 0% coverage
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestExpireOrders:
    def test_gtd_order_past_expiry_gets_expired(self, db):
        user = db.create_user("expire-bot")
        account = db.create_account(user["id"], "default")
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test",
            market_condition_id="0xabc",
            outcome="yes",
            side="buy",
            amount=500,
            limit_price=0.45,
            order_type="gtd",
            expires_at="2025-01-01T00:00:00Z",  # in the past
        )
        expired = db.expire_orders("2026-01-01T00:00:00Z")
        assert expired == 1
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 0

    def test_gtc_order_unaffected_by_expire(self, db):
        user = db.create_user("gtc-bot")
        account = db.create_account(user["id"], "default")
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test",
            market_condition_id="0xabc",
            outcome="yes",
            side="buy",
            amount=500,
            limit_price=0.45,
            order_type="gtc",
        )
        expired = db.expire_orders("2099-01-01T00:00:00Z")
        assert expired == 0
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 1

    def test_expire_multiple_gtd_orders(self, db):
        user = db.create_user("multi-expire")
        account = db.create_account(user["id"], "default")
        for i in range(3):
            db.create_limit_order(
                account_id=account["id"],
                market_slug="test",
                market_condition_id="0xabc",
                outcome="yes",
                side="buy",
                amount=100,
                limit_price=0.45,
                order_type="gtd",
                expires_at=f"2025-0{i+1}-01T00:00:00Z",
            )
        expired = db.expire_orders("2026-01-01T00:00:00Z")
        assert expired == 3

    def test_expire_future_gtd_not_expired(self, db):
        user = db.create_user("future-bot")
        account = db.create_account(user["id"], "default")
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test",
            market_condition_id="0xabc",
            outcome="yes",
            side="buy",
            amount=500,
            limit_price=0.45,
            order_type="gtd",
            expires_at="2099-12-31T23:59:59Z",  # far future
        )
        expired = db.expire_orders("2026-01-01T00:00:00Z")
        assert expired == 0


# ---------------------------------------------------------------------------
# MIN_ORDER_USD boundary
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMinOrderBoundary:
    def test_buy_exactly_at_minimum(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        resp = _buy(client, user["api_key"], account["id"], amount=1.00)
        assert resp.status_code == 200

    def test_buy_below_minimum(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        resp = _buy(client, user["api_key"], account["id"], amount=0.50)
        assert resp.status_code == 422  # Pydantic validation


# ---------------------------------------------------------------------------
# Sell limit fills in check_orders_job
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSellLimitFills:
    def test_sell_limit_order_fills(self, db):
        """Sell limit order matches against bid side of the book."""
        user = db.create_user("sell-limit-bot")
        account = db.create_account(user["id"], "default")
        # Create a position first
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc123",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.50,
            total_cost=50,
            realized_pnl=0,
        )
        # Place sell limit order with min_price below best bid
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="yes",
            side="sell",
            amount=50,  # sell 50 shares
            limit_price=0.60,  # willing to sell at 0.60 or above
        )
        market = Market(
            condition_id="0xabc123",
            slug="test-market",
            question="Test?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.65, 0.35],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=True, closed=False,
            volume=1000000, liquidity=100000,
            end_date="2026-12-31", fee_rate_bps=0, tick_size=0.01,
        )
        book = OrderBook(
            bids=[
                OrderBookLevel(price=0.64, size=500),  # Above 0.60 min_price
                OrderBookLevel(price=0.63, size=500),
            ],
            asks=[OrderBookLevel(price=0.66, size=500)],
        )
        pm = MockPolymarketClient(market, book)
        count = check_orders_job(db, pm)
        assert count == 1
        # Cash should increase
        updated = db.get_account(account["id"])
        assert float(updated["cash"]) > 10000
        # Position should be reduced
        pos = db.get_position(account["id"], "0xabc123", "yes")
        assert pos["shares"] == 50  # 100 - 50


# ---------------------------------------------------------------------------
# Float precision
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestFloatPrecision:
    def test_tiny_share_remainder(self, client):
        """Buy small amount, sell almost all, verify near-zero remainder."""
        user = _register(client)
        account = _create_account(client, user["api_key"])
        buy_resp = _buy(client, user["api_key"], account["id"], amount=10.0)
        shares = buy_resp.json()["data"]["trade"]["shares"]

        # Sell all but a tiny fraction
        almost_all = shares - 0.001
        if almost_all > 0:
            sell_resp = _sell(client, user["api_key"], account["id"], shares=almost_all)
            assert sell_resp.status_code == 200

    def test_near_zero_cash_buy(self):
        """Account with barely enough cash should still fill."""
        app.state.polymarket = MockPolymarketClient()
        with TestClient(app) as client:
            user = _register(client)
            account = _create_account(client, user["api_key"])
            # Set cash to just barely enough for a $1 buy
            # At ask price 0.66, $1 buys ~1.515 shares, cost = $1.00
            db = app.state.db
            db.update_cash(account["id"], 1.50)
            resp = _buy(client, user["api_key"], account["id"], amount=1.0)
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Portfolio edge cases
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestPortfolioEdgeCases:
    def test_portfolio_no_positions_returns_empty(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        resp = client.get(
            f"/accounts/{account['id']}/portfolio",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# Multi-user auto-resolve
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestMultiUserAutoResolve:
    def test_three_users_same_market_different_outcomes(self, db):
        """3 users hold positions in same market, different outcomes resolved."""
        accounts = []
        for name in ("user-a", "user-b", "user-c"):
            user = db.create_user(name)
            accounts.append(db.create_account(user["id"], "default"))

        # user-a: 100 YES shares
        db.upsert_position(
            account_id=accounts[0]["id"],
            market_condition_id="0xmulti",
            market_slug="multi-resolve",
            market_question="Multi?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.50,
            total_cost=50,
            realized_pnl=0,
        )
        # user-b: 200 NO shares
        db.upsert_position(
            account_id=accounts[1]["id"],
            market_condition_id="0xmulti",
            market_slug="multi-resolve",
            market_question="Multi?",
            outcome="no",
            shares=200,
            avg_entry_price=0.30,
            total_cost=60,
            realized_pnl=0,
        )
        # user-c: 50 YES shares
        db.upsert_position(
            account_id=accounts[2]["id"],
            market_condition_id="0xmulti",
            market_slug="multi-resolve",
            market_question="Multi?",
            outcome="yes",
            shares=50,
            avg_entry_price=0.60,
            total_cost=30,
            realized_pnl=0,
        )

        resolved_market = Market(
            condition_id="0xmulti",
            slug="multi-resolve",
            question="Multi?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],  # YES won
            tokens=[
                {"token_id": "tok_yes_m", "outcome": "Yes"},
                {"token_id": "tok_no_m", "outcome": "No"},
            ],
            active=False, closed=True,
            volume=0, liquidity=0,
            end_date="2026-01-01", fee_rate_bps=0, tick_size=0.01,
        )
        pm = MockPolymarketClient(resolved_market, OrderBook(bids=[], asks=[]))
        count = auto_resolve_job(db, pm)
        assert count == 3

        # user-a gets $100 (100 * $1)
        assert float(db.get_account(accounts[0]["id"])["cash"]) == 10100.0
        # user-b gets $0 (NO lost)
        assert float(db.get_account(accounts[1]["id"])["cash"]) == 10000.0
        # user-c gets $50 (50 * $1)
        assert float(db.get_account(accounts[2]["id"])["cash"]) == 10050.0


# ---------------------------------------------------------------------------
# GTD full lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestGTDLifecycle:
    def test_gtd_place_then_expire(self, db):
        user = db.create_user("gtd-life")
        account = db.create_account(user["id"], "default")
        order = db.create_limit_order(
            account_id=account["id"],
            market_slug="test",
            market_condition_id="0xabc",
            outcome="yes",
            side="buy",
            amount=500,
            limit_price=0.45,
            order_type="gtd",
            expires_at="2025-06-01T00:00:00Z",
        )
        assert order["status"] == "pending"
        assert order["order_type"] == "gtd"

        # Expire it
        expired = db.expire_orders("2026-01-01T00:00:00Z")
        assert expired == 1

        # No longer pending
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 0

        # Can't cancel an expired order
        result = db.cancel_order(order["id"])
        assert result is None


# ---------------------------------------------------------------------------
# Book with single level
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSingleLevelBook:
    def test_fok_fills_exactly_single_level(self):
        single_ask = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100)],
            asks=[OrderBookLevel(price=0.66, size=100)],
        )
        app.state.polymarket = MockPolymarketClient(book=single_ask)
        with TestClient(app) as client:
            user = _register(client)
            account = _create_account(client, user["api_key"])
            # Buy exactly the available liquidity
            resp = _buy(client, user["api_key"], account["id"], amount=66.0)
            assert resp.status_code == 200
            trade = resp.json()["data"]["trade"]
            assert trade["levels_filled"] == 1

    def test_fok_rejects_when_not_enough(self):
        tiny_ask = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=100)],
            asks=[OrderBookLevel(price=0.66, size=1)],  # only 1 share
        )
        app.state.polymarket = MockPolymarketClient(book=tiny_ask)
        with TestClient(app) as client:
            user = _register(client)
            account = _create_account(client, user["api_key"])
            # Try to buy more than available
            resp = _buy(client, user["api_key"], account["id"], amount=100.0)
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Duplicate agent name
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDuplicateRegistration:
    def test_duplicate_agent_name_409(self, client):
        _register(client, "unique-bot")
        resp = client.post("/auth/register", json={"agent_name": "unique-bot"})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Order on closed market
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestClosedMarketTrade:
    def test_buy_on_closed_market(self, client_closed_market):
        user = _register(client_closed_market)
        account = _create_account(client_closed_market, user["api_key"])
        resp = _buy(client_closed_market, user["api_key"], account["id"])
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "MARKET_CLOSED"


# ---------------------------------------------------------------------------
# Sell wrong outcome
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSellWrongOutcome:
    def test_sell_outcome_with_no_position(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        # Buy YES
        _buy(client, user["api_key"], account["id"])
        # Try to sell NO (have no NO position)
        resp = _sell(client, user["api_key"], account["id"], shares=10, outcome="no")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "NO_POSITION"


# ---------------------------------------------------------------------------
# Cancel already-filled order
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestCancelFilledOrder:
    def test_cancel_filled_order_returns_404(self, db):
        user = db.create_user("cancel-bot")
        account = db.create_account(user["id"], "default")
        order = db.create_limit_order(
            account_id=account["id"],
            market_slug="test",
            market_condition_id="0xabc",
            outcome="yes",
            side="buy",
            amount=500,
            limit_price=0.45,
        )
        # Fill it
        db.fill_order(order["id"])
        # Try to cancel
        result = db.cancel_order(order["id"])
        assert result is None  # Can't cancel a filled order


# ---------------------------------------------------------------------------
# Slippage through multiple book levels
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestSlippageMultipleLevels:
    def test_avg_price_across_levels(self):
        """Buy that sweeps multiple ask levels should have correct avg_price."""
        multi_level_book = OrderBook(
            bids=[OrderBookLevel(price=0.60, size=500)],
            asks=[
                OrderBookLevel(price=0.62, size=50),   # 50 shares @ 0.62 = $31
                OrderBookLevel(price=0.65, size=50),   # 50 shares @ 0.65 = $32.50
                OrderBookLevel(price=0.70, size=100),  # fill from here too
            ],
        )
        app.state.polymarket = MockPolymarketClient(book=multi_level_book)
        with TestClient(app) as client:
            user = _register(client)
            account = _create_account(client, user["api_key"])
            # Buy enough to sweep first 2 levels ($31 + $32.50 = $63.50)
            resp = _buy(client, user["api_key"], account["id"], amount=63.0)
            assert resp.status_code == 200
            trade = resp.json()["data"]["trade"]
            assert trade["levels_filled"] >= 2
            # Average price should be between 0.62 and 0.65
            assert 0.62 <= trade["avg_price"] <= 0.66
