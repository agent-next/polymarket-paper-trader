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

import sqlite3

from server.config import API_CACHE_DIR


class _ThreadSafeDatabase(Database):
    """Database subclass with check_same_thread=False for uvicorn workers."""

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn


def create_polymarket_client() -> PolymarketClient:
    """Create a PolymarketClient with a thread-safe SQLite cache.

    Trade data goes to the leaderboard DB. This SQLite is only for
    the 5-minute market metadata cache that PolymarketClient requires.
    """
    cache_dir = Path(API_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    db = _ThreadSafeDatabase(cache_dir)
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
