"""Tests for model dataclasses and error types."""

from __future__ import annotations

import pytest

from pm_trader.models import (
    Market,
    Position,
    TickSizeViolationError,
)


class TestTickSizeViolationError:
    def test_attributes(self):
        err = TickSizeViolationError(0.123, 0.01)
        assert err.price == 0.123
        assert err.tick_size == 0.01
        assert "0.123" in str(err)
        assert "0.01" in str(err)


class TestMarketProperties:
    def test_yes_price(self):
        m = Market(
            condition_id="0x1", slug="m", question="Q", description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.70, 0.30],
            tokens=[
                {"token_id": "t1", "outcome": "Yes"},
                {"token_id": "t2", "outcome": "No"},
            ],
            active=True, closed=False,
        )
        assert m.yes_price == 0.70

    def test_no_price(self):
        m = Market(
            condition_id="0x1", slug="m", question="Q", description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.70, 0.30],
            tokens=[
                {"token_id": "t1", "outcome": "Yes"},
                {"token_id": "t2", "outcome": "No"},
            ],
            active=True, closed=False,
        )
        assert m.no_price == 0.30

    def test_yes_price_missing(self):
        """Multi-outcome market without 'Yes' returns 0.0."""
        m = Market(
            condition_id="0x1", slug="m", question="Q", description="",
            outcomes=["A", "B"],
            outcome_prices=[0.60, 0.40],
            tokens=[
                {"token_id": "t1", "outcome": "A"},
                {"token_id": "t2", "outcome": "B"},
            ],
            active=True, closed=False,
        )
        assert m.yes_price == 0.0
        assert m.no_price == 0.0


class TestPositionMethods:
    def _pos(self, **kwargs):
        defaults = dict(
            market_condition_id="0x1",
            market_slug="m",
            market_question="Q",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.60,
            total_cost=60.0,
            realized_pnl=0.0,
            is_resolved=False,
        )
        defaults.update(kwargs)
        return Position(**defaults)

    def test_current_price(self):
        pos = self._pos()
        assert pos.current_price(0.75) == 0.75

    def test_percent_pnl_zero_cost(self):
        pos = self._pos(total_cost=0.0)
        assert pos.percent_pnl(0.80) == 0.0


@pytest.mark.parametrize("name", ["default", "alice", "a-b_c", "x.y"])
def test_is_valid_account_name_accepts(name):
    from pm_trader.models import is_valid_account_name

    assert is_valid_account_name(name)


@pytest.mark.parametrize("name", ["", ".", "..", " x", "x ", "a/b", "a\\b", "x..y"])
def test_is_valid_account_name_rejects(name):
    from pm_trader.models import is_valid_account_name

    assert not is_valid_account_name(name)
