"""Sole pm_trader touchpoint. All pm_trader imports live here.

Server code imports from this module only — never directly from pm_trader.
When pm-trader upgrades, only this file needs updating.
"""
from __future__ import annotations

from pathlib import Path

from pm_trader.orderbook import simulate_buy_fill, simulate_sell_fill, calculate_fee
from pm_trader.analytics import compute_stats, win_rate, sharpe_ratio, max_drawdown
from pm_trader.models import (
    Market, OrderBook, OrderBookLevel, FillResult, Fill,
    Trade, Position, Account, TradeResult,
    SimError, InsufficientBalanceError, MarketClosedError,
    OrderRejectedError, AmbiguousResolutionError, NoPositionError,
    InvalidOutcomeError,
)
from pm_trader.api import PolymarketClient
from pm_trader.db import Database

from server.config import API_CACHE_DIR


def create_polymarket_client() -> PolymarketClient:
    """Create a PolymarketClient with a temp SQLite for API caching only.

    Trade data goes to PostgreSQL. This SQLite is only for the 5-minute
    market metadata cache that PolymarketClient requires.
    """
    cache_dir = Path(API_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    db = Database(cache_dir)
    db.init_schema()
    return PolymarketClient(db)


def validate_outcome(outcome: str, market: Market) -> str:
    """Validate and normalize outcome against market."""
    outcome = outcome.lower().strip()
    if not outcome:
        raise InvalidOutcomeError(outcome)
    valid = [o.lower() for o in market.outcomes]
    if outcome not in valid:
        raise InvalidOutcomeError(outcome, valid)
    return outcome


__all__ = [
    "simulate_buy_fill", "simulate_sell_fill", "calculate_fee",
    "compute_stats", "win_rate", "sharpe_ratio", "max_drawdown",
    "Market", "OrderBook", "OrderBookLevel", "FillResult", "Fill",
    "Trade", "Position", "Account", "TradeResult",
    "SimError", "InsufficientBalanceError", "MarketClosedError",
    "OrderRejectedError", "AmbiguousResolutionError", "NoPositionError",
    "InvalidOutcomeError",
    "PolymarketClient", "Database",
    "create_polymarket_client", "validate_outcome",
]
