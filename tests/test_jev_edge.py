"""Tests for the examples/jev_edge.py strategy.

The Jev client ships in the separate `pm_benchmark` package, which the root
test suite does not install — tests patch `examples.jev_edge._load_jev` with a
duck-typed stand-in instead.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import examples.jev_edge as jev_edge
from pm_trader.engine import Engine
from pm_trader.models import ApiError, Market, OrderBook, OrderBookLevel


# ---------------------------------------------------------------------------
# Fixtures and helpers (same pattern as tests/test_behavior.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path / "test")
    yield e
    e.close()


@pytest.fixture
def acct(engine):
    engine.init_account(10_000.0)
    return engine


def _market(
    slug="test-market",
    condition_id="0xtest",
    question="Test question?",
    *,
    closed=False,
    outcomes=None,
    tokens=None,
    outcome_prices=None,
):
    outcomes = outcomes or ["Yes", "No"]
    return Market(
        condition_id=condition_id,
        slug=slug,
        question=question,
        description="Some public description.",
        outcomes=outcomes,
        outcome_prices=outcome_prices or [0.65, 0.35],
        tokens=tokens or [
            {"token_id": f"tok_yes_{slug}", "outcome": "Yes"},
            {"token_id": f"tok_no_{slug}", "outcome": "No"},
        ],
        active=not closed,
        closed=closed,
        volume=1_000_000.0,
        liquidity=100_000.0,
        end_date="2026-12-31T23:59:59Z",
    )


def _book():
    return OrderBook(
        asks=[OrderBookLevel(0.66, 500)],
        bids=[OrderBookLevel(0.64, 500)],
    )


def _mock(engine, markets, mid=0.65):
    """Mock the engine's API surface: search, market lookup, book, fees, mid."""
    by_slug = {m.slug: m for m in markets}
    engine.api.search_markets = MagicMock(return_value=markets)
    engine.api.get_market = MagicMock(side_effect=lambda slug: by_slug[slug])
    engine.api.get_order_book = MagicMock(return_value=_book())
    engine.api.get_fee_rate = MagicMock(return_value=0)
    engine.api.get_midpoint = MagicMock(return_value=mid)


class _FakeJevError(Exception):
    """Stand-in for pm_benchmark.jev.JevError."""


def _fake_jev(probability=0.65, raises=None):
    """Return a duck-typed pm_benchmark.jev module, recording query calls."""
    calls = []

    def query_jev(model, state, questions, **kwargs):
        calls.append({"model": model, "state": state, "questions": questions})
        if raises is not None:
            raise raises
        return {jev_edge.QUESTION_ID: {"noul": probability}}

    def noul_probability(answers, question_id):
        return float(answers[question_id]["noul"])

    return SimpleNamespace(
        query_jev=query_jev,
        noul_probability=noul_probability,
        JevError=_FakeJevError,
        calls=calls,
    )


def _use_fake_jev(monkeypatch, jev):
    monkeypatch.setattr(jev_edge, "_load_jev", lambda: jev)


# ---------------------------------------------------------------------------
# Strategy behavior
# ---------------------------------------------------------------------------


