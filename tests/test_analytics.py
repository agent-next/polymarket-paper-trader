"""Tests for performance analytics."""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from pm_trader.analytics import (
    _daily_equity_curve,
    _daily_pnl,
    _parse_trade_datetime,
    _sort_trades_chronological,
    compute_stats,
    max_drawdown,
    sharpe_ratio,
    win_rate,
)
from pm_trader.models import Account, Trade


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _trade(
    *,
    id: int = 1,
    side: str = "buy",
    outcome: str = "yes",
    avg_price: float = 0.60,
    amount_usd: float = 60.0,
    shares: float = 100.0,
    fee: float = 0.0,
    market_condition_id: str = "0xabc",
    market_slug: str = "test-market",
    created_at: str = "2026-01-15 12:00:00",
) -> Trade:
    return Trade(
        id=id,
        market_condition_id=market_condition_id,
        market_slug=market_slug,
        market_question="Test?",
        outcome=outcome,
        side=side,
        order_type="fok",
        avg_price=avg_price,
        amount_usd=amount_usd,
        shares=shares,
        fee_rate_bps=0,
        fee=fee,
        slippage=0.0,
        levels_filled=1,
        is_partial=False,
        created_at=created_at,
    )


def _account(cash: float = 9_000.0, starting: float = 10_000.0) -> Account:
    return Account(id=1, starting_balance=starting, cash=cash, created_at="2026-01-01")


# ---------------------------------------------------------------------------
# win_rate tests
# ---------------------------------------------------------------------------


class TestWinRate:
    def test_no_trades(self):
        assert win_rate([]) == 0.0

    def test_no_sells(self):
        trades = [_trade(side="buy")]
        assert win_rate(trades) == 0.0

    def test_sell_without_prior_buy(self):
        """Sell with no matching buy falls back to sell's own avg_price (always tie → 0%)."""
        trades = [_trade(id=1, side="sell", avg_price=0.50, amount_usd=50.0, shares=100.0)]
        assert win_rate(trades) == 0.0

    def test_all_wins(self):
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0,
                   created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", avg_price=0.70, amount_usd=70.0, shares=100.0,
                   created_at="2026-01-02 10:00:00"),
        ]
        assert win_rate(trades) == 1.0

    def test_all_losses(self):
        trades = [
            _trade(id=1, side="buy", avg_price=0.70),
            _trade(id=2, side="sell", avg_price=0.50),
        ]
        assert win_rate(trades) == 0.0

    def test_mixed(self):
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0, market_condition_id="0x1"),
            _trade(id=2, side="sell", avg_price=0.70, amount_usd=70.0, shares=100.0, market_condition_id="0x1"),  # win
            _trade(id=3, side="buy", avg_price=0.60, amount_usd=60.0, shares=100.0, market_condition_id="0x2"),
            _trade(id=4, side="sell", avg_price=0.40, amount_usd=40.0, shares=100.0, market_condition_id="0x2"),  # loss
        ]
        assert win_rate(trades) == 0.5

    def test_fifo_partial_sell_uses_oldest_lot(self):
        """Partial sell consumes the oldest lot first, not a weighted average."""
        # Buy 100@0.40 ($40) + Buy 50@0.60 ($30); Sell 75@0.50 ($37.50)
        # FIFO entry cost = 75 × 0.40 = $30 < $37.50 → WIN
        trades = [
            _trade(id=1, side="buy", avg_price=0.40, amount_usd=40.0, shares=100.0),
            _trade(id=2, side="buy", avg_price=0.60, amount_usd=30.0, shares=50.0),
            _trade(id=3, side="sell", avg_price=0.50, amount_usd=37.5, shares=75.0),
        ]
        assert win_rate(trades) == 1.0

    def test_fifo_loss(self):
        """FIFO entry cost correctly identifies a non-winning sell."""
        # Buy 100@0.60 ($60) + Buy 100@0.70 ($70); Sell 100@0.60 ($60)
        # FIFO entry cost = 100 × 0.60 = $60 → tie → not a win
        trades = [
            _trade(id=1, side="buy", avg_price=0.60, amount_usd=60.0, shares=100.0),
            _trade(id=2, side="buy", avg_price=0.70, amount_usd=70.0, shares=100.0),
            _trade(id=3, side="sell", avg_price=0.60, amount_usd=60.0, shares=100.0),
        ]
        assert win_rate(trades) == 0.0

    def test_fifo_lot_basis_for_partial_sell(self):
        """FIFO (not weighted-average) decides the sell: weighted avg would tie."""
        # Buy 100@0.20 ($20) + Buy 100@0.80 ($80); Sell 100@0.50 ($50)
        # FIFO cost = $20 → WIN. Weighted avg = 0.50 → tie (would give 0.0).
        trades = [
            _trade(id=1, side="buy", avg_price=0.20, amount_usd=20.0, shares=100.0,
                   created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="buy", avg_price=0.80, amount_usd=80.0, shares=100.0,
                   created_at="2026-01-02 10:00:00"),
            _trade(id=3, side="sell", avg_price=0.50, amount_usd=50.0, shares=100.0,
                   created_at="2026-01-03 10:00:00"),
        ]
        assert win_rate(trades) == 1.0

    def test_fifo_result_independent_of_input_order(self):
        """Same history passed newest-first (DB row order) yields the same result."""
        chronological = [
            _trade(id=1, side="buy", avg_price=0.20, amount_usd=20.0, shares=100.0,
                   created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="buy", avg_price=0.80, amount_usd=80.0, shares=100.0,
                   created_at="2026-01-02 10:00:00"),
            _trade(id=3, side="sell", avg_price=0.50, amount_usd=50.0, shares=100.0,
                   created_at="2026-01-03 10:00:00"),
        ]
        assert win_rate(list(reversed(chronological))) == 1.0

    def test_fee_inclusive_entry_basis(self):
        """Buy fees raise cost_per_share; a nominal price uptick can still lose."""
        # Buy 100@0.50 ($50) fee 5.0 → cost/share 0.55; Sell 100@0.54 ($54) → LOSS
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0, fee=5.0),
            _trade(id=2, side="sell", avg_price=0.54, amount_usd=54.0, shares=100.0),
        ]
        assert win_rate(trades) == 0.0

    def test_ignores_zero_share_buy_and_unknown_side(self):
        trades = [
            _trade(id=1, side="buy", shares=0.0, amount_usd=0.0),
            _trade(id=2, side="hold", amount_usd=0.0),  # unknown side should be ignored
            _trade(id=3, side="sell", avg_price=0.5, amount_usd=50.0, shares=100.0),
        ]
        assert win_rate(trades) == 0.0

    def test_mixed_naive_and_aware_timestamps(self):
        """Naive DB timestamps and Z-suffixed aware ones sort together, no crash."""
        # Aware buy on day 1, naive sell on day 2 — passed newest-first.
        trades = [
            _trade(id=2, side="sell", avg_price=0.70, amount_usd=70.0, shares=100.0,
                   created_at="2026-01-02 10:00:00"),
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0,
                   created_at="2026-01-01T10:00:00Z"),
        ]
        assert [t.id for t in _sort_trades_chronological(trades)] == [1, 2]
        assert win_rate(trades) == 1.0


