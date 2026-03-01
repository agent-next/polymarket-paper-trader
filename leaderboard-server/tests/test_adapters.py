"""Test that pm_trader adapter re-exports work."""
from __future__ import annotations


def test_simulate_buy_fill_importable():
    from server.adapters.polymarket import simulate_buy_fill
    assert callable(simulate_buy_fill)


def test_simulate_sell_fill_importable():
    from server.adapters.polymarket import simulate_sell_fill
    assert callable(simulate_sell_fill)


def test_compute_stats_importable():
    from server.adapters.polymarket import compute_stats
    assert callable(compute_stats)


def test_models_importable():
    from server.adapters.polymarket import (
        Market, OrderBook, OrderBookLevel, FillResult,
        SimError, InsufficientBalanceError, OrderRejectedError,
    )
    assert Market is not None


def test_create_polymarket_client():
    from server.adapters.polymarket import create_polymarket_client
    client = create_polymarket_client()
    assert client is not None
    client.close()
