"""Tests for the trade execution engine."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pm_trader.db import Database
from pm_trader.engine import Engine
from pm_trader.models import (
    ApiError,
    InsufficientBalanceError,
    InvalidOutcomeError,
    Market,
    MarketClosedError,
    NoPositionError,
    NotInitializedError,
    OrderBook,
    OrderBookLevel,
    OrderRejectedError,
    TickSizeViolationError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_data_dir: Path) -> Engine:
    eng = Engine(tmp_data_dir)
    yield eng
    eng.close()


@pytest.fixture
def initialized_engine(engine: Engine) -> Engine:
    """Engine with an initialized $10k account."""
    engine.init_account(10_000.0)
    return engine


def _make_book(
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> OrderBook:
    """Helper to build an OrderBook from tuples."""
    return OrderBook(
        bids=[OrderBookLevel(price=p, size=s) for p, s in (bids or [])],
        asks=[OrderBookLevel(price=p, size=s) for p, s in (asks or [])],
    )


SAMPLE_MARKET = Market(
    condition_id="0xabc123",
    slug="will-bitcoin-hit-100k",
    question="Will Bitcoin hit $100k?",
    description="BTC market",
    outcomes=["Yes", "No"],
    outcome_prices=[0.65, 0.35],
    tokens=[
        {"token_id": "tok_yes", "outcome": "Yes"},
        {"token_id": "tok_no", "outcome": "No"},
    ],
    active=True,
    closed=False,
    volume=5_000_000.0,
    liquidity=250_000.0,
    end_date="2026-12-31",
    fee_rate_bps=0,
    tick_size=0.01,
)

SAMPLE_BOOK = _make_book(
    bids=[(0.64, 500), (0.63, 500), (0.62, 500)],
    asks=[(0.66, 500), (0.67, 500), (0.68, 500)],
)


def _mock_api(engine: Engine, market=None, book=None, fee_rate=0):
    """Patch the engine's API client methods."""
    m = market or SAMPLE_MARKET
    b = book or SAMPLE_BOOK
    engine.api.get_market = MagicMock(return_value=m)
    engine.api.get_trade_context = MagicMock(return_value=(m, b, fee_rate))
    engine.api.get_order_book = MagicMock(return_value=b)
    engine.api.get_fee_rate = MagicMock(return_value=fee_rate)
    engine.api.get_midpoint = MagicMock(return_value=0.65)


# ---------------------------------------------------------------------------
# Account tests
# ---------------------------------------------------------------------------


class TestAccount:
    def test_init_account(self, engine: Engine):
        account = engine.init_account(5000.0)
        assert account.cash == 5000.0
        assert account.starting_balance == 5000.0

    def test_get_account_not_initialized(self, engine: Engine):
        with pytest.raises(NotInitializedError):
            engine.get_account()

    def test_get_account_after_init(self, initialized_engine: Engine):
        account = initialized_engine.get_account()
        assert account.cash == 10_000.0

    def test_reset(self, initialized_engine: Engine):
        initialized_engine.reset()
        with pytest.raises(NotInitializedError):
            initialized_engine.get_account()


# ---------------------------------------------------------------------------
# Buy tests
# ---------------------------------------------------------------------------


