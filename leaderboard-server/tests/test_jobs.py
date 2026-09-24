"""Test background jobs."""
from __future__ import annotations

import pytest

from server.db import DB
from server.jobs.check_orders import check_orders_job
from server.jobs.auto_resolve import auto_resolve_job
from server.adapters.polymarket import (
    Market,
    OrderBook,
    OrderBookLevel,
)

from tests.conftest import MockPolymarketClient


@pytest.fixture
def db():
    _db = DB(":memory:")
    _db.init_schema()
    yield _db
    _db.close()


@pytest.fixture
def sample_market():
    return Market(
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
        active=True,
        closed=False,
        volume=1000000,
        liquidity=100000,
        end_date="2026-12-31",
        fee_rate_bps=0,
        tick_size=0.01,
    )


@pytest.fixture
def sample_book():
    return OrderBook(
        bids=[
            OrderBookLevel(price=0.64, size=150),
            OrderBookLevel(price=0.63, size=200),
        ],
        asks=[
            OrderBookLevel(price=0.45, size=500),  # Below limit price of 0.50
            OrderBookLevel(price=0.48, size=300),
        ],
    )


class TestCheckOrders:
    def test_no_pending_orders(self, db, sample_market, sample_book):
        pm = MockPolymarketClient(sample_market, sample_book)
        count = check_orders_job(db, pm)
        assert count == 0

    def test_buy_order_fills(self, db, sample_market, sample_book):
        user = db.create_user("job-bot")
        account = db.create_account(user["id"], "default")
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100,
            limit_price=0.50,
        )
        pm = MockPolymarketClient(sample_market, sample_book)
        count = check_orders_job(db, pm)
        assert count == 1
        # Check order was filled
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 0
        # Check trade was recorded
        trades = db.get_trades(account["id"])
        assert len(trades) == 1
        assert trades[0]["side"] == "buy"
        assert trades[0]["book_snapshot_id"] is not None
        # Check cash was deducted
        updated = db.get_account(account["id"])
        assert float(updated["cash"]) < 10000

    def test_mixed_outcomes_use_their_own_books(self, db, sample_market):
        """YES/NO orders in one market must not share the same order book."""
        user = db.create_user("mixed-outcome-bot")
        account = db.create_account(user["id"], "default")
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=50,
            limit_price=0.50,
        )
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="no",
            side="buy",
            amount=50,
            limit_price=0.50,
        )

        yes_book = OrderBook(
            bids=[OrderBookLevel(price=0.40, size=200)],
            asks=[OrderBookLevel(price=0.45, size=200)],
        )
        no_book = OrderBook(
            bids=[OrderBookLevel(price=0.30, size=200)],
            asks=[OrderBookLevel(price=0.70, size=200)],
        )

        class OutcomeBookClient:
            def get_market(self, slug: str):
                return sample_market

            def get_order_book(self, token_id: str):
                if token_id == "tok_yes":
                    return yes_book
                if token_id == "tok_no":
                    return no_book
                raise KeyError(token_id)

            def get_fee_rate(self, token_id: str) -> int:
                return 0

        count = check_orders_job(db, OutcomeBookClient())
        assert count == 1
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 1
        assert pending[0]["outcome"] == "no"

    def test_check_orders_expires_gtd_before_matching(self, db, sample_market, sample_book):
        """Expired GTD orders should be marked expired and skipped by matching."""
        user = db.create_user("gtd-bot")
        account = db.create_account(user["id"], "default")
        order = db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100,
            limit_price=0.50,
            order_type="gtd",
            expires_at="2000-01-01T00:00:00Z",
        )

        pm = MockPolymarketClient(sample_market, sample_book)
        count = check_orders_job(db, pm)
        assert count == 0
        pending = db.get_pending_orders(account["id"])
        assert pending == []

        row = db._conn.execute(
            "SELECT status FROM limit_orders WHERE id = ?",
            (order["id"],),
        ).fetchone()
        assert row["status"] == "expired"

    def test_order_not_filled_above_limit(self, db, sample_market):
        """Order with limit price below all asks should not fill."""
        user = db.create_user("job-bot")
        account = db.create_account(user["id"], "default")
        db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100,
            limit_price=0.30,  # Below all asks
        )
        book = OrderBook(
            bids=[OrderBookLevel(price=0.64, size=150)],
            asks=[OrderBookLevel(price=0.66, size=80)],  # All above 0.30
        )
        pm = MockPolymarketClient(sample_market, book)
        count = check_orders_job(db, pm)
        assert count == 0
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 1  # Still pending


class TestAutoResolve:
    def test_no_open_positions(self, db, sample_market):
        pm = MockPolymarketClient(sample_market, OrderBook(bids=[], asks=[]))
        count = auto_resolve_job(db, pm)
        assert count == 0

    def test_resolve_winning_position(self, db):
        user = db.create_user("resolve-bot")
        account = db.create_account(user["id"], "default")
        # Create a position
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xdef",
            market_slug="resolved-market",
            market_question="Test?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.50,
            total_cost=50,
            realized_pnl=0,
        )
        # Mock a resolved market where YES won
        resolved_market = Market(
            condition_id="0xdef",
            slug="resolved-market",
            question="Test?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],  # YES won
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
            volume=0,
            liquidity=0,
            end_date="2026-01-01",
            fee_rate_bps=0,
            tick_size=0.01,
        )
        pm = MockPolymarketClient(resolved_market, OrderBook(bids=[], asks=[]))
        count = auto_resolve_job(db, pm)
        assert count == 1
        # Check cash: $10000 + 100 shares * $1 = $10100
        updated = db.get_account(account["id"])
        assert float(updated["cash"]) == 10100.0
        # Position should be resolved
        positions = db.get_open_positions(account["id"])
        assert len(positions) == 0

    def test_resolve_losing_position(self, db):
        user = db.create_user("loser-bot")
        account = db.create_account(user["id"], "default")
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xdef",
            market_slug="resolved-market",
            market_question="Test?",
            outcome="no",  # Bet on NO, but YES won
            shares=100,
            avg_entry_price=0.30,
            total_cost=30,
            realized_pnl=0,
        )
        resolved_market = Market(
            condition_id="0xdef",
            slug="resolved-market",
            question="Test?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
            volume=0,
            liquidity=0,
            end_date="2026-01-01",
            fee_rate_bps=0,
            tick_size=0.01,
        )
        pm = MockPolymarketClient(resolved_market, OrderBook(bids=[], asks=[]))
        count = auto_resolve_job(db, pm)
        assert count == 1
        # Cash unchanged (no payout for losers)
        updated = db.get_account(account["id"])
        assert float(updated["cash"]) == 10000.0

    def test_active_market_not_resolved(self, db, sample_market, sample_book):
        """Active (non-closed) market should not be resolved."""
        user = db.create_user("active-bot")
        account = db.create_account(user["id"], "default")
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc123",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.65,
            total_cost=65,
            realized_pnl=0,
        )
        pm = MockPolymarketClient(sample_market, sample_book)
        count = auto_resolve_job(db, pm)
        assert count == 0
        positions = db.get_open_positions(account["id"])
        assert len(positions) == 1
