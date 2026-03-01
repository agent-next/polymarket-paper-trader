"""Momentum strategy — buy when price crosses above threshold, sell when it drops.

Usage (live):
    pm-trader benchmark run examples.momentum.run

Usage (backtest):
    from pm_trader.backtest import run_backtest, load_snapshots_csv
    from examples.momentum import backtest_strategy
    snapshots = load_snapshots_csv("data/prices.csv")
    result = run_backtest(snapshots, backtest_strategy, "momentum")
"""

from __future__ import annotations

from pm_trader.engine import Engine
from pm_trader.models import NoPositionError, OrderRejectedError


# --- Configuration ---
BUY_THRESHOLD = 0.55      # Buy when YES price > this
SELL_THRESHOLD = 0.70      # Take profit when YES price > this
STOP_LOSS = 0.35           # Cut loss when YES price < this
POSITION_SIZE_USD = 200.0  # Dollars per trade
MAX_POSITIONS = 5          # Maximum concurrent positions


def run(engine: Engine) -> None:
    """Live momentum strategy — scans top markets and trades signals."""
    markets = engine.api.search_markets("bitcoin")

    for market in markets[:10]:
        if market.closed or market.liquidity < 100:
            continue

        price = market.yes_price

        # Check if we already have a position
        pos = engine.db.get_position(market.condition_id, "yes")
        has_position = pos is not None and pos.shares > 0

        if has_position:
            # Take profit or stop loss
            if price >= SELL_THRESHOLD or price <= STOP_LOSS:
                try:
                    engine.sell(market.slug, "yes", pos.shares, order_type="fak")
                except (OrderRejectedError, NoPositionError):
                    pass
        else:
            # Entry signal: price above threshold
            open_positions = len(engine.db.get_open_positions())
            if price > BUY_THRESHOLD and open_positions < MAX_POSITIONS:
                try:
                    engine.buy(market.slug, "yes", POSITION_SIZE_USD)
                except (OrderRejectedError, Exception):
                    pass


def backtest_strategy(engine: Engine, snapshot, prices: dict) -> None:
    """Backtest version — called once per price snapshot."""
    key = f"{snapshot.market_slug}:{snapshot.outcome}"
    mid = snapshot.midpoint

    pos = engine.db.get_position(
        engine.api.get_market(snapshot.market_slug).condition_id,
        snapshot.outcome,
    )
    has_position = pos is not None and pos.shares > 0

    if has_position:
        if mid >= SELL_THRESHOLD or mid <= STOP_LOSS:
            try:
                engine.sell(snapshot.market_slug, snapshot.outcome, pos.shares, order_type="fak")
            except Exception:
                pass
    elif mid > BUY_THRESHOLD:
        try:
            engine.buy(snapshot.market_slug, snapshot.outcome, POSITION_SIZE_USD)
        except Exception:
            pass