class TestBuy:
    def test_basic_buy(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        result = initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)
        assert result.trade.side == "buy"
        assert result.trade.outcome == "yes"
        assert result.trade.amount_usd > 0
        assert result.trade.shares > 0
        assert result.account.cash < 10_000.0

    def test_buy_updates_position(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)
        pos = initialized_engine.db.get_position("0xabc123", "yes")
        assert pos is not None
        assert pos.shares > 0
        assert pos.total_cost > 0

    def test_buy_adds_to_existing_position(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 50.0)
        pos1 = initialized_engine.db.get_position("0xabc123", "yes")
        shares1 = pos1.shares

        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 50.0)
        pos2 = initialized_engine.db.get_position("0xabc123", "yes")
        assert pos2.shares > shares1

    def test_buy_no_outcome(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        result = initialized_engine.buy("will-bitcoin-hit-100k", "no", 100.0)
        assert result.trade.outcome == "no"

    def test_buy_insufficient_balance(self, initialized_engine: Engine):
        # Book has enough liquidity but account doesn't have enough cash
        deep_book = _make_book(
            bids=[(0.64, 100_000)],
            asks=[(0.66, 100_000)],  # $66k of liquidity
        )
        _mock_api(initialized_engine, book=deep_book)
        with pytest.raises(InsufficientBalanceError):
            initialized_engine.buy("btc", "yes", 50_000.0)

    def test_buy_invalid_outcome(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(InvalidOutcomeError):
            initialized_engine.buy("btc", "maybe", 100.0)

    def test_buy_below_minimum(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(OrderRejectedError, match="Minimum"):
            initialized_engine.buy("btc", "yes", 0.5)

    def test_buy_closed_market(self, initialized_engine: Engine):
        closed = Market(
            condition_id="0xclosed",
            slug="closed-market",
            question="Closed?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "t1", "outcome": "Yes"},
                {"token_id": "t2", "outcome": "No"},
            ],
            active=False,
            closed=True,
            fee_rate_bps=0,
            tick_size=0.01,
        )
        _mock_api(initialized_engine, market=closed)
        with pytest.raises(MarketClosedError):
            initialized_engine.buy("closed-market", "yes", 100.0)

    def test_buy_inactive_market_rejected(self, initialized_engine: Engine):
        inactive = Market(
            condition_id="0xabc123",
            slug="inactive-market",
            question="Inactive?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.65, 0.35],
            tokens=[
                {"token_id": "t1", "outcome": "Yes"},
                {"token_id": "t2", "outcome": "No"},
            ],
            active=False,
            closed=False,
        )
        _mock_api(initialized_engine, market=inactive)
        with pytest.raises(OrderRejectedError, match="not active"):
            initialized_engine.buy("inactive-market", "yes", 100.0)

    def test_buy_market_not_accepting_orders_rejected(self, initialized_engine: Engine):
        paused = Market(
            condition_id="0xabc123",
            slug="paused-market",
            question="Paused?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.65, 0.35],
            tokens=[
                {"token_id": "t1", "outcome": "Yes"},
                {"token_id": "t2", "outcome": "No"},
            ],
            active=True,
            closed=False,
            accepting_orders=False,
        )
        _mock_api(initialized_engine, market=paused)
        with pytest.raises(OrderRejectedError, match="not accepting orders"):
            initialized_engine.buy("paused-market", "yes", 100.0)

    def test_buy_fok_rejected_insufficient_liquidity(self, initialized_engine: Engine):
        thin_book = _make_book(
            bids=[(0.64, 10)],
            asks=[(0.66, 10)],  # Only $6.60 of liquidity
        )
        _mock_api(initialized_engine, book=thin_book)
        with pytest.raises(OrderRejectedError, match="FOK"):
            initialized_engine.buy("btc", "yes", 100.0, order_type="fok")

    def test_buy_fak_partial_fill(self, initialized_engine: Engine):
        thin_book = _make_book(
            bids=[(0.64, 10)],
            asks=[(0.66, 10)],  # Only $6.60 of liquidity
        )
        _mock_api(initialized_engine, book=thin_book)
        result = initialized_engine.buy("btc", "yes", 100.0, order_type="fak")
        assert result.trade.is_partial is True
        assert result.trade.amount_usd < 100.0

    def test_buy_with_fees(self, initialized_engine: Engine):
        _mock_api(initialized_engine, fee_rate=200)
        result = initialized_engine.buy("btc", "yes", 100.0)
        assert result.trade.fee > 0
        assert result.trade.fee_rate_bps == 200

    def test_buy_deducts_cost_plus_fee(self, initialized_engine: Engine):
        _mock_api(initialized_engine, fee_rate=200)
        result = initialized_engine.buy("btc", "yes", 100.0)
        expected_cash = 10_000.0 - result.trade.amount_usd - result.trade.fee
        assert abs(result.account.cash - expected_cash) < 0.01

    def test_buy_records_multiple_levels(self, initialized_engine: Engine):
        multi_level_book = _make_book(
            bids=[(0.64, 50)],
            asks=[(0.66, 50), (0.67, 50)],  # Two levels
        )
        _mock_api(initialized_engine, book=multi_level_book)
        result = initialized_engine.buy("btc", "yes", 50.0)
        assert result.trade.levels_filled >= 1


# ---------------------------------------------------------------------------
# Sell tests
# ---------------------------------------------------------------------------


class TestSell:
    def _setup_position(self, engine: Engine):
        """Buy some shares first so we have a position to sell."""
        _mock_api(engine)
        engine.buy("will-bitcoin-hit-100k", "yes", 100.0)

    def test_basic_sell(self, initialized_engine: Engine):
        self._setup_position(initialized_engine)
        pos = initialized_engine.db.get_position("0xabc123", "yes")
        sell_shares = pos.shares / 2

        _mock_api(initialized_engine)
        result = initialized_engine.sell("will-bitcoin-hit-100k", "yes", sell_shares)
        assert result.trade.side == "sell"
        assert result.trade.shares == pytest.approx(sell_shares, abs=0.01)

    def test_sell_increases_cash(self, initialized_engine: Engine):
        self._setup_position(initialized_engine)
        cash_before = initialized_engine.get_account().cash

        pos = initialized_engine.db.get_position("0xabc123", "yes")
        _mock_api(initialized_engine)
        initialized_engine.sell("will-bitcoin-hit-100k", "yes", pos.shares / 2)
        cash_after = initialized_engine.get_account().cash
        assert cash_after > cash_before

    def test_sell_reduces_position(self, initialized_engine: Engine):
        self._setup_position(initialized_engine)
        pos_before = initialized_engine.db.get_position("0xabc123", "yes")

        _mock_api(initialized_engine)
        initialized_engine.sell("will-bitcoin-hit-100k", "yes", pos_before.shares / 2)
        pos_after = initialized_engine.db.get_position("0xabc123", "yes")
        assert pos_after.shares < pos_before.shares

    def test_sell_no_position(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(NoPositionError):
            initialized_engine.sell("will-bitcoin-hit-100k", "yes", 10.0)

    def test_sell_more_than_held(self, initialized_engine: Engine):
        self._setup_position(initialized_engine)
        pos = initialized_engine.db.get_position("0xabc123", "yes")

        _mock_api(initialized_engine)
        with pytest.raises(OrderRejectedError, match="Cannot sell"):
            initialized_engine.sell("will-bitcoin-hit-100k", "yes", pos.shares + 100)

    def test_sell_with_fees(self, initialized_engine: Engine):
        self._setup_position(initialized_engine)
        pos = initialized_engine.db.get_position("0xabc123", "yes")

        _mock_api(initialized_engine, fee_rate=175)
        result = initialized_engine.sell("will-bitcoin-hit-100k", "yes", pos.shares / 2)
        assert result.trade.fee > 0
        assert result.trade.fee_rate_bps == 175

    def test_sell_realized_pnl_tracked(self, initialized_engine: Engine):
        self._setup_position(initialized_engine)
        pos = initialized_engine.db.get_position("0xabc123", "yes")

        _mock_api(initialized_engine)
        initialized_engine.sell("will-bitcoin-hit-100k", "yes", pos.shares)
        pos_after = initialized_engine.db.get_position("0xabc123", "yes")
        # realized_pnl should be non-zero (could be profit or loss)
        assert pos_after.realized_pnl != 0.0 or pos_after.shares == 0


# ---------------------------------------------------------------------------
# Portfolio tests
# ---------------------------------------------------------------------------


class TestPortfolio:
    def test_empty_portfolio(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        portfolio = initialized_engine.get_portfolio()
        assert portfolio == []

    def test_portfolio_with_position(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)

        portfolio = initialized_engine.get_portfolio()
        assert len(portfolio) == 1
        assert portfolio[0]["outcome"] == "yes"
        assert portfolio[0]["shares"] > 0
        assert "unrealized_pnl" in portfolio[0]
        assert "live_price" in portfolio[0]

    def test_portfolio_not_initialized(self, engine: Engine):
        with pytest.raises(NotInitializedError):
            engine.get_portfolio()


# ---------------------------------------------------------------------------
# Balance tests
# ---------------------------------------------------------------------------


class TestBalance:
    def test_initial_balance(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        balance = initialized_engine.get_balance()
        assert balance["cash"] == 10_000.0
        assert balance["starting_balance"] == 10_000.0
        assert balance["positions_value"] == 0.0
        assert balance["total_value"] == 10_000.0
        assert balance["pnl"] == 0.0

    def test_balance_after_buy(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)

        balance = initialized_engine.get_balance()
        assert balance["cash"] < 10_000.0
        assert balance["positions_value"] > 0
        assert balance["total_value"] > 0


# ---------------------------------------------------------------------------
# History tests
# ---------------------------------------------------------------------------


class TestHistory:
    def test_empty_history(self, initialized_engine: Engine):
        trades = initialized_engine.get_history()
        assert trades == []

    def test_history_after_trades(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 50.0)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 50.0)

        trades = initialized_engine.get_history()
        assert len(trades) == 2
        # Newest first
        assert trades[0].id > trades[1].id


# ---------------------------------------------------------------------------
# Resolution tests
# ---------------------------------------------------------------------------


class TestResolve:
    def test_resolve_winning_position(self, initialized_engine: Engine):
        # Buy YES, then market resolves YES wins
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)
        pos = initialized_engine.db.get_position("0xabc123", "yes")
        shares = pos.shares

        # Now mock a resolved market where YES won
        resolved_market = Market(
            condition_id="0xabc123",
            slug="will-bitcoin-hit-100k",
            question="Will Bitcoin hit $100k?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
            fee_rate_bps=0,
            tick_size=0.01,
        )
        initialized_engine.api.get_market = MagicMock(return_value=resolved_market)

        results = initialized_engine.resolve_market("will-bitcoin-hit-100k")
        assert len(results) == 1
        assert results[0].payout == pytest.approx(shares, abs=0.01)
        assert results[0].position.is_resolved is True

    def test_resolve_losing_position(self, initialized_engine: Engine):
        # Buy YES, but NO wins
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)

        resolved_market = Market(
            condition_id="0xabc123",
            slug="will-bitcoin-hit-100k",
            question="Will Bitcoin hit $100k?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.0, 1.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
            fee_rate_bps=0,
            tick_size=0.01,
        )
        initialized_engine.api.get_market = MagicMock(return_value=resolved_market)

        results = initialized_engine.resolve_market("will-bitcoin-hit-100k")
        assert len(results) == 1
        assert results[0].payout == 0.0

    def test_resolve_not_closed(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)
        # Market is still open
        with pytest.raises(MarketClosedError):
            initialized_engine.resolve_market("will-bitcoin-hit-100k")

    def test_resolve_no_position(self, initialized_engine: Engine):
        resolved_market = Market(
            condition_id="0xnone",
            slug="no-pos",
            question="?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "t1", "outcome": "Yes"},
                {"token_id": "t2", "outcome": "No"},
            ],
            active=False,
            closed=True,
            fee_rate_bps=0,
            tick_size=0.01,
        )
        initialized_engine.api.get_market = MagicMock(return_value=resolved_market)
        with pytest.raises(NoPositionError):
            initialized_engine.resolve_market("no-pos")

    def test_resolve_adds_payout_to_cash(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("will-bitcoin-hit-100k", "yes", 100.0)
        pos = initialized_engine.db.get_position("0xabc123", "yes")
        cash_before_resolve = initialized_engine.get_account().cash

        resolved_market = Market(
            condition_id="0xabc123",
            slug="will-bitcoin-hit-100k",
            question="Will Bitcoin hit $100k?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
            fee_rate_bps=0,
            tick_size=0.01,
        )
        initialized_engine.api.get_market = MagicMock(return_value=resolved_market)
        initialized_engine.resolve_market("will-bitcoin-hit-100k")

        cash_after = initialized_engine.get_account().cash
        assert cash_after > cash_before_resolve


# ---------------------------------------------------------------------------
# Outcome validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_outcome_case_insensitive(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        result = initialized_engine.buy("btc", "YES", 50.0)
        assert result.trade.outcome == "yes"

    def test_outcome_whitespace_stripped(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        result = initialized_engine.buy("btc", " yes ", 50.0)
        assert result.trade.outcome == "yes"


class TestWatchPrices:
    def test_invalid_outcome_raises(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(ValueError, match="maybe"):
            initialized_engine.watch_prices(["btc"], ["maybe"])


class TestCheckOrders:
    def test_check_orders_rejects_order_when_market_becomes_inactive(
        self, initialized_engine: Engine,
    ):
        """A resting order in a paused market is rejected, not retried forever."""
        _mock_api(initialized_engine)
        from pm_trader.orders import create_order, get_pending_orders

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100.0,
            limit_price=0.55,
        )

        initialized_engine.api.get_market = MagicMock(
            return_value=replace(SAMPLE_MARKET, active=False)
        )
        results = initialized_engine.check_orders()

        rejected = [r for r in results if r["action"] == "rejected"]
        assert len(rejected) == 1
        assert "not active" in rejected[0]["reason"]
        assert rejected[0]["order"]["status"] == "rejected"
        assert len(get_pending_orders(initialized_engine.db.conn)) == 0


class TestCheckOrdersRejection:
    def test_unfillable_order_gets_rejected(self, initialized_engine: Engine):
        """An order with amount below minimum should be rejected, not retried forever."""
        _mock_api(initialized_engine)
        # Bypass engine validation by inserting directly into orders table
        from pm_trader.orders import create_order, get_pending_orders

        # Sell order with no position — permanently unfillable (NoPositionError)
        order = create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="sell",
            amount=10.0,
            limit_price=0.50,  # Low limit so best_bid (0.64) >= limit
        )
        assert len(get_pending_orders(initialized_engine.db.conn)) == 1

        results = initialized_engine.check_orders()

        # Order should be rejected, not still pending
        assert len(results) >= 1
        rejected = [r for r in results if r["action"] == "rejected"]
        assert len(rejected) == 1
        assert rejected[0]["order"]["status"] == "rejected"
        assert "No position" in rejected[0]["reason"]

        # No pending orders left
        assert len(get_pending_orders(initialized_engine.db.conn)) == 0


class TestLimitOrderValidation:
    def test_gtd_without_expiry_rejected(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(OrderRejectedError, match="expires_at"):
            initialized_engine.place_limit_order(
                "btc", "yes", "buy", 100.0, 0.50,
                order_type="gtd", expires_at=None,
            )

    def test_buy_amount_below_minimum_rejected(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(OrderRejectedError, match="Minimum"):
            initialized_engine.place_limit_order(
                "btc", "yes", "buy", 0.50, 0.55,
            )

    def test_limit_price_tick_size_violation_rejected(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(TickSizeViolationError):
            initialized_engine.place_limit_order(
                "btc", "yes", "buy", 100.0, 0.555,
            )
        # A violation leaves no order row behind
        assert initialized_engine.get_pending_orders() == []

    def test_zero_tick_size_short_circuits_validation(self):
        """A market reporting no tick size imposes no grid constraint."""
        Engine._validate_tick_size(0.555, 0.0)

    def test_limit_order_fetches_tick_size_when_market_tick_is_missing(
        self, initialized_engine: Engine,
    ):
        _mock_api(
            initialized_engine,
            market=replace(SAMPLE_MARKET, tick_size=0.0),
        )
        initialized_engine.api.get_tick_size = MagicMock(return_value=0.01)
        placed = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 100.0, 0.55,
        )
        initialized_engine.api.get_tick_size.assert_called_once_with("tok_yes")
        assert placed["status"] == "pending"


class TestLimitOrderPriceEnforcement:
    """Bug #1: Limit orders must NOT fill at prices beyond the limit."""

    def test_buy_limit_skips_asks_above_limit(self, initialized_engine: Engine):
        """A buy limit at 0.55 must NOT fill when all asks are above 0.55."""
        _mock_api(initialized_engine)
        from pm_trader.orders import create_order, get_pending_orders

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100.0,
            limit_price=0.55,  # Below best ask (0.66)
        )
        results = initialized_engine.check_orders()
        # Should NOT fill — all asks are above 0.55
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 0
        assert len(get_pending_orders(initialized_engine.db.conn)) == 1

    def test_buy_limit_fills_at_or_below_limit(self, initialized_engine: Engine):
        """A buy limit at 0.70 fills at asks 0.66, 0.67, 0.68 (all <= 0.70)."""
        _mock_api(initialized_engine)
        from pm_trader.orders import create_order, get_pending_orders

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100.0,
            limit_price=0.70,  # Above best ask (0.66)
        )
        results = initialized_engine.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 1
        assert len(get_pending_orders(initialized_engine.db.conn)) == 0


class TestLimitOrderRemainingAmount:
    """A partial FAK fill keeps its remainder open instead of dropping it."""

    def test_buy_partial_then_fill_uses_remaining_amount(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine)
        from pm_trader.orders import create_order

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100.0,
            limit_price=0.70,
        )

        # First check: only $33 of ask liquidity (0.66 x 50) -> partial fill
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 500)], asks=[(0.66, 50)])
        )
        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["partially_filled"]
        assert results[0]["order"]["remaining_amount"] == pytest.approx(67.0)
        assert len(initialized_engine.get_pending_orders()) == 1

        # Second check: the order retries only its $67 remainder
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 500)], asks=[(0.67, 500)])
        )
        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["filled"]
        assert initialized_engine.get_pending_orders() == []

        buys = [
            t for t in initialized_engine.get_history(limit=10) if t.side == "buy"
        ]
        assert len(buys) == 2
        assert sum(t.amount_usd for t in buys) == pytest.approx(100.0)

    def test_sell_partial_then_fill_uses_remaining_amount(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine)
        initialized_engine.buy("btc", "yes", 100.0)
        initial_shares = initialized_engine.db.get_position("0xabc123", "yes").shares

        from pm_trader.orders import create_order

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="sell",
            amount=100.0,  # 100 shares
            limit_price=0.60,
        )

        # First check: only 40 shares bid at 0.64 (>= 0.60 limit) -> partial
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 40)], asks=[(0.66, 500)])
        )
        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["partially_filled"]
        assert results[0]["order"]["remaining_amount"] == pytest.approx(60.0)

        # Second check: enough bids for the 60-share remainder
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 60)], asks=[(0.66, 500)])
        )
        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["filled"]
        assert initialized_engine.get_pending_orders() == []

        sells = [
            t for t in initialized_engine.get_history(limit=10) if t.side == "sell"
        ]
        assert len(sells) == 2
        assert sum(t.shares for t in sells) == pytest.approx(100.0)

        # The position only ever shrank by the shares actually sold
        pos = initialized_engine.db.get_position("0xabc123", "yes")
        assert pos.shares == pytest.approx(initial_shares - 100.0)


