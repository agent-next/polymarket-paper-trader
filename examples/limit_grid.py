"""Grid trading strategy — place limit orders at multiple price levels.

Places a grid of limit buy orders below current price and limit sell orders
above entry. Profits from oscillation within a range.

Usage (live):
    pm-trader benchmark run examples.limit_grid.run
"""

from __future__ import annotations

from pm_trader.engine import Engine
from pm_trader.models import OrderRejectedError


# --- Configuration ---
GRID_LEVELS = 5            # Number of buy levels below market
GRID_SPACING = 0.03        # Price gap between levels (3 cents)
ORDER_SIZE_USD = 100.0     # USD per grid level
TAKE_PROFIT_OFFSET = 0.05  # Sell 5 cents above entry


def run(engine: Engine) -> None:
    """Place a grid of limit buy orders on a liquid market."""
    markets = engine.api.list_markets(sort_by="liquidity", limit=5)

    for market in markets:
        if market.closed or market.liquidity < 500:
            continue

        base_price = market.yes_price
        if base_price <= 0.15 or base_price >= 0.90:
            continue  # Skip extreme prices

        # Place grid of limit buys below current price
        for i in range(1, GRID_LEVELS + 1):
            buy_price = round(base_price - (i * GRID_SPACING), 2)
            if buy_price <= 0.01:
                break
            try:
                engine.place_limit_order(
                    slug_or_id=market.slug,
                    outcome="yes",
                    side="buy",
                    amount=ORDER_SIZE_USD,
                    limit_price=buy_price,
                    order_type="gtc",
                )
            except OrderRejectedError:
                pass

        # Check if any existing orders can fill
        engine.check_orders()

        # Place take-profit sells on any filled positions
        pos = engine.db.get_position(market.condition_id, "yes")
        if pos and pos.shares > 0:
            sell_price = round(min(pos.avg_entry_price + TAKE_PROFIT_OFFSET, 0.99), 2)
            try:
                engine.place_limit_order(
                    slug_or_id=market.slug,
                    outcome="yes",
                    side="sell",
                    amount=pos.shares,
                    limit_price=sell_price,
                    order_type="gtc",
                )
            except OrderRejectedError:
                pass

        break  # One market per run
