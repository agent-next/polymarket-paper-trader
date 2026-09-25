"""Benchmarking harness for pm-trader trading strategies.

A strategy is any Python callable with signature:
    def strategy(engine: Engine) -> None

The runner creates a fresh account, executes the strategy,
and computes analytics on the resulting trades.

PK battle: run two strategies head-to-head, same starting conditions.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from tempfile import mkdtemp
from typing import Callable

from pm_trader.analytics import compute_stats
from pm_trader.engine import Engine

# Only allow loading strategies from these packages (prevents arbitrary code execution)
_ALLOWED_STRATEGY_PREFIXES = ("examples.", "tests.test_benchmark.")

# Strategies must be valid Python identifiers separated by dots
_STRATEGY_PATH_RE = re.compile(r"^[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)+$")


def _validate_strategy_path(strategy_path: str) -> tuple[str, str]:
    """Validate and parse a strategy path, returning (module_path, func_name).

    Raises ValueError if the path is malformed or not in the allowlist.
    """
    module_path, _, func_name = strategy_path.rpartition(".")
    if not module_path:
        raise ValueError(
            f"Strategy path must be 'module.function', got: {strategy_path!r}"
        )
    if not _STRATEGY_PATH_RE.match(strategy_path):
        raise ValueError(
            f"Strategy path contains invalid characters: {strategy_path!r}"
        )
    if not any(strategy_path.startswith(p) for p in _ALLOWED_STRATEGY_PREFIXES):
        raise ValueError(
            f"Strategy must be from an allowed package "
            f"({', '.join(_ALLOWED_STRATEGY_PREFIXES)}), "
            f"got: {strategy_path!r}"
        )
    return module_path, func_name


def _load_strategy(strategy_path: str) -> Callable[[Engine], None]:
    """Validate ``strategy_path`` and import it, resolving modules from the cwd.

    Console scripts don't put the working directory on ``sys.path`` (unlike
    ``python -m``), so ``examples.*`` in the user's project must be added here.
    """
    module_path, func_name = _validate_strategy_path(strategy_path)
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    return getattr(importlib.import_module(module_path), func_name)


def run_strategy(
    strategy_path: str,
    *,
    balance: float = 10_000.0,
    data_dir: Path | None = None,
) -> dict:
    """Run a strategy and return its scorecard.

    Args:
        strategy_path: Dotted import path like "examples.momentum.run".
            Must be from an allowed package (examples.* or tests.test_benchmark.*).
        balance: Starting account balance.
        data_dir: Optional data directory. Uses a temp dir if not provided.

    Returns:
        Dict with strategy name, analytics metrics, and trade count.
    """
    strategy_fn = _load_strategy(strategy_path)

    # Create a fresh engine
    if data_dir is None:
        data_dir = Path(mkdtemp(prefix="pm-trader-bench-"))
    engine = Engine(data_dir)
    engine.init_account(balance)

    try:
        # Execute the strategy
        strategy_fn(engine)

        # Compute analytics
        account = engine.get_account()
        trades = engine.get_history(limit=10_000)
        portfolio = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio)
        stats = compute_stats(trades, account, positions_value)

        return {
            "strategy": strategy_path,
            "data_dir": str(data_dir),
            **stats,
        }
    finally:
        engine.close()


def compare_accounts(
    data_dirs: dict[str, Path],
) -> list[dict]:
    """Compare analytics across multiple named accounts.

    Args:
        data_dirs: Mapping of account name → data directory path.

    Returns:
        List of scorecards, one per account.
    """
    results = []
    for name, data_dir in data_dirs.items():
        engine = Engine(data_dir)
        try:
            account = engine.get_account()
            trades = engine.get_history(limit=10_000)
            portfolio = engine.get_portfolio()
            positions_value = sum(p["current_value"] for p in portfolio)
            stats = compute_stats(trades, account, positions_value)
            results.append({"account": name, **stats})
        finally:
            engine.close()
    return results


def pk_battle(
    strategy_a_path: str,
    strategy_b_path: str,
    name_a: str = "player_a",
    name_b: str = "player_b",
    balance: float = 10_000.0,
) -> dict:
    """Run two strategies head-to-head and generate a PK comparison.

    Both strategies start with the same balance. Runs each,
    computes analytics, generates a PK card, and declares a winner.

    Returns:
        Dict with stats_a, stats_b, pk_card text, and winner name.
    """
    from pm_trader.card import generate_pk_card

    stats_a = run_strategy(strategy_a_path, balance=balance)
    stats_b = run_strategy(strategy_b_path, balance=balance)

    card = generate_pk_card(stats_a, name_a, stats_b, name_b)

    roi_a = stats_a.get("roi_pct", 0.0)
    roi_b = stats_b.get("roi_pct", 0.0)
    if roi_a > roi_b:
        winner = name_a
    elif roi_b > roi_a:
        winner = name_b
    else:
        winner = "tie"

    return {
        "winner": winner,
        "card": card,
        name_a: stats_a,
        name_b: stats_b,
    }
