"""Shared fixtures for polymarket-benchmark tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary directory for test outputs."""
    return tmp_path / "output"


@pytest.fixture
def sample_market_info():
    """Sample market info dict matching MarketInfo fields."""
    return {
        "slug": "will-bitcoin-hit-100k-2025",
        "question": "Will Bitcoin hit $100k in 2025?",
        "description": "Resolves Yes if BTC reaches $100,000 USD.",
        "outcomes": ["Yes", "No"],
        "outcome_prices": [0.65, 0.35],
        "volume": 1_500_000.0,
        "liquidity": 250_000.0,
        "end_date": "2025-12-31T23:59:59Z",
        "active": True,
        "closed": False,
    }
