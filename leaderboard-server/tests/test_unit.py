"""Unit tests: pure functions with no HTTP. @pytest.mark.unit throughout."""
from __future__ import annotations

import pytest

from server.adapters.polymarket import (
    Market,
    OrderBook,
    OrderBookLevel,
    InvalidOutcomeError,
    validate_outcome,
    simulate_buy_fill,
    simulate_sell_fill,
    calculate_fee,
    Trade,
    Account,
    compute_stats,
)
from server.routes.leaderboard import _compute_tier, _dict_to_trade, _dict_to_account
from server.routes.trading import _update_position_after_buy, _update_position_after_sell
from server.db import DB

from tests.conftest import SAMPLE_MARKET, SAMPLE_BOOK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    _db = DB(":memory:")
    _db.init_schema()
    yield _db
    _db.close()


def _make_account(db):
    user = db.create_user("unit-bot")
    return db.create_account(user["id"], "default")


# ---------------------------------------------------------------------------
# _compute_tier
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestComputeTier:
    def test_bronze_default(self):
        assert _compute_tier(trade_count=0, roi_pct=0.0, sharpe=0.0) == "bronze"

    def test_bronze_few_trades(self):
        assert _compute_tier(trade_count=5, roi_pct=50.0, sharpe=2.0) == "bronze"

    def test_silver_boundary(self):
        # Exactly at threshold: 20 trades, roi > 5
        assert _compute_tier(trade_count=20, roi_pct=5.01, sharpe=0.5) == "silver"

    def test_silver_at_boundary_not_above(self):
        # roi_pct exactly 5 → NOT silver (condition is > 5)
        assert _compute_tier(trade_count=20, roi_pct=5.0, sharpe=0.5) == "bronze"

    def test_gold_boundary(self):
        assert _compute_tier(trade_count=30, roi_pct=10.01, sharpe=1.01) == "gold"

    def test_gold_not_enough_sharpe(self):
        # sharpe exactly 1.0 → NOT gold (condition is > 1.0)
        assert _compute_tier(trade_count=30, roi_pct=15.0, sharpe=1.0) == "silver"

    def test_diamond_boundary(self):
        assert _compute_tier(trade_count=50, roi_pct=20.01, sharpe=1.51) == "diamond"

    def test_diamond_not_enough_trades(self):
        assert _compute_tier(trade_count=49, roi_pct=25.0, sharpe=2.0) == "gold"

    def test_unranked_negative_roi(self):
        assert _compute_tier(trade_count=100, roi_pct=-5.0, sharpe=-0.5) == "bronze"


# ---------------------------------------------------------------------------
# validate_outcome
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestValidateOutcome:
    def test_yes_lowercase(self):
        assert validate_outcome("yes", SAMPLE_MARKET) == "yes"

    def test_no_lowercase(self):
        assert validate_outcome("no", SAMPLE_MARKET) == "no"

    def test_yes_uppercase(self):
        assert validate_outcome("Yes", SAMPLE_MARKET) == "yes"

    def test_no_mixed_case(self):
        assert validate_outcome("NO", SAMPLE_MARKET) == "no"

    def test_yes_with_whitespace(self):
        assert validate_outcome("  yes  ", SAMPLE_MARKET) == "yes"

    def test_invalid_outcome(self):
        with pytest.raises(InvalidOutcomeError):
            validate_outcome("maybe", SAMPLE_MARKET)

    def test_empty_string(self):
        with pytest.raises(InvalidOutcomeError):
            validate_outcome("", SAMPLE_MARKET)


# ---------------------------------------------------------------------------
# Fee calculation
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFeeCalculation:
    def test_zero_fee_rate(self):
        fee = calculate_fee(0, 0.50, 100)
        assert fee == 0.0

    def test_200bps_at_midpoint(self):
        # fee = (200/10000) * min(0.50, 0.50) * 100 = 0.02 * 0.50 * 100 = 1.0
        fee = calculate_fee(200, 0.50, 100)
        assert fee == pytest.approx(1.0)

    def test_boundary_price_low(self):
        # fee = (200/10000) * min(0.01, 0.99) * 100 = 0.02 * 0.01 * 100 = 0.02
        fee = calculate_fee(200, 0.01, 100)
        assert fee == pytest.approx(0.02)

    def test_boundary_price_high(self):
        # fee = (200/10000) * min(0.99, 0.01) * 100 = 0.02 * 0.01 * 100 = 0.02
        fee = calculate_fee(200, 0.99, 100)
        assert fee == pytest.approx(0.02)

    def test_symmetric_fee(self):
        # Fee at 0.30 should equal fee at 0.70
        fee_low = calculate_fee(200, 0.30, 100)
        fee_high = calculate_fee(200, 0.70, 100)
        assert fee_low == pytest.approx(fee_high)


