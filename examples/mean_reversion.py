"""Mean reversion strategy — buy dips, sell rips around fair value.

Assumes prediction markets revert toward 0.50 when no strong signal exists.
Buys when price drops below lower band, sells when it returns above upper band.

Usage (live):
    pm-trader benchmark run examples.mean_reversion.run

Usage (backtest):
    from pm_trader.backtest import run_backtest, load_snapshots_csv
    from examples.mean_reversion import backtest_strategy
    snapshots = load_snapshots_csv("data/prices.csv")
    result = run_backtest(snapshots, backtest_strategy, "mean_reversion")
"""

from __future__ import annotations

from pm_trader.engine import Engine
from pm_trader.models import NoPositionError, OrderRejectedError


# --- Configuration ---
FAIR_VALUE = 0.50          # Assumed center of mean reversion
ENTRY_DEVIATION = 0.12     # Buy when price is this far below fair value
EXIT_DEVIATION = 0.03      # Sell when price returns within this of fair value
POSITION_SIZE_USD = 150.0  # Dollars per trade
MAX_POSITIONS = 8


def run(engine: Engine) -> None:
    """Live mean reversion — buy underpriced YES, sell when it reverts."""
    markets = engine.api.list_markets(sort_by="liquidity", limit=20)

    for market in markets:
        if market.closed or market.liquidity < 50:
            continue

        price = market.yes_price
        pos = engine.db.get_position(market.condition_id, "yes")
        has_position = pos is not None and pos.shares > 0

        if has_position:
            # Exit: price reverted back toward fair value
            if price >= FAIR_VALUE - EXIT_DEVIATION:
                try:
                    engine.sell(market.slug, "yes", pos.shares, order_type="fak")
                except (OrderRejectedError, NoPositionError):
                    pass
        else:
            # Entry: price dropped below fair value - entry_deviation
            open_count = len(engine.db.get_open_positions())
            if price < FAIR_VALUE - ENTRY_DEVIATION and open_count < MAX_POSITIONS:
                try:
                    engine.buy(market.slug, "yes", POSITION_SIZE_USD)
                except Exception:
                    pass


def backtest_strategy(engine: Engine, snapshot, prices: dict) -> None:
    """Backtest version — called once per price snapshot."""
    mid = snapshot.midpoint

    pos = engine.db.get_position(
        engine.api.get_market(snapshot.market_slug).condition_id,
        snapshot.outcome,
    )
    has_position = pos is not None and pos.shares > 0

    if has_position:
        if mid >= FAIR_VALUE - EXIT_DEVIATION:
            try:
                engine.sell(snapshot.market_slug, snapshot.outcome, pos.shares, order_type="fak")
            except Exception:
                pass
    elif mid < FAIR_VALUE - ENTRY_DEVIATION:
        try:
            engine.buy(snapshot.market_slug, snapshot.outcome, POSITION_SIZE_USD)
        except Exception:
            pass