# ---------------------------------------------------------------------------
# sharpe_ratio tests
# ---------------------------------------------------------------------------


class TestSharpeRatio:
    def test_no_trades(self):
        assert sharpe_ratio([], 10_000) == 0.0

    def test_single_trade(self):
        # Need at least 2 days of returns
        trades = [_trade(side="sell", amount_usd=100, fee=0)]
        assert sharpe_ratio(trades, 10_000) == 0.0

    def test_consistent_positive_returns(self):
        # Two days of positive equity gains → positive Sharpe
        trades = [
            _trade(id=1, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        result = sharpe_ratio(trades, 10_000)
        assert result > 0

    def test_zero_cumulative(self):
        """When equity goes non-positive, that day's return is recorded as 0.0."""
        # Day 1: buy $20,000 on credit → equity goes negative. Day 2: small sell.
        trades = [
            _trade(id=1, side="buy", amount_usd=20_000, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=50, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        result = sharpe_ratio(trades, 10_000)
        # Should not crash and return some finite value
        assert math.isfinite(result)

    def test_zero_std_returns_zero(self):
        """When all daily returns are exactly zero, std=0 → sharpe=0."""
        # Each day: buy $100 + sell $100 at same mark → flat equity per day
        trades = [
            _trade(id=1, side="buy", amount_usd=100, fee=0, created_at="2026-01-01 08:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 12:00:00"),
            _trade(id=3, side="buy", amount_usd=100, fee=0, created_at="2026-01-02 08:00:00"),
            _trade(id=4, side="sell", amount_usd=100, fee=0, created_at="2026-01-02 12:00:00"),
        ]
        result = sharpe_ratio(trades, 10_000)
        assert result == 0.0

    def test_volatile_returns(self):
        # Big win then big loss → lower Sharpe than consistent
        consistent = [
            _trade(id=1, side="sell", amount_usd=50, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=50, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        volatile = [
            _trade(id=1, side="sell", amount_usd=200, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="buy", amount_usd=100, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        assert sharpe_ratio(consistent, 10_000) > sharpe_ratio(volatile, 10_000)

    def test_zero_trade_days_are_included_in_returns_series(self):
        contiguous = [
            _trade(id=1, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-02 10:00:00"),
        ]
        with_gap = [
            _trade(id=1, side="sell", amount_usd=100, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=100, fee=0, created_at="2026-01-03 10:00:00"),
        ]
        assert sharpe_ratio(with_gap, 10_000) < sharpe_ratio(contiguous, 10_000)

    def test_mark_to_market_reprices_open_position(self):
        """A later trade's price re-marks open shares; a buy alone is equity-neutral."""
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0, fee=0,
                   created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="buy", avg_price=0.90, amount_usd=0.90, shares=1.0, fee=0,
                   created_at="2026-01-02 10:00:00"),
        ]
        # Day 1: cash 9950 + 100×0.50 = 10000 (buy is equity-neutral)
        # Day 2: cash 9949.10 + 101×0.90 = 10040 (mark reprices all open shares)
        curve = _daily_equity_curve(trades, 10_000)
        assert curve == pytest.approx([10_000, 10_000, 10_040])
        # MtM shows a gain on day 2; a cash-only curve would show a loss.
        assert sharpe_ratio(trades, 10_000) > 0


# ---------------------------------------------------------------------------
# max_drawdown tests
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    def test_no_trades(self):
        assert max_drawdown([], 10_000) == 0.0

    def test_only_wins(self):
        trades = [
            _trade(id=1, side="sell", amount_usd=100, fee=0),
        ]
        assert max_drawdown(trades, 10_000) == 0.0

    def test_single_buy_is_equity_neutral(self):
        """Mark-to-market: a buy swaps cash for shares at mark — no drawdown."""
        trades = [
            _trade(id=1, side="buy", avg_price=0.60, amount_usd=60.0, shares=100.0, fee=0),
        ]
        # Cash-only accounting would report (10000-9940)/10000 = 0.006.
        assert max_drawdown(trades, 10_000) == 0.0

    def test_single_loss(self):
        # Buy 100@0.60 ($60) day 1 → equity 10000; sell 100@0.30 ($30) day 2 → 9970
        trades = [
            _trade(id=1, side="buy", avg_price=0.60, amount_usd=60.0, shares=100.0, fee=0,
                   created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", avg_price=0.30, amount_usd=30.0, shares=100.0, fee=0,
                   created_at="2026-01-02 10:00:00"),
        ]
        dd = max_drawdown(trades, 10_000)
        assert dd == pytest.approx(30 / 10_000, abs=1e-6)

    def test_recovery_still_records_peak_dd(self):
        # Day 1: buy 100@0.50 ($50) → equity 10000
        # Day 2: sell 100@0.20 ($20) → equity 9970 (dd = 0.003)
        # Day 3: sell 100@0.90 ($90) in another market → equity 10060 (new peak)
        trades = [
            _trade(id=1, side="buy", avg_price=0.50, amount_usd=50.0, shares=100.0, fee=0,
                   market_condition_id="0x1", created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", avg_price=0.20, amount_usd=20.0, shares=100.0, fee=0,
                   market_condition_id="0x1", created_at="2026-01-02 10:00:00"),
            _trade(id=3, side="sell", avg_price=0.90, amount_usd=90.0, shares=100.0, fee=0,
                   market_condition_id="0x2", created_at="2026-01-03 10:00:00"),
        ]
        dd = max_drawdown(trades, 10_000)
        assert dd == pytest.approx(30 / 10_000, abs=1e-6)

    def test_multiple_drawdowns(self):
        # Day 1: buy $1000 → equity 9000 + 100×0.60 = 9060 (dd = 0.094)
        # Day 2: sell $2000 → equity 11000 (new peak)
        # Day 3: buy $3000 → equity 8000 + 100×0.60 = 8060 (dd = 2940/11000)
        trades = [
            _trade(id=1, side="buy", amount_usd=1_000, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=2_000, fee=0, created_at="2026-01-02 10:00:00"),
            _trade(id=3, side="buy", amount_usd=3_000, fee=0, created_at="2026-01-03 10:00:00"),
        ]
        dd = max_drawdown(trades, 10_000)
        assert dd == pytest.approx(2_940 / 11_000, abs=1e-6)

    def test_gap_days_carry_equity_forward(self):
        # Day 1: sell $100 (different market) → equity 10100 (peak)
        # Day 2: no trades → carried at 10100
        # Day 3: buy 100@0.60 ($60) → equity 10100 (neutral)
        # Day 4: buy 1@0.30 ($0.30) → mark drops to 0.30, equity 10039.70 + 30.30 = 10070
        trades = [
            _trade(id=1, side="sell", amount_usd=100.0, fee=0, market_condition_id="0x1",
                   created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="buy", avg_price=0.60, amount_usd=60.0, shares=100.0, fee=0,
                   market_condition_id="0x2", created_at="2026-01-03 10:00:00"),
            _trade(id=3, side="buy", avg_price=0.30, amount_usd=0.30, shares=1.0, fee=0,
                   market_condition_id="0x2", created_at="2026-01-04 10:00:00"),
        ]
        dd = max_drawdown(trades, 10_000)
        assert dd == pytest.approx(30 / 10_100, abs=1e-6)


# ---------------------------------------------------------------------------
# compute_stats tests
# ---------------------------------------------------------------------------


class TestComputeStats:
    def test_empty_account(self):
        stats = compute_stats([], _account(cash=10_000), 0.0)
        assert stats["total_trades"] == 0
        assert stats["pnl"] == 0.0
        assert stats["roi_pct"] == 0.0
        assert stats["win_rate"] == 0.0
        assert stats["sharpe_ratio"] == 0.0
        assert stats["max_drawdown"] == 0.0

    def test_with_trades(self):
        trades = [
            _trade(id=2, side="sell", amount_usd=80, fee=1.0, avg_price=0.70,
                   created_at="2026-01-02 10:00:00"),
            _trade(id=1, side="buy", amount_usd=60, fee=0.5, avg_price=0.50,
                   created_at="2026-01-01 10:00:00"),
        ]
        stats = compute_stats(trades, _account(cash=9_018.5), positions_value=0.0)
        assert stats["total_trades"] == 2
        assert stats["buy_count"] == 1
        assert stats["sell_count"] == 1
        assert stats["total_fees"] == pytest.approx(1.5)
        assert stats["pnl"] == pytest.approx(-981.5)

    def test_roi_calculation(self):
        stats = compute_stats([], _account(cash=11_000, starting=10_000), 0.0)
        assert stats["roi_pct"] == pytest.approx(10.0)

    def test_positions_value_included(self):
        stats = compute_stats([], _account(cash=8_000), positions_value=3_000)
        assert stats["total_value"] == pytest.approx(11_000)
        assert stats["pnl"] == pytest.approx(1_000)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestAnalyticsInternals:
    def test_daily_pnl_empty_input(self):
        assert _daily_pnl([]) == []

    def test_daily_pnl_returns_empty_when_no_cashflow_sides(self):
        trades = [_trade(id=1, side="hold", amount_usd=10.0, created_at="2026-01-01 10:00:00")]
        assert _daily_pnl(trades) == []

    def test_daily_pnl_zero_fills_missing_days(self):
        trades = [
            _trade(id=1, side="buy", amount_usd=100.0, fee=0, created_at="2026-01-01 10:00:00"),
            _trade(id=2, side="sell", amount_usd=50.0, fee=0, created_at="2026-01-03 10:00:00"),
        ]
        assert _daily_pnl(trades) == [-100.0, 0.0, 50.0]

    def test_daily_equity_curve_empty(self):
        assert _daily_equity_curve([], 10_000) == [10_000]

    def test_parse_trade_datetime_handles_blank(self):
        assert _parse_trade_datetime("").year == 1

    def test_parse_trade_datetime_handles_z_suffix(self):
        dt = _parse_trade_datetime("2026-01-01T00:00:00Z")
        assert dt.year == 2026
        assert dt.tzinfo is None  # normalized to naive UTC

    def test_parse_trade_datetime_normalizes_offset_to_naive_utc(self):
        dt = _parse_trade_datetime("2026-01-02T02:30:00+05:00")
        assert dt == datetime(2026, 1, 1, 21, 30, 0)
        assert dt.tzinfo is None

    def test_parse_trade_datetime_invalid_with_space_fallback(self):
        assert _parse_trade_datetime("bad date").year == 1

    def test_parse_trade_datetime_strptime_fallback(self):
        # Non-padded month/day fail fromisoformat but match strptime's %m/%d.
        dt = _parse_trade_datetime("2026-1-1 10:00:00")
        assert (dt.year, dt.month, dt.day) == (2026, 1, 1)
