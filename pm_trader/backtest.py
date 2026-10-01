"""Backtesting engine for pm-trader.

Replays historical price data through trading strategies using synthetic
order books, then computes performance metrics.

Historical data format (CSV or JSON):
    timestamp, market_slug, outcome, midpoint
    2026-01-01T00:00:00Z, will-x-happen, yes, 0.65
    2026-01-01T01:00:00Z, will-x-happen, yes, 0.68
    ...
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pm_trader.analytics import compute_stats
from pm_trader.engine import Engine
from pm_trader.models import OrderBook, OrderBookLevel


@dataclass
class PriceSnapshot:
    """A single historical price observation."""

    timestamp: str
    market_slug: str
    outcome: str
    midpoint: float


@dataclass
class BacktestResult:
    """Result of running a backtest."""

    strategy: str
    starting_balance: float
    ending_cash: float
    total_trades: int
    pnl: float
    roi_pct: float
    sharpe_ratio: float
    win_rate: float
    max_drawdown: float
    snapshots_processed: int
    strategy_errors: int = 0
    last_error: str | None = None


def load_snapshots_csv(path: Path) -> list[PriceSnapshot]:
    """Load historical price data from CSV.

    Expected columns: timestamp, market_slug, outcome, midpoint
    """
    snapshots = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            snapshots.append(PriceSnapshot(
                timestamp=row["timestamp"].strip(),
                market_slug=row["market_slug"].strip(),
                outcome=row["outcome"].strip().lower(),
                midpoint=float(row["midpoint"]),
            ))
    return snapshots


def load_snapshots_json(path: Path) -> list[PriceSnapshot]:
    """Load historical price data from JSON.

    Expected format: [{"timestamp": "...", "market_slug": "...",
                       "outcome": "...", "midpoint": 0.65}, ...]
    """
    with open(path) as f:
        data = json.load(f)
    return [
        PriceSnapshot(
            timestamp=d["timestamp"],
            market_slug=d["market_slug"],
            outcome=d["outcome"].lower(),
            midpoint=float(d["midpoint"]),
        )
        for d in data
    ]


def _build_synthetic_book(midpoint: float, spread: float = 0.02, depth: float = 500.0) -> OrderBook:
    """Build a synthetic order book around a midpoint price.

    Creates a simple 3-level book with configurable spread and depth.
    """
    half_spread = spread / 2

    ask_base = min(midpoint + half_spread, 0.99)
    bid_base = max(midpoint - half_spread, 0.01)

    asks = [
        OrderBookLevel(price=round(ask_base, 4), size=depth),
        OrderBookLevel(price=round(min(ask_base + 0.01, 0.99), 4), size=depth),
        OrderBookLevel(price=round(min(ask_base + 0.02, 0.99), 4), size=depth),
    ]
    bids = [
        OrderBookLevel(price=round(bid_base, 4), size=depth),
        OrderBookLevel(price=round(max(bid_base - 0.01, 0.01), 4), size=depth),
        OrderBookLevel(price=round(max(bid_base - 0.02, 0.01), 4), size=depth),
    ]

    return OrderBook(asks=asks, bids=bids)


def run_backtest(
    snapshots: list[PriceSnapshot],
    strategy: Callable[[Engine, PriceSnapshot, dict[str, float]], None],
    strategy_name: str = "unnamed",
    balance: float = 10_000.0,
    spread: float = 0.02,
    depth: float = 500.0,
) -> BacktestResult:
    """Run a backtest with historical price data.

    The strategy callable receives:
        - engine: Engine instance for executing trades
        - snapshot: current PriceSnapshot
        - prices: dict mapping "slug:outcome" -> midpoint for all observed prices

    The engine's midpoint, order book and fee-rate lookups are patched to use
    synthetic values derived from the historical prices. Other API calls the
    strategy makes (e.g. ``engine.api.get_market``) are NOT patched and may hit
    the live API.

    Exceptions raised by the strategy do not stop the backtest; they are counted
    in ``BacktestResult.strategy_errors`` (last message in ``last_error``).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = Engine(Path(tmpdir))
        engine.init_account(balance)

        # Track latest prices for all market/outcome pairs
        prices: dict[str, float] = {}
        strategy_errors = 0
        last_error: str | None = None

        for snapshot in snapshots:
            key = f"{snapshot.market_slug}:{snapshot.outcome}"
            prices[key] = snapshot.midpoint

            # Patch midpoint lookup for this snapshot's market
            def make_midpoint_fn(mid: float):
                def fn(token_id: str) -> float:
                    return mid
                return fn

            engine.api.get_midpoint = make_midpoint_fn(snapshot.midpoint)

            # Patch order book to return synthetic book
            def make_book_fn(mid: float):
                def fn(token_id: str) -> OrderBook:
                    return _build_synthetic_book(mid, spread, depth)
                return fn

            engine.api.get_order_book = make_book_fn(snapshot.midpoint)

            # Patch fee rate to return 0 for backtests
            engine.api.get_fee_rate = lambda token_id: 0

            try:
                strategy(engine, snapshot, dict(prices))
            except Exception as e:
                strategy_errors += 1  # Strategy errors don't stop the backtest
                last_error = f"{type(e).__name__}: {e}"

        # Compute results
        account = engine.get_account()
        trades = engine.db.get_trades(limit=100_000)
        total_trades = len(trades)
        ending_cash = account.cash
        pnl = ending_cash - balance
        roi_pct = (pnl / balance) * 100 if balance > 0 else 0.0

        stats = compute_stats(trades, account, positions_value=0.0)

        result = BacktestResult(
            strategy=strategy_name,
            starting_balance=balance,
            ending_cash=ending_cash,
            total_trades=total_trades,
            pnl=pnl,
            roi_pct=roi_pct,
            sharpe_ratio=stats["sharpe_ratio"],
            win_rate=stats["win_rate"],
            max_drawdown=stats["max_drawdown"],
            snapshots_processed=len(snapshots),
            strategy_errors=strategy_errors,
            last_error=last_error,
        )

        engine.close()
        return result
