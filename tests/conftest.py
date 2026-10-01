"""Shared fixtures for pm-trader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from pm_trader.models import Market, OrderBook, OrderBookLevel


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for database storage."""
    data_dir = tmp_path / "pm-trader-test"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def sample_market() -> Market:
    """An active Bitcoin market for testing."""
    return Market(
        condition_id="0xabc123",
        slug="will-bitcoin-hit-100k",
        question="Will Bitcoin hit $100k by end of 2026?",
        description="Resolves YES if Bitcoin reaches $100,000 USD.",
        outcomes=["Yes", "No"],
        outcome_prices=[0.65, 0.35],
        tokens=[
            {"token_id": "tok_yes_btc", "outcome": "Yes"},
            {"token_id": "tok_no_btc", "outcome": "No"},
        ],
        active=True,
        closed=False,
        volume=5_000_000.0,
        liquidity=250_000.0,
        end_date="2026-12-31T23:59:59Z",
        fee_rate_bps=0,
        tick_size=0.01,
    )


@pytest.fixture
def closed_market() -> Market:
    """A resolved market where YES won."""
    return Market(
        condition_id="0xdef456",
        slug="will-eth-hit-5k",
        question="Will ETH hit $5k by March 2026?",
        description="Resolves YES if ETH reaches $5,000 USD.",
        outcomes=["Yes", "No"],
        outcome_prices=[1.0, 0.0],
        tokens=[
            {"token_id": "tok_yes_eth", "outcome": "Yes"},
            {"token_id": "tok_no_eth", "outcome": "No"},
        ],
        active=False,
        closed=True,
        volume=2_000_000.0,
        liquidity=0.0,
        end_date="2026-03-01T00:00:00Z",
        fee_rate_bps=0,
        tick_size=0.01,
    )


@pytest.fixture
def sample_order_book() -> OrderBook:
    """A realistic multi-level order book with bids and asks."""
    return OrderBook(
        bids=[
            OrderBookLevel(price=0.64, size=150.0),
            OrderBookLevel(price=0.63, size=200.0),
            OrderBookLevel(price=0.62, size=300.0),
            OrderBookLevel(price=0.60, size=500.0),
        ],
        asks=[
            OrderBookLevel(price=0.66, size=80.0),
            OrderBookLevel(price=0.67, size=120.0),
            OrderBookLevel(price=0.68, size=200.0),
            OrderBookLevel(price=0.70, size=400.0),
        ],
    )


def seed_trades(db, total: int) -> None:
    """Fill ``db`` with ``total`` trades by repeatedly doubling one seeded row."""
    db.insert_trade(
        market_condition_id="0xbulk",
        market_slug="bulk",
        market_question="Bulk?",
        outcome="yes",
        side="buy",
        order_type="fok",
        avg_price=0.5,
        amount_usd=1.0,
        shares=2.0,
        fee_rate_bps=0,
        fee=0.0,
        slippage=0.0,
        levels_filled=1,
        is_partial=False,
    )
    cols = ", ".join(
        r["name"] for r in db.conn.execute("PRAGMA table_info(trades)") if r["name"] != "id"
    )
    count = 1
    while count < total:
        take = min(count, total - count)
        db.conn.execute(
            f"INSERT INTO trades ({cols}) SELECT {cols} FROM trades LIMIT ?", (take,)
        )
        count += take
    db.conn.commit()