class TestMarketableLimit:
    def test_marketable_limit_partial_fill_at_placement_rests_remainder(
        self, initialized_engine: Engine,
    ):
        """A marketable limit that only partly fills at placement keeps the
        remainder as a partially_filled maker order (not dropped)."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        # $33 of ask depth (0.66 x 50) under a 0.70 limit
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 500)], asks=[(0.66, 50)])
        )

        placed = initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.70)
        assert placed["status"] == "partially_filled"
        assert placed["remaining_amount"] == pytest.approx(67.0)
        assert [o["id"] for o in initialized_engine.get_pending_orders()] == [
            placed["id"]
        ]

        # Liquidity deepens: the remainder fills as a maker via check_orders
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 500)], asks=[(0.67, 500)])
        )
        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["filled"]
        assert initialized_engine.get_pending_orders() == []

        buys = [
            t for t in initialized_engine.get_history(limit=10) if t.side == "buy"
        ]
        assert len(buys) == 2
        assert sum(t.amount_usd for t in buys) == pytest.approx(100.0)
        # History is newest-first: the last trade is the placement taker fill
        # (paid the fee) and the newest is the maker fill (fee-free).
        assert buys[-1].fee > 0.0
        assert buys[0].fee == 0.0


# ---------------------------------------------------------------------------
# Additional engine edge case tests (coverage gaps)
# ---------------------------------------------------------------------------


class TestValidateOutcome:
    def test_empty_outcome_raises(self, initialized_engine: Engine):
        with pytest.raises(InvalidOutcomeError):
            initialized_engine._validate_outcome("")

    def test_whitespace_only_raises(self, initialized_engine: Engine):
        with pytest.raises(InvalidOutcomeError):
            initialized_engine._validate_outcome("   ")


class TestSellEdgeCases:
    def test_sell_more_than_held(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("btc", "yes", 100.0)
        with pytest.raises(OrderRejectedError, match="Cannot sell"):
            initialized_engine.sell("btc", "yes", 99_999.0)

    def test_sell_closed_market(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("btc", "yes", 100.0)
        closed = Market(
            condition_id="0xabc123",
            slug="will-bitcoin-hit-100k",
            question="Q",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
        )
        initialized_engine.api.get_market = MagicMock(return_value=closed)
        with pytest.raises(MarketClosedError):
            initialized_engine.sell("btc", "yes", 10.0)

    def test_sell_fok_rejected_on_empty_book(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("btc", "yes", 100.0)
        empty_book = _make_book(bids=[], asks=[])
        initialized_engine.api.get_order_book = MagicMock(return_value=empty_book)
        with pytest.raises(OrderRejectedError, match="FOK rejected"):
            initialized_engine.sell("btc", "yes", 10.0)

    def test_sell_below_min_notional_rejected(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("btc", "yes", 100.0)
        tiny_notional_book = _make_book(bids=[(0.10, 1000)], asks=[(0.20, 1000)])
        initialized_engine.api.get_order_book = MagicMock(return_value=tiny_notional_book)
        # 5 shares x $0.10 best bid = $0.50 gross, below the $1 minimum
        with pytest.raises(OrderRejectedError, match="Minimum order size"):
            initialized_engine.sell("btc", "yes", 5.0)


class TestResolveMarket:
    def test_resolve_closed_market_with_winner(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        # Buy YES shares
        initialized_engine.buy("btc", "yes", 100.0)

        # Market resolves — YES wins
        resolved_market = Market(
            condition_id="0xabc123",
            slug="will-bitcoin-hit-100k",
            question="Q",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
        )
        initialized_engine.api.get_market = MagicMock(return_value=resolved_market)
        results = initialized_engine.resolve_market("btc")
        assert len(results) >= 1
        # YES payout should be $1/share
        yes_result = [r for r in results if r.position.outcome == "yes"]
        assert len(yes_result) == 1
        assert yes_result[0].payout > 0

    def test_resolve_not_closed(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(MarketClosedError, match="not yet closed"):
            initialized_engine.resolve_market("btc")

    def test_resolve_no_position(self, initialized_engine: Engine):
        closed = Market(
            condition_id="0xclosed",
            slug="closed-market",
            question="Q",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "t1", "outcome": "Yes"},
                {"token_id": "t2", "outcome": "No"},
            ],
            active=False,
            closed=True,
        )
        initialized_engine.api.get_market = MagicMock(return_value=closed)
        with pytest.raises(NoPositionError):
            initialized_engine.resolve_market("closed-market")


class TestResolveAll:
    def test_resolve_all_with_closed_market(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        # Buy YES shares
        initialized_engine.buy("btc", "yes", 100.0)

        # Market resolves — YES wins
        resolved_market = Market(
            condition_id="0xabc123",
            slug="will-bitcoin-hit-100k",
            question="Q",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
        )
        initialized_engine.api.get_market = MagicMock(return_value=resolved_market)
        results = initialized_engine.resolve_all()
        assert len(results) >= 1


class TestWatchPricesEdgeCases:
    def test_watch_market_not_found(self, initialized_engine: Engine):
        """Markets that can't be resolved are silently skipped."""
        from pm_trader.models import MarketNotFoundError
        initialized_engine.api.get_market = MagicMock(
            side_effect=MarketNotFoundError("bad")
        )
        result = initialized_engine.watch_prices(["bad"])
        assert result == []

    def test_watch_api_price_error(self, initialized_engine: Engine):
        """Price fetch errors are silently skipped for that market."""
        _mock_api(initialized_engine)
        initialized_engine.api.get_midpoint = MagicMock(side_effect=Exception("timeout"))
        result = initialized_engine.watch_prices(["btc"])
        assert result == []

    def test_watch_default_outcome(self, initialized_engine: Engine):
        """Without outcomes param, defaults to ['yes']."""
        _mock_api(initialized_engine)
        result = initialized_engine.watch_prices(["btc"])
        assert len(result) == 1
        assert result[0]["outcome"] == "yes"


