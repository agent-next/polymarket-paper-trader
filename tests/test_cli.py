"""Tests for the pm-sim CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click.testing
import pytest

from pm_sim.cli import main
from pm_sim.models import (
    Market,
    OrderBook,
    OrderBookLevel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return click.testing.CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pm-sim-cli-test"
    d.mkdir()
    return d


def _invoke(runner, args: list[str], data_dir: Path):
    """Invoke the CLI with --data-dir and return parsed JSON."""
    result = runner.invoke(main, ["--data-dir", str(data_dir)] + args)
    return result


def _parse(result) -> dict:
    """Parse the JSON output from a CLI invocation."""
    return json.loads(result.output)


SAMPLE_MARKET = Market(
    condition_id="0xabc123",
    slug="will-bitcoin-hit-100k",
    question="Will Bitcoin hit $100k?",
    description="BTC market",
    outcomes=["Yes", "No"],
    outcome_prices=[0.65, 0.35],
    tokens=[
        {"token_id": "tok_yes", "outcome": "Yes"},
        {"token_id": "tok_no", "outcome": "No"},
    ],
    active=True,
    closed=False,
    volume=5_000_000.0,
    liquidity=250_000.0,
    end_date="2026-12-31",
    fee_rate_bps=0,
    tick_size=0.01,
)

SAMPLE_BOOK = OrderBook(
    bids=[
        OrderBookLevel(price=0.64, size=500),
        OrderBookLevel(price=0.63, size=500),
    ],
    asks=[
        OrderBookLevel(price=0.66, size=500),
        OrderBookLevel(price=0.67, size=500),
    ],
)


# ---------------------------------------------------------------------------
# Init / Balance / Reset
# ---------------------------------------------------------------------------

class TestAccountCommands:
    def test_init(self, runner, data_dir):
        result = _invoke(runner, ["init"], data_dir)
        assert result.exit_code == 0
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cash"] == 10_000.0

    def test_init_custom_balance(self, runner, data_dir):
        result = _invoke(runner, ["init", "--balance", "5000"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cash"] == 5000.0

    def test_balance_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["balance"], data_dir)
        assert result.exit_code == 1
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "NOT_INITIALIZED"

    def test_balance_after_init(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["balance"], data_dir)
        assert result.exit_code == 0
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["cash"] == 10_000.0
        assert data["data"]["total_value"] == 10_000.0

    def test_reset_without_confirm(self, runner, data_dir):
        result = _invoke(runner, ["reset"], data_dir)
        assert result.exit_code == 1
        data = _parse(result)
        assert data["code"] == "CONFIRM_REQUIRED"

    def test_reset_with_confirm(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["reset", "--confirm"], data_dir)
        assert result.exit_code == 0
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["reset"] is True


# ---------------------------------------------------------------------------
# Buy / Sell (with mocked API)
# ---------------------------------------------------------------------------

class TestTradingCommands:
    def _init_and_mock(self, runner, data_dir):
        """Initialize account and set up API mocks."""
        _invoke(runner, ["init"], data_dir)

    @patch("pm_sim.engine.PolymarketClient")
    def test_buy(self, MockClient, runner, data_dir):
        self._init_and_mock(runner, data_dir)

        # Mock the API client that gets created in Engine.__init__
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        mock_instance.get_order_book.return_value = SAMPLE_BOOK
        mock_instance.get_fee_rate.return_value = 0
        mock_instance.get_midpoint.return_value = 0.65

        result = _invoke(runner, ["buy", "will-bitcoin-hit-100k", "yes", "100"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"]["trade"]["side"] == "buy"
        assert data["data"]["trade"]["outcome"] == "yes"
        assert data["data"]["account"]["cash"] < 10_000.0

    @patch("pm_sim.engine.PolymarketClient")
    def test_sell_no_position(self, MockClient, runner, data_dir):
        self._init_and_mock(runner, data_dir)

        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET

        result = _invoke(runner, ["sell", "will-bitcoin-hit-100k", "yes", "10"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "NO_POSITION"

    @patch("pm_sim.engine.PolymarketClient")
    def test_buy_invalid_outcome(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_market.return_value = SAMPLE_MARKET
        result = _invoke(runner, ["buy", "btc", "maybe", "100"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "INVALID_OUTCOME"

    def test_buy_minimum_order(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["buy", "btc", "yes", "0.5"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "ORDER_REJECTED"


# ---------------------------------------------------------------------------
# Portfolio / History
# ---------------------------------------------------------------------------

class TestPortfolioCommands:
    def test_portfolio_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["portfolio"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert data["code"] == "NOT_INITIALIZED"

    @patch("pm_sim.engine.PolymarketClient")
    def test_portfolio_empty(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        mock_instance = MockClient.return_value
        mock_instance.get_midpoint.return_value = 0.65

        result = _invoke(runner, ["portfolio"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []

    def test_history_not_initialized(self, runner, data_dir):
        result = _invoke(runner, ["history"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    def test_history_empty(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["history"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []


# ---------------------------------------------------------------------------
# Resolve
# ---------------------------------------------------------------------------

class TestResolveCommand:
    def test_resolve_missing_argument(self, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["resolve"], data_dir)
        data = _parse(result)
        assert data["ok"] is False

    @patch("pm_sim.engine.PolymarketClient")
    def test_resolve_all_empty(self, MockClient, runner, data_dir):
        _invoke(runner, ["init"], data_dir)
        result = _invoke(runner, ["resolve", "--all"], data_dir)
        data = _parse(result)
        assert data["ok"] is True
        assert data["data"] == []


# ---------------------------------------------------------------------------
# JSON envelope format
# ---------------------------------------------------------------------------

class TestJsonEnvelope:
    def test_success_has_ok_true(self, runner, data_dir):
        result = _invoke(runner, ["init"], data_dir)
        data = _parse(result)
        assert "ok" in data
        assert data["ok"] is True
        assert "data" in data

    def test_error_has_ok_false_and_code(self, runner, data_dir):
        result = _invoke(runner, ["balance"], data_dir)
        data = _parse(result)
        assert data["ok"] is False
        assert "error" in data
        assert "code" in data
