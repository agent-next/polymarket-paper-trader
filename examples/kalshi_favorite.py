"""Kalshi favorite strategy — buy the middle-priced favorites of one series.

A small live strategy that shows Kalshi paper trading end to end: list a
series' open markets, read each one's live YES midpoint, and paper buy the
favorites priced in the [MIN_PRICE, MAX_PRICE] band. Trades go through the
same engine as Polymarket — the ``kalshi:`` slug is what routes them, with
Kalshi's per-series fee curve and no $1 minimum (so $5 trades are fine).

Usage (live, from the repo root — examples.* needs the repo root on
sys.path under a strict editable install):
    pm-trader strategy run examples.kalshi_favorite.run

Any Kalshi series works; KXNBAGAME (NBA game winners) is just an example
with clean binary markets. Find others with `pm-trader kalshi markets`.
"""

from __future__ import annotations

from pm_trader.engine import Engine
from pm_trader.kalshi_api import KalshiClient
from pm_trader.models import OrderRejectedError


# --- Configuration ---
SERIES = "KXNBAGAME"       # Kalshi series ticker to scan
MIN_PRICE = 0.60           # A favorite, but not a lock: below this is
MAX_PRICE = 0.85           # not favored enough to be worth buying
POSITION_SIZE_USD = 5.0    # Dollars per trade
MAX_POSITIONS = 3         # Maximum concurrent open positions


def run(engine: Engine) -> None:
    """Buy the YES side of every in-band favorite in one Kalshi series."""
    # The engine's market client routes by slug, not by query, so Kalshi
    # listing goes through the read-only client (as the kalshi tools do).
    client = KalshiClient(engine.db)
    try:
        markets = client.list_markets(series_ticker=SERIES, status="open")
    finally:
        client.close()

    for market in markets:
        if market.closed:
            continue
        if len(engine.db.get_open_positions()) >= MAX_POSITIONS:
            break

        pos = engine.db.get_position(market.condition_id, "yes")
        if pos is not None and pos.shares > 0:
            continue

        # The listing is metadata (last trade); the live book is the price.
        mid = engine.api.get_midpoint(market.yes_token_id)
        if not (MIN_PRICE <= mid <= MAX_PRICE):
            continue

        try:
            engine.buy(market.slug, "yes", POSITION_SIZE_USD)
        except OrderRejectedError:
            continue