class TestLimitOrderExecution:
    """Test the limit order fill execution paths."""

    def test_sell_limit_fills_when_bid_above_limit(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        # First buy shares
        initialized_engine.buy("btc", "yes", 100.0)
        from pm_trader.orders import create_order, get_pending_orders

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="sell",
            amount=10.0,
            limit_price=0.50,  # Below best bid (0.64), so should fill
        )
        results = initialized_engine.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 1
        assert len(get_pending_orders(initialized_engine.db.conn)) == 0

    def test_sell_limit_skips_when_bid_below_limit(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.buy("btc", "yes", 100.0)
        from pm_trader.orders import create_order, get_pending_orders

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="sell",
            amount=10.0,
            limit_price=0.90,  # Above best bid (0.64), so should NOT fill
        )
        results = initialized_engine.check_orders()
        filled = [r for r in results if r["action"] == "filled"]
        assert len(filled) == 0
        assert len(get_pending_orders(initialized_engine.db.conn)) == 1


class TestCancelAllOrders:
    def test_cancel_all_empty(self, initialized_engine: Engine):
        result = initialized_engine.cancel_all_orders()
        assert result == []

    def test_cancel_all_with_orders(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.55)
        initialized_engine.place_limit_order("btc", "yes", "buy", 200.0, 0.50)
        cancelled = initialized_engine.cancel_all_orders()
        assert len(cancelled) == 2
        assert initialized_engine.get_pending_orders() == []


