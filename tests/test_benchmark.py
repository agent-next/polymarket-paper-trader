"""Tests for benchmarking harness."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from unittest.mock import patch

from pm_trader.benchmark import compare_accounts, pk_battle, run_strategy
from pm_trader.engine import Engine


# ---------------------------------------------------------------------------
# Dummy strategies for testing
# ---------------------------------------------------------------------------


def noop_strategy(engine: Engine) -> None:
    """Does nothing — baseline strategy."""
    pass


def buy_once_strategy(engine: Engine) -> None:
    """Buys $100 of the first market's YES outcome."""
    markets = engine.api.list_markets(limit=1)
    if markets:
        engine.buy(markets[0].slug, "yes", 100.0)


def noop_backtest_strategy(engine, snapshot, prices) -> None:
    """Noop backtest strategy for testing."""
    pass


# ---------------------------------------------------------------------------
# run_strategy tests
# ---------------------------------------------------------------------------


class TestRunStrategy:
    def test_invalid_strategy_path(self):
        with pytest.raises(ValueError, match="module.function"):
            run_strategy("just_a_name")

    def test_disallowed_module(self):
        with pytest.raises(ValueError, match="allowed package"):
            run_strategy("os.system")

    def test_invalid_characters(self):
        with pytest.raises(ValueError, match="invalid characters"):
            run_strategy("examples.foo;bar.run")

    def test_missing_function(self):
        with pytest.raises(AttributeError):
            run_strategy("tests.test_benchmark.nonexistent_function")

    def test_noop_strategy(self):
        result = run_strategy(
            "tests.test_benchmark.noop_strategy",
            balance=5_000.0,
        )
        assert result["strategy"] == "tests.test_benchmark.noop_strategy"
        assert result["starting_balance"] == 5_000.0
        assert result["cash"] == 5_000.0
        assert result["total_trades"] == 0
        assert result["pnl"] == 0.0
        assert result["roi_pct"] == 0.0

    def test_resolves_strategy_from_cwd(self, tmp_path: Path, monkeypatch):
        """Console scripts lack the cwd on sys.path; a user's examples/ must still load."""
        (tmp_path / "examples").mkdir()
        (tmp_path / "examples" / "cwd_probe.py").write_text(
            "def run(engine):\n    engine.init_account(321.0)\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p not in ("", ".")])
        monkeypatch.delitem(sys.modules, "examples.cwd_probe", raising=False)
        for _ in range(2):  # second call: cwd already on sys.path
            assert run_strategy("examples.cwd_probe.run")["cash"] == 321.0
        assert sys.path[0] == str(tmp_path)
        monkeypatch.delitem(sys.modules, "examples.cwd_probe")


# ---------------------------------------------------------------------------
# compare_accounts tests
# ---------------------------------------------------------------------------


class TestCompareAccounts:
    def test_compare_two_accounts(self, tmp_path: Path):
        # Create two accounts with different balances
        dir_a = tmp_path / "agent-a"
        dir_b = tmp_path / "agent-b"

        eng_a = Engine(dir_a)
        eng_a.init_account(10_000.0)
        eng_a.close()

        eng_b = Engine(dir_b)
        eng_b.init_account(5_000.0)
        eng_b.close()

        results = compare_accounts({
            "agent-a": dir_a,
            "agent-b": dir_b,
        })
        assert len(results) == 2
        assert results[0]["account"] == "agent-a"
        assert results[0]["starting_balance"] == 10_000.0
        assert results[1]["account"] == "agent-b"
        assert results[1]["starting_balance"] == 5_000.0

    def test_compare_empty(self):
        results = compare_accounts({})
        assert results == []


# ---------------------------------------------------------------------------
# pk_battle tests
# ---------------------------------------------------------------------------


class TestPkBattle:
    def test_two_noop_strategies(self):
        result = pk_battle(
            "tests.test_benchmark.noop_strategy",
            "tests.test_benchmark.noop_strategy",
            name_a="alpha",
            name_b="beta",
            balance=5_000.0,
        )
        assert result["winner"] == "tie"
        assert "alpha" in result["card"]
        assert "beta" in result["card"]
        assert "Tie" in result["card"]
        assert "alpha" in result
        assert "beta" in result
        assert result["alpha"]["starting_balance"] == 5_000.0
        assert result["beta"]["starting_balance"] == 5_000.0

    def test_bad_strategy_raises(self):
        with pytest.raises(ValueError, match="module.function"):
            pk_battle("bad_path", "also_bad")

    @patch("pm_trader.benchmark.run_strategy")
    def test_a_wins(self, mock_run):
        mock_run.side_effect = [
            {"roi_pct": 15.0, "pnl": 1500.0, "total_trades": 10, "sharpe_ratio": 1.2, "win_rate": 0.7},
            {"roi_pct": 5.0, "pnl": 500.0, "total_trades": 8, "sharpe_ratio": 0.5, "win_rate": 0.5},
        ]
        result = pk_battle("a.run", "b.run", name_a="alpha", name_b="beta")
        assert result["winner"] == "alpha"

    @patch("pm_trader.benchmark.run_strategy")
    def test_b_wins(self, mock_run):
        mock_run.side_effect = [
            {"roi_pct": 2.0, "pnl": 200.0, "total_trades": 5},
            {"roi_pct": 18.0, "pnl": 1800.0, "total_trades": 12},
        ]
        result = pk_battle("a.run", "b.run", name_a="slow", name_b="fast")
        assert result["winner"] == "fast"
