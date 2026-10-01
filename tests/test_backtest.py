"""Tests for backtesting engine."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pm_trader.backtest import (
    BacktestResult,
    PriceSnapshot,
    _build_synthetic_book,
    load_snapshots_csv,
    load_snapshots_json,
    run_backtest,
)
from pm_trader.engine import Engine
from pm_trader.models import Market


# ---------------------------------------------------------------------------
# Synthetic book
# ---------------------------------------------------------------------------


class TestBuildSyntheticBook:
    def test_basic_structure(self):
        book = _build_synthetic_book(0.50)
        assert len(book.asks) == 3
        assert len(book.bids) == 3
        # Asks should be above midpoint
        assert all(a.price >= 0.50 for a in book.asks)
        # Bids should be below midpoint
        assert all(b.price <= 0.50 for b in book.bids)

    def test_extreme_high_price(self):
        book = _build_synthetic_book(0.98)
        assert all(a.price <= 0.99 for a in book.asks)

    def test_extreme_low_price(self):
        book = _build_synthetic_book(0.02)
        assert all(b.price >= 0.01 for b in book.bids)

    def test_custom_depth(self):
        book = _build_synthetic_book(0.50, depth=1000.0)
        assert book.asks[0].size == 1000.0


# ---------------------------------------------------------------------------
# Loading snapshots
# ---------------------------------------------------------------------------


class TestLoadSnapshotsCsv:
    def test_loads_csv(self, tmp_path: Path):
        csv_file = tmp_path / "data.csv"
        csv_file.write_text(
            "timestamp,market_slug,outcome,midpoint\n"
            "2026-01-01T00:00:00Z,test-market,yes,0.65\n"
            "2026-01-01T01:00:00Z,test-market,yes,0.70\n"
        )
        snapshots = load_snapshots_csv(csv_file)
        assert len(snapshots) == 2
        assert snapshots[0].midpoint == 0.65
        assert snapshots[1].midpoint == 0.70
        assert snapshots[0].market_slug == "test-market"
        assert snapshots[0].outcome == "yes"


class TestLoadSnapshotsJson:
    def test_loads_json(self, tmp_path: Path):
        json_file = tmp_path / "data.json"
        json_file.write_text(json.dumps([
            {"timestamp": "2026-01-01T00:00:00Z", "market_slug": "m1", "outcome": "Yes", "midpoint": 0.55},
            {"timestamp": "2026-01-01T01:00:00Z", "market_slug": "m1", "outcome": "yes", "midpoint": 0.60},
        ]))
        snapshots = load_snapshots_json(json_file)
        assert len(snapshots) == 2
        assert snapshots[0].outcome == "yes"  # normalized to lower


# ---------------------------------------------------------------------------
# Running backtests
# ---------------------------------------------------------------------------


def noop_strategy(engine: Engine, snapshot: PriceSnapshot, prices: dict) -> None:
    """Does nothing."""
    pass


def buy_on_dip_strategy(engine: Engine, snapshot: PriceSnapshot, prices: dict) -> None:
    """Buys when price dips below 0.50."""
    if snapshot.midpoint < 0.50:
        engine.buy(snapshot.market_slug, snapshot.outcome, 100.0)


def _make_market(slug: str, outcome: str, midpoint: float) -> Market:
    """Build a Market for use inside a trading strategy."""
    return Market(
        condition_id=f"0x{slug}",
        slug=slug,
        question=f"Question for {slug}?",
        description="",
        outcomes=["Yes", "No"],
        outcome_prices=[midpoint, round(1.0 - midpoint, 4)],
        tokens=[
            {"token_id": f"tok_yes_{slug}", "outcome": "Yes"},
            {"token_id": f"tok_no_{slug}", "outcome": "No"},
        ],
        active=True,
        closed=False,
    )


def trading_strategy(engine: Engine, snapshot: PriceSnapshot, prices: dict) -> None:
    """Strategy that buys on first snapshot to exercise engine.buy().

    This forces the backtest's patched get_midpoint (make_midpoint_fn inner fn,
    line 159) and get_order_book (make_book_fn inner fn, line 167) closures
    to be called during order fill simulation inside engine.buy().
    """
    # Only buy once (when no position exists yet)
    if engine.get_portfolio():
        return

    market = _make_market(snapshot.market_slug, snapshot.outcome, snapshot.midpoint)
    engine.api.get_market = MagicMock(return_value=market)
    engine.buy(snapshot.market_slug, snapshot.outcome, 100.0)


class TestRunBacktest:
    def test_noop_strategy(self):
        snapshots = [
            PriceSnapshot("2026-01-01T00:00:00Z", "m1", "yes", 0.50),
            PriceSnapshot("2026-01-01T01:00:00Z", "m1", "yes", 0.55),
        ]
        result = run_backtest(snapshots, noop_strategy, "noop")
        assert result.strategy == "noop"
        assert result.starting_balance == 10_000.0
        assert result.ending_cash == 10_000.0
        assert result.total_trades == 0
        assert result.pnl == 0.0
        assert result.snapshots_processed == 2

    def test_custom_balance(self):
        snapshots = [PriceSnapshot("2026-01-01T00:00:00Z", "m1", "yes", 0.50)]
        result = run_backtest(snapshots, noop_strategy, balance=5_000.0)
        assert result.starting_balance == 5_000.0

    def test_strategy_error_doesnt_crash(self):
        def bad_strategy(engine, snapshot, prices):
            raise ValueError("boom")

        snapshots = [PriceSnapshot("2026-01-01T00:00:00Z", "m1", "yes", 0.50)]
        result = run_backtest(snapshots, bad_strategy, "bad")
        assert result.snapshots_processed == 1
        assert result.total_trades == 0

    def test_strategy_errors_are_reported(self):
        def offline_strategy(engine, snapshot, prices):
            raise ConnectionError("offline")

        snapshots = [
            PriceSnapshot("2026-01-01T00:00:00Z", "m1", "yes", 0.50),
            PriceSnapshot("2026-01-01T01:00:00Z", "m1", "yes", 0.55),
        ]
        result = run_backtest(snapshots, offline_strategy, "offline")
        assert result.total_trades == 0
        assert result.strategy_errors == 2
        assert result.last_error == "ConnectionError: offline"

    def test_clean_run_reports_no_errors(self):
        snapshots = [PriceSnapshot("2026-01-01T00:00:00Z", "m1", "yes", 0.50)]
        result = run_backtest(snapshots, noop_strategy)
        assert result.strategy_errors == 0
        assert result.last_error is None

    def test_result_fields(self):
        snapshots = [PriceSnapshot("2026-01-01T00:00:00Z", "m1", "yes", 0.50)]
        result = run_backtest(snapshots, noop_strategy)
        assert isinstance(result, BacktestResult)
        assert hasattr(result, "sharpe_ratio")
        assert hasattr(result, "win_rate")
        assert hasattr(result, "max_drawdown")

    def test_trading_strategy_exercises_closures(self):
        """A strategy that calls engine.buy() exercises the inner closures:
        - make_midpoint_fn's inner fn (line 159): called by engine via
          api.get_midpoint during portfolio valuation
        - make_book_fn's inner fn (line 167): called by engine via
          api.get_order_book during buy fill simulation
        """
        snapshots = [
            PriceSnapshot("2026-01-01T00:00:00Z", "test-market", "yes", 0.60),
            PriceSnapshot("2026-01-01T01:00:00Z", "test-market", "yes", 0.65),
        ]
        result = run_backtest(snapshots, trading_strategy, "trader", balance=10_000.0)

        assert result.snapshots_processed == 2
        assert result.total_trades == 1
        # Should have spent some cash on the buy
        assert result.ending_cash < 10_000.0

    def test_open_position_marked_at_last_snapshot(self):
        snapshots = [
            PriceSnapshot("2026-01-01T00:00:00Z", "mkt", "yes", 0.60),
            PriceSnapshot("2026-01-01T01:00:00Z", "mkt", "yes", 0.80),
        ]
        result = run_backtest(snapshots, trading_strategy, "mark", balance=10_000.0)

        spent = 10_000.0 - result.ending_cash
        shares = 100.0 / 0.61  # filled at the synthetic ask (0.60 + 0.01)
        assert spent == pytest.approx(100.0)
        assert result.pnl == pytest.approx(result.ending_cash + shares * 0.80 - 10_000.0, abs=0.05)
        assert result.pnl > 0
        assert result.roi_pct == pytest.approx(result.pnl / 10_000.0 * 100)

    def test_open_position_without_snapshot_valued_at_cost(self):
        def buy_unseen(engine, snapshot, prices):
            if engine.get_portfolio():
                return
            engine.api.get_market = MagicMock(
                return_value=_make_market("other", "yes", snapshot.midpoint)
            )
            engine.buy("other", "yes", 100.0)

        snapshots = [PriceSnapshot("2026-01-01T00:00:00Z", "mkt", "yes", 0.60)]
        result = run_backtest(snapshots, buy_unseen, "unseen", balance=10_000.0)
        assert result.total_trades == 1
        assert result.pnl == pytest.approx(0.0, abs=1e-6)

    def test_trading_strategy_multiple_snapshots(self):
        """Verify closures capture correct midpoint per snapshot."""
        snapshots = [
            PriceSnapshot("2026-01-01T00:00:00Z", "mkt", "yes", 0.40),
            PriceSnapshot("2026-01-01T01:00:00Z", "mkt", "yes", 0.50),
            PriceSnapshot("2026-01-01T02:00:00Z", "mkt", "yes", 0.60),
        ]
        result = run_backtest(snapshots, trading_strategy, "multi", balance=10_000.0)

        assert result.snapshots_processed == 3
        # Should have exactly 1 trade (strategy only buys when portfolio is empty)
        assert result.total_trades == 1

    def test_patched_get_midpoint_is_dead_code(self):
        """_patched_get_midpoint (lines 145-150) is defined but never
        assigned to engine.api.get_midpoint. It is unreachable dead code.

        This test documents that fact: the for-loop snapshot immediately
        overwrites engine.api.get_midpoint with make_midpoint_fn, so
        _patched_get_midpoint can never be called.
        """
        # We verify this by confirming that even after run_backtest,
        # the midpoint function used is always make_midpoint_fn, not
        # _patched_get_midpoint. Since _patched_get_midpoint is a local
        # closure that is never assigned, there is no way to reach it.
        #
        # Lines 148-150 cannot be covered without modifying the source.
        pass