class TestOrderTypeValidation:
    def test_invalid_order_type_rejected(self, initialized_engine: Engine):
        _mock_api(initialized_engine)
        with pytest.raises(OrderRejectedError, match="Invalid order_type"):
            initialized_engine.place_limit_order(
                "btc", "yes", "buy", 100.0, 0.55, order_type="bad",
            )


# ---------------------------------------------------------------------------
# feeSchedule-driven fee curve
# ---------------------------------------------------------------------------

def _schedule_market(**schedule) -> Market:
    """SAMPLE_MARKET carrying a Gamma-style feeSchedule block."""
    return replace(SAMPLE_MARKET, fee_schedule=dict(schedule))


CRYPTO_SCHEDULE = {"rate": 0.07, "exponent": 1, "takerOnly": True, "rebateRate": 0.25}


class TestFeeScheduleMarket:
    """A market carrying a feeSchedule is charged with the official curve."""

    def test_buy_charges_official_curve_on_shares(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        trade = initialized_engine.buy("btc", "yes", 100.0).trade

        # $100 at 0.66 = 151.515... shares
        assert trade.avg_price == pytest.approx(0.66)
        assert trade.fee == pytest.approx(
            trade.shares * 0.07 * 0.66 * (1 - 0.66)
        )
        # Audit trail keeps the rate as its bps equivalent (0.07 → 700)
        assert trade.fee_rate_bps == 700
        # feeSchedule is the fee source — the CLOB bps endpoint is not consulted
        initialized_engine.api.get_fee_rate.assert_not_called()

    def test_buy_below_half_pays_more_than_the_legacy_model(
        self, initialized_engine: Engine,
    ):
        book = _make_book(bids=[(0.28, 1000)], asks=[(0.30, 1000)])
        _mock_api(
            initialized_engine,
            market=_schedule_market(**CRYPTO_SCHEDULE),
            book=book,
        )
        trade = initialized_engine.buy("btc", "yes", 100.0).trade

        # 333.33 shares × 0.07 × 0.30 × 0.70 = 4.90 (legacy notional: 2.10)
        assert trade.shares == pytest.approx(100.0 / 0.30)
        assert trade.fee == pytest.approx(4.90, abs=0.005)

    def test_sell_charges_official_curve_on_shares(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        initialized_engine.buy("btc", "yes", 100.0)
        trade = initialized_engine.sell("btc", "yes", 50.0).trade

        # 50 shares × 0.07 × 0.64 × 0.36 = 0.8064
        assert trade.avg_price == pytest.approx(0.64)
        assert trade.fee == pytest.approx(50 * 0.07 * 0.64 * 0.36)

    def test_zero_rate_falls_back_to_bps(self, initialized_engine: Engine):
        """A fee-free category (rate 0) has no schedule to charge."""
        schedule = {"rate": 0.0, "exponent": 1, "takerOnly": True, "rebateRate": 0}
        _mock_api(initialized_engine, market=_schedule_market(**schedule))
        trade = initialized_engine.buy("btc", "yes", 100.0).trade

        assert trade.fee == 0.0
        initialized_engine.api.get_fee_rate.assert_called_once_with("tok_yes")

    def test_non_identity_exponent_falls_back_to_bps(
        self, initialized_engine: Engine,
    ):
        schedule = {"rate": 0.07, "exponent": 2, "takerOnly": True, "rebateRate": 0}
        _mock_api(
            initialized_engine, market=_schedule_market(**schedule), fee_rate=200,
        )
        trade = initialized_engine.buy("btc", "yes", 100.0).trade

        assert trade.fee_rate_bps == 200
        # Legacy model: 0.02 × min(0.66, 0.34) × $100
        assert trade.fee == pytest.approx(0.02 * 0.34 * trade.amount_usd)


class TestMakerFillFees:
    """Resting limit fills are maker fills."""

    @staticmethod
    def _place_buy(engine: Engine) -> None:
        from pm_trader.orders import create_order

        create_order(
            engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100.0,
            limit_price=0.70,  # At/above best ask (0.66), so it fills
        )

    def test_taker_only_market_pays_no_maker_fee(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        self._place_buy(initialized_engine)
        cash_before = initialized_engine.get_account().cash

        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["filled"]

        trade = initialized_engine.get_history(limit=1)[0]
        assert trade.fee == 0.0
        # Cash moves by the fill cost alone
        assert initialized_engine.get_account().cash == pytest.approx(
            cash_before - trade.amount_usd, abs=1e-9
        )

    def test_non_taker_only_market_charges_the_maker(
        self, initialized_engine: Engine,
    ):
        schedule = {"rate": 0.05, "exponent": 1, "takerOnly": False, "rebateRate": 0.25}
        _mock_api(initialized_engine, market=_schedule_market(**schedule))
        self._place_buy(initialized_engine)

        initialized_engine.check_orders()
        trade = initialized_engine.get_history(limit=1)[0]
        assert trade.fee == pytest.approx(
            trade.shares * 0.05 * trade.avg_price * (1 - trade.avg_price)
        )

    def test_taker_only_market_pays_no_maker_fee_on_sell(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        initialized_engine.buy("btc", "yes", 100.0)
        from pm_trader.orders import create_order

        create_order(
            initialized_engine.db.conn,
            market_slug="will-bitcoin-hit-100k",
            market_condition_id="0xabc123",
            outcome="yes",
            side="sell",
            amount=10.0,
            limit_price=0.50,  # Below best bid (0.64), so it fills
        )

        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["filled"]
        assert initialized_engine.get_history(limit=1)[0].fee == 0.0

    def test_marketable_limit_buy_pays_taker_fee_at_placement(
        self, initialized_engine: Engine,
    ):
        """A limit through the ask never rests: it fills NOW as a taker."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        cash_before = initialized_engine.get_account().cash

        placed = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 100.0, 0.70,  # best ask 0.66 < 0.70: marketable
        )
        assert placed["status"] == "filled"
        assert initialized_engine.get_pending_orders() == []

        trade = initialized_engine.get_history(limit=1)[0]
        assert trade.fee == pytest.approx(
            trade.shares * 0.07 * trade.avg_price * (1 - trade.avg_price)
        )
        assert trade.fee > 0.0
        assert initialized_engine.get_account().cash == pytest.approx(
            cash_before - trade.amount_usd - trade.fee, abs=1e-9
        )

    def test_marketable_limit_sell_pays_taker_fee_at_placement(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        initialized_engine.buy("btc", "yes", 100.0)

        placed = initialized_engine.place_limit_order(
            "btc", "yes", "sell", 10.0, 0.50,  # best bid 0.64 > 0.50: marketable
        )
        assert placed["status"] == "filled"

        trade = initialized_engine.get_history(limit=1)[0]
        assert trade.fee == pytest.approx(
            trade.shares * 0.07 * trade.avg_price * (1 - trade.avg_price)
        )

    def test_resting_limit_still_fills_maker_free(
        self, initialized_engine: Engine,
    ):
        """Placed BELOW the ask: rests; the market later moves down to the
        limit and lifts the resting order — a maker fill, no fee."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))

        placed = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 100.0, 0.60,  # best ask 0.66 > 0.60: rests
        )
        assert placed["status"] == "pending"

        # Sellers lower their asks INTO our resting bid (someone else crossed).
        from unittest.mock import MagicMock
        moved_book = _make_book(
            bids=[(0.59, 5000)],
            asks=[(0.60, 5000)],
        )
        initialized_engine.api.get_order_book = MagicMock(return_value=moved_book)

        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["filled"]
        trade = initialized_engine.get_history(limit=1)[0]
        assert trade.fee == 0.0

    def test_marketable_limit_with_no_depth_rests(
        self, initialized_engine: Engine,
    ):
        """Price crosses at placement but the fill comes back empty (defensive
        edge): rest the order instead of erroring."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        from pm_trader.orderbook import FillResult
        import pm_trader.engine as engine_mod
        empty_fill = FillResult(
            filled=False, is_partial=False, total_shares=0.0,
            total_cost=0.0, avg_price=0.0, fee=0.0,
            slippage_bps=0.0, levels_filled=0, fills=[],
        )

        orig_buy, orig_sell = engine_mod.simulate_buy_fill, engine_mod.simulate_sell_fill
        engine_mod.simulate_buy_fill = lambda *a, **kw: empty_fill
        engine_mod.simulate_sell_fill = lambda *a, **kw: empty_fill
        try:
            placed = initialized_engine.place_limit_order(
                "btc", "yes", "buy", 100.0, 0.70,  # crosses (ask 0.66 < 0.70)
            )
            assert placed["status"] == "pending"
            assert initialized_engine.get_history(limit=1) == []

            placed_sell = initialized_engine.place_limit_order(
                "btc", "yes", "sell", 1.0, 0.60,  # crosses (bid 0.64 > 0.60)
            )
            assert placed_sell["status"] == "pending"
        finally:
            engine_mod.simulate_buy_fill, engine_mod.simulate_sell_fill = orig_buy, orig_sell

    def test_transient_failure_at_placement_rejects_atomically(
        self, initialized_engine: Engine,
    ):
        """Book fetch failing mid-placement must NOT leave a pending order:
        a caller retry would double-place and the orphan would later fill as
        a fee-free maker (review finding, round 3)."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))

        def flaky_book(token_id):
            raise ApiError("Gamma API request failed: transient")
        initialized_engine.api.get_order_book = MagicMock(side_effect=flaky_book)

        with pytest.raises(ApiError):
            initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.70)
        assert initialized_engine.get_pending_orders() == []

        # Caller retries once the API recovers: exactly ONE order, filled as taker.
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        placed = initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.70)
        assert placed["status"] == "filled"
        trades = initialized_engine.get_history(limit=5)
        assert len(trades) == 1
        assert trades[0].fee == pytest.approx(
            trades[0].shares * 0.07 * trades[0].avg_price * (1 - trades[0].avg_price)
        )


# ---------------------------------------------------------------------------
# Cash reservation for open buy limit orders
# ---------------------------------------------------------------------------


class TestCashReservation:
    """A resting buy reserves amount (+ fee bound) against available cash."""

    def test_place_limit_buy_overcommit_rejected(self, initialized_engine: Engine):
        """A resting buy larger than the account cannot be placed at all."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        initialized_engine.db.update_cash(100.0)

        count_before = initialized_engine.db.conn.execute(
            "SELECT COUNT(*) AS n FROM limit_orders"
        ).fetchone()["n"]
        with pytest.raises(InsufficientBalanceError) as exc:
            initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.55)
        assert exc.value.required == pytest.approx(107.0)  # 100 + 0.07*100 bound
        assert exc.value.available == pytest.approx(100.0)

        # Rejection leaves no order behind
        assert initialized_engine.get_pending_orders() == []
        count_after = initialized_engine.db.conn.execute(
            "SELECT COUNT(*) AS n FROM limit_orders"
        ).fetchone()["n"]
        assert count_after == count_before

    def test_place_limit_buy_second_overcommit_rejected(
        self, initialized_engine: Engine,
    ):
        """A second buy competing for the same cash is rejected."""
        _mock_api(initialized_engine)  # zero-fee market: reserve == amount
        initialized_engine.db.update_cash(100.0)

        placed = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 50.0, 0.55,
        )
        assert placed["status"] == "pending"

        with pytest.raises(InsufficientBalanceError) as exc:
            initialized_engine.place_limit_order("btc", "yes", "buy", 60.0, 0.50)
        assert exc.value.available == pytest.approx(50.0)

        # The first order is untouched and still pending
        assert [o["id"] for o in initialized_engine.get_pending_orders()] == [
            placed["id"]
        ]

    def test_cancel_releases_reservation(self, initialized_engine: Engine):
        """Cancelling the resting buy frees its cash for a new placement."""
        _mock_api(initialized_engine)
        initialized_engine.db.update_cash(100.0)

        first = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 50.0, 0.55,
        )
        with pytest.raises(InsufficientBalanceError):
            initialized_engine.place_limit_order("btc", "yes", "buy", 60.0, 0.50)

        assert initialized_engine.cancel_limit_order(first["id"]) is not None
        released = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 60.0, 0.50,
        )
        assert released["status"] == "pending"

    def test_available_cash_clamps_at_zero(self, initialized_engine: Engine):
        """cash < reserved must not report a negative available cash."""
        _mock_api(initialized_engine)
        initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.55)
        assert initialized_engine._reserved_buy_notional() == pytest.approx(100.0)

        initialized_engine.db.update_cash(10.0)  # craft cash < reserved
        assert initialized_engine._available_cash() == 0.0
        assert initialized_engine._reserved_buy_notional() == pytest.approx(100.0)

    def test_tick_violation_precedence_over_balance(
        self, initialized_engine: Engine,
    ):
        """An off-grid price fails tick validation before the balance gate."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        initialized_engine.db.update_cash(100.0)

        with pytest.raises(TickSizeViolationError):
            initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.555)
        assert initialized_engine.get_pending_orders() == []

    def test_maker_exempt_market_still_reserves_taker_bound(
        self, initialized_engine: Engine,
    ):
        """In a takerOnly market a RESTING fill would pay no fee, but the
        placement cannot know whether the order will rest or cross — so the
        taker bound (amount x rate) is required either way (conservative)."""
        _mock_api(initialized_engine, market=_schedule_market(**CRYPTO_SCHEDULE))
        initialized_engine.db.update_cash(106.99)

        with pytest.raises(InsufficientBalanceError) as exc:
            initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.55)
        assert exc.value.required == pytest.approx(107.0)

    def test_market_buy_cannot_spend_reserved_cash(
        self, initialized_engine: Engine,
    ):
        """A market order is checked against available, not raw, cash."""
        deep_book = _make_book(
            bids=[(0.64, 100_000)], asks=[(0.66, 100_000)],
        )
        _mock_api(initialized_engine, book=deep_book)  # zero-fee market
        initialized_engine.place_limit_order("btc", "yes", "buy", 5000.0, 0.55)

        with pytest.raises(InsufficientBalanceError) as exc:
            initialized_engine.buy("btc", "yes", 9000.0)
        assert exc.value.available == pytest.approx(5000.0)


