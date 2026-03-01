"""Tests for the trade execution engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pm_sim.db import Database
from pm_sim.engine import Engine
from pm_sim.models import (
    InsufficientBalanceError,
    InvalidOutcomeError,
    Market,
    MarketClosedError,
    NoPositionError,
    NotInitializedError,
    OrderBook,
    OrderBookLevel,
    OrderRejectedError,
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
        with pytest.raises(InvalidOutcomeError):
            initialized_engine.buy("btc", "maybe", 100.0)

    def test_buy_below_minimum(self, initialized_engine: Engine):
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