# ---------------------------------------------------------------------------
# _update_position_after_buy
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdatePositionAfterBuy:
    def test_new_position(self, db):
        account = _make_account(db)
        pos = _update_position_after_buy(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            new_shares=100,
            cost=66.0,
            avg_fill_price=0.66,
        )
        assert pos["shares"] == 100
        assert pos["avg_entry_price"] == pytest.approx(0.66)
        assert pos["total_cost"] == pytest.approx(66.0)

    def test_cost_averaging(self, db):
        account = _make_account(db)
        # First buy: 100 shares at 0.50
        _update_position_after_buy(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            new_shares=100,
            cost=50.0,
            avg_fill_price=0.50,
        )
        # Second buy: 100 shares at 0.70
        pos = _update_position_after_buy(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            new_shares=100,
            cost=70.0,
            avg_fill_price=0.70,
        )
        assert pos["shares"] == 200
        assert pos["total_cost"] == pytest.approx(120.0)
        assert pos["avg_entry_price"] == pytest.approx(0.60)  # 120/200

    def test_different_price_averaging(self, db):
        account = _make_account(db)
        # Buy 50 shares at 0.40
        _update_position_after_buy(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            new_shares=50,
            cost=20.0,
            avg_fill_price=0.40,
        )
        # Buy 150 shares at 0.80
        pos = _update_position_after_buy(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            new_shares=150,
            cost=120.0,
            avg_fill_price=0.80,
        )
        assert pos["shares"] == 200
        assert pos["avg_entry_price"] == pytest.approx(0.70)  # 140/200


# ---------------------------------------------------------------------------
# _update_position_after_sell
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdatePositionAfterSell:
    def _setup_position(self, db, shares=100, avg_price=0.50, total_cost=50.0):
        account = _make_account(db)
        db.upsert_position(
            account_id=account["id"],
            market_condition_id=SAMPLE_MARKET.condition_id,
            market_slug=SAMPLE_MARKET.slug,
            market_question=SAMPLE_MARKET.question,
            outcome="yes",
            shares=shares,
            avg_entry_price=avg_price,
            total_cost=total_cost,
            realized_pnl=0.0,
        )
        return account

    def test_sell_with_profit(self, db):
        account = self._setup_position(db)
        pos = _update_position_after_sell(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            sold_shares=100,
            proceeds=70.0,  # Sold at 0.70, bought at 0.50 → $20 profit
        )
        assert pos["shares"] == 0
        assert pos["realized_pnl"] == pytest.approx(20.0)  # 70 - 50

    def test_sell_with_loss(self, db):
        account = self._setup_position(db)
        pos = _update_position_after_sell(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            sold_shares=100,
            proceeds=30.0,  # Sold at 0.30, bought at 0.50 → -$20 loss
        )
        assert pos["shares"] == 0
        assert pos["realized_pnl"] == pytest.approx(-20.0)

    def test_sell_break_even(self, db):
        account = self._setup_position(db)
        pos = _update_position_after_sell(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            sold_shares=100,
            proceeds=50.0,
        )
        assert pos["realized_pnl"] == pytest.approx(0.0)

    def test_partial_sell(self, db):
        account = self._setup_position(db, shares=100, avg_price=0.50, total_cost=50.0)
        pos = _update_position_after_sell(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            sold_shares=50,
            proceeds=35.0,  # Sold 50 at 0.70 → profit = 35 - 25 = 10
        )
        assert pos["shares"] == 50
        assert pos["realized_pnl"] == pytest.approx(10.0)
        assert pos["total_cost"] == pytest.approx(25.0)  # half the original cost

    def test_sell_no_existing_position(self, db):
        account = _make_account(db)
        result = _update_position_after_sell(
            db,
            account_id=account["id"],
            market=SAMPLE_MARKET,
            outcome="yes",
            sold_shares=100,
            proceeds=50.0,
        )
        assert result is None


# ---------------------------------------------------------------------------
# _dict_to_trade / _dict_to_account converters
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDictConverters:
    def test_dict_to_trade(self):
        d = {
            "id": 1,
            "market_condition_id": "0xabc",
            "market_slug": "test",
            "market_question": "T?",
            "outcome": "yes",
            "side": "buy",
            "order_type": "fok",
            "avg_price": "0.50",
            "amount_usd": "100.00",
            "shares": "200.00",
            "fee_rate_bps": 200,
            "fee": "1.00",
            "slippage": "5.00",
            "levels_filled": 2,
            "is_partial": 0,
            "created_at": "2026-01-01T00:00:00Z",
        }
        trade = _dict_to_trade(d)
        assert isinstance(trade, Trade)
        assert trade.avg_price == 0.50
        assert trade.shares == 200.0
        assert trade.is_partial is False

    def test_dict_to_account(self):
        d = {
            "id": 1,
            "starting_balance": "10000.00",
            "cash": "9500.00",
            "created_at": "2026-01-01T00:00:00Z",
        }
        account = _dict_to_account(d)
        assert isinstance(account, Account)
        assert account.starting_balance == 10000.0
        assert account.cash == 9500.0

    def test_dict_to_trade_string_numbers(self):
        """SQLite sometimes returns strings for numeric columns."""
        d = {
            "id": 42,
            "market_condition_id": "0x123",
            "market_slug": "test-market",
            "market_question": "Q?",
            "outcome": "no",
            "side": "sell",
            "order_type": "fak",
            "avg_price": 0.65,
            "amount_usd": 650.0,
            "shares": 1000.0,
            "fee_rate_bps": 0,
            "fee": 0.0,
            "slippage": 0.0,
            "levels_filled": 1,
            "is_partial": 1,
            "created_at": "2026-06-15T12:00:00Z",
        }
        trade = _dict_to_trade(d)
        assert trade.is_partial is True
        assert trade.side == "sell"