class TestEstimateBuyFee:
    """Unit tests for the placement-time fee upper bound."""

    def test_schedule_bound(self, initialized_engine: Engine):
        """With a usable feeSchedule the bound is amount * rate."""
        market = _schedule_market(**CRYPTO_SCHEDULE)
        assert initialized_engine._estimate_buy_fee(
            market, "tok_yes", 100.0,
        ) == pytest.approx(7.0)

    def test_legacy_worst_case(self, initialized_engine: Engine):
        """Legacy path: p=0.5 worst case of min(p, 1-p) on the USD notional."""
        _mock_api(initialized_engine, fee_rate=200)
        assert initialized_engine._estimate_buy_fee(
            SAMPLE_MARKET, "tok_yes", 100.0,
        ) == pytest.approx(1.0)  # 0.02 * 0.5 * 100

    def test_legacy_minimum_floor(self, initialized_engine: Engine):
        """Tiny legacy fees are floored at calculate_fee's 0.0001 minimum."""
        _mock_api(initialized_engine, fee_rate=1)
        assert initialized_engine._estimate_buy_fee(
            SAMPLE_MARKET, "tok_yes", 0.5,
        ) == pytest.approx(0.0001)

    def test_zero_bps_is_zero(self, initialized_engine: Engine):
        """Fee-free legacy markets bound to 0: the gate degenerates to notional."""
        _mock_api(initialized_engine, fee_rate=0)
        assert initialized_engine._estimate_buy_fee(
            SAMPLE_MARKET, "tok_yes", 100.0,
        ) == 0.0

    def test_fill_time_cash_guard_rejects_fee_slack_exhaustion(
        self, initialized_engine: Engine,
    ):
        """The fill-time raw-cash check in _execute_limit_buy stays the final
        guard: two resting buys whose worst-case fees together exceed the cash
        (the placement gate reserves remaining_amount only) fill one and
        permanently reject the other, instead of going negative."""
        schedule = {"rate": 0.07, "exponent": 1, "takerOnly": False}
        _mock_api(initialized_engine, market=_schedule_market(**schedule))
        initialized_engine.db.update_cash(213.0)

        first = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 100.0, 0.55,
        )
        second = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 100.0, 0.50,
        )

        # Deep-low ask (0.01): each fill costs 100 + 6.93 worst-case fee.
        deep_low_book = _make_book(
            bids=[(0.64, 100_000)], asks=[(0.01, 100_000)],
        )
        initialized_engine.api.get_order_book = MagicMock(return_value=deep_low_book)

        results = initialized_engine.check_orders()
        actions = {r["order"]["id"]: r["action"] for r in results}
        assert actions[first["id"]] == "filled"
        assert actions[second["id"]] == "rejected"
        rejected = next(r for r in results if r["order"]["id"] == second["id"])
        assert "Insufficient balance" in rejected["reason"]