class TestJevEdge:
    def test_buys_yes_when_jev_probability_above_market(
        self, acct, monkeypatch, capsys
    ):
        market = _market()
        _mock(acct, [market], mid=0.65)
        jev = _fake_jev(probability=0.80)  # diff = +0.15 > EDGE
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        pos = acct.db.get_position(market.condition_id, "yes")
        assert pos is not None and pos.shares > 0
        out = capsys.readouterr().out
        assert "jev=0.80" in out and "mid=0.65" in out and "BUY YES" in out

    def test_buys_no_when_jev_probability_below_market(self, acct, monkeypatch):
        market = _market()
        _mock(acct, [market], mid=0.65)
        jev = _fake_jev(probability=0.30)  # diff = -0.35
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        pos = acct.db.get_position(market.condition_id, "no")
        assert pos is not None and pos.shares > 0

    def test_no_edge_skips(self, acct, monkeypatch):
        _mock(acct, [_market()], mid=0.65)
        jev = _fake_jev(probability=0.66)  # diff = +0.01 < EDGE
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        assert acct.get_history() == []
        assert acct.db.get_open_positions() == []

    def test_already_held_market_skipped(self, acct, monkeypatch):
        market = _market()
        _mock(acct, [market])
        acct.buy(market.slug, "yes", 100.0)
        trades_before = len(acct.get_history())
        jev = _fake_jev(probability=0.95)
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        assert jev.calls == []  # skipped before ever asking Jev
        assert len(acct.get_history()) == trades_before

    def test_jev_error_skips_market_and_continues(self, acct, monkeypatch):
        bad = _market(slug="m1", condition_id="0x1", question="First?")
        good = _market(slug="m2", condition_id="0x2", question="Second?")
        _mock(acct, [bad, good])
        jev = _fake_jev(probability=0.90)

        def flaky(model, state, questions, **kwargs):
            jev.calls.append({"model": model, "state": state})
            if "First?" in state:
                raise _FakeJevError("Jev HTTP 500")
            return {jev_edge.QUESTION_ID: {"noul": 0.90}}

        jev.query_jev = flaky
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        assert len(jev.calls) == 2
        assert acct.db.get_position(bad.condition_id, "yes") is None
        pos = acct.db.get_position(good.condition_id, "yes")
        assert pos is not None and pos.shares > 0

    def test_state_contains_only_public_market_data(self, acct, monkeypatch):
        market = _market()
        _mock(acct, [market], mid=0.65)
        jev = _fake_jev(probability=0.65)
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        state = jev.calls[0]["state"]
        assert market.question in state
        assert market.description in state
        assert market.end_date in state
        assert "0.6500" in state
        # Nothing beyond public market data — no token ids / condition id
        assert market.condition_id not in state
        assert "tok_yes" not in state
        assert jev.calls[0]["model"] == jev_edge.JEV_MODEL

    def test_midpoint_failure_falls_back_to_market_price(
        self, acct, monkeypatch
    ):
        market = _market()
        _mock(acct, [market])
        acct.api.get_midpoint = MagicMock(
            side_effect=ApiError("midpoint down")
        )
        jev = _fake_jev(probability=0.90)
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        assert "0.6500" in jev.calls[0]["state"]  # market.yes_price fallback
        pos = acct.db.get_position(market.condition_id, "yes")
        assert pos is not None and pos.shares > 0

    def test_closed_and_non_binary_markets_skipped(self, acct, monkeypatch):
        closed = _market(slug="m1", condition_id="0x1", closed=True)
        multi = _market(
            slug="m2",
            condition_id="0x2",
            outcomes=["A", "B", "C"],
            tokens=[
                {"token_id": "tok_a", "outcome": "A"},
                {"token_id": "tok_b", "outcome": "B"},
                {"token_id": "tok_c", "outcome": "C"},
            ],
            outcome_prices=[0.4, 0.35, 0.25],
        )
        open_market = _market(slug="m3", condition_id="0x3")
        _mock(acct, [closed, multi, open_market])
        jev = _fake_jev(probability=0.65)
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        assert len(jev.calls) == 1  # only the open binary market was asked
        assert open_market.question in jev.calls[0]["state"]

    def test_price_band_skips_before_querying_jev(self, acct, monkeypatch):
        market = _market()
        _mock(acct, [market], mid=0.02)  # below MIN_PRICE
        jev = _fake_jev(probability=0.90)  # would be a huge edge if asked
        _use_fake_jev(monkeypatch, jev)

        jev_edge.run(acct)

        assert jev.calls == []  # skipped before ever asking Jev
        assert acct.get_history() == []

    def test_position_cap_stops_scan(self, acct, monkeypatch):
        _mock(acct, [_market()])
        jev = _fake_jev(probability=0.95)
        _use_fake_jev(monkeypatch, jev)
        monkeypatch.setattr(jev_edge, "MAX_POSITIONS", 0)

        jev_edge.run(acct)

        assert jev.calls == []
        assert acct.get_history() == []

    def test_missing_benchmark_package_is_a_clear_error(
        self, acct, monkeypatch
    ):
        _mock(acct, [_market()])
        monkeypatch.setitem(sys.modules, "pm_benchmark", None)

        with pytest.raises(RuntimeError, match='pip install -e "benchmark"'):
            jev_edge.run(acct)