# ---------------------------------------------------------------------------
# Balance surface: reserved / available cash
# ---------------------------------------------------------------------------


class TestBalanceReservedKeys:
    def test_balance_reports_reserved_and_available(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine)  # zero-fee market

        clean = initialized_engine.get_balance()
        assert clean["reserved_cash"] == 0.0
        assert clean["available_cash"] == pytest.approx(clean["cash"])

        initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.55)
        bal = initialized_engine.get_balance()
        assert bal["reserved_cash"] == pytest.approx(100.0)
        assert bal["available_cash"] == pytest.approx(bal["cash"] - 100.0)
        # Reserved cash is a subset of cash: total_value and pnl are unchanged
        assert bal["total_value"] == pytest.approx(
            bal["cash"] + bal["positions_value"]
        )
        assert bal["pnl"] == pytest.approx(
            bal["total_value"] - bal["starting_balance"]
        )

    def test_partial_fill_re_reserves_remainder(self, initialized_engine: Engine):
        """After a partial fill only the resting remainder stays reserved."""
        _mock_api(initialized_engine)
        initialized_engine.place_limit_order("btc", "yes", "buy", 100.0, 0.55)

        # Only $40 of ask depth within the limit (0.50 x 80 shares)
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 500)], asks=[(0.50, 80)])
        )
        results = initialized_engine.check_orders()
        assert [r["action"] for r in results] == ["partially_filled"]

        bal = initialized_engine.get_balance()
        assert bal["reserved_cash"] == pytest.approx(60.0)

    def test_marketable_full_fill_leaves_no_reservation(
        self, initialized_engine: Engine,
    ):
        _mock_api(initialized_engine)  # zero-fee market
        cash_before = initialized_engine.get_account().cash

        placed = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 100.0, 0.70,  # crosses (ask 0.66): fills now
        )
        assert placed["status"] == "filled"

        bal = initialized_engine.get_balance()
        assert bal["reserved_cash"] == 0.0
        assert bal["cash"] == pytest.approx(cash_before - 100.0)

    def test_marketable_partial_fill_reserves_remainder(
        self, initialized_engine: Engine,
    ):
        """A crossing buy that only partly fills reserves what still rests."""
        _mock_api(initialized_engine)
        # $33 of ask depth (0.66 x 50) under a 0.70 limit
        initialized_engine.api.get_order_book = MagicMock(
            return_value=_make_book(bids=[(0.64, 500)], asks=[(0.66, 50)])
        )

        placed = initialized_engine.place_limit_order(
            "btc", "yes", "buy", 100.0, 0.70,
        )
        assert placed["status"] == "partially_filled"

        bal = initialized_engine.get_balance()
        assert bal["reserved_cash"] == pytest.approx(67.0)
