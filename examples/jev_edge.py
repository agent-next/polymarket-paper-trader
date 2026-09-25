"""Jev edge strategy — trade when Jev's probability disagrees with the book.

Jev (https://fiapp.pro/jev) is a decision model: you give it a `state` and a
map of typed questions, it answers them. For each binary market we ask one
`noul` (yes/no) question — "Will this market resolve YES?" — and compare its
probability against the current YES midpoint. When the gap exceeds EDGE we
buy the underpriced side.

The Jev client lives in the benchmark package:
    pip install -e "benchmark"

Usage (live, from the repo root — examples.* needs the repo root on
sys.path under a strict editable install):
    PYTHONPATH=. pm-trader strategy run examples.jev_edge.run

Environment:
    JEV_MODEL  — Jev model id (default: opencode/jev-1.13-free, no key needed)
    JEV_QUERY  — market search query (default: bitcoin)
"""

from __future__ import annotations

import os

from pm_trader.engine import Engine
from pm_trader.models import Market, SimError


# --- Configuration ---
QUERY = os.environ.get("JEV_QUERY", "bitcoin")       # Market search query
JEV_MODEL = os.environ.get("JEV_MODEL", "opencode/jev-1.13-free")
EDGE = 0.10                  # Min |jev_p - yes_price| gap required to trade
MIN_PRICE = 0.05             # Skip markets priced below this (deep longshots:
MAX_PRICE = 0.95             # fees eat a fixed fraction of stake regardless
                              # of edge size, per the official fee curve —
                              # fee = amount * rate * (1 - p), which stays
                              # near its max as p -> 0)
POSITION_SIZE_USD = 200.0    # Dollars per trade
MAX_POSITIONS = 5            # Maximum concurrent open positions
MAX_MARKETS = 10             # Markets to scan per run
DESCRIPTION_CHARS = 400      # Truncate descriptions in the Jev state

QUESTION_ID = "probability"
JEV_QUESTIONS = {
    QUESTION_ID: {
        "type": "noul",
        "instructions": "Will this market resolve YES?",
    },
}


def _load_jev():
    """Import the Jev client — it ships in the separate benchmark package."""
    try:
        from pm_benchmark import jev
    except ImportError as e:
        raise RuntimeError(
            'jev_edge needs the benchmark package for the Jev client: '
            'pip install -e "benchmark"'
        ) from e
    return jev


def _is_binary(market: Market) -> bool:
    """Only plain YES/NO markets get a `noul` question."""
    return {o.lower() for o in market.outcomes} == {"yes", "no"}


def _market_state(market: Market, yes_mid: float) -> str:
    """State string for Jev — public market data only."""
    return (
        f"Market: {market.question}\n"
        f"Description: {market.description[:DESCRIPTION_CHARS]}\n"
        f"Resolution date: {market.end_date or 'unknown'}\n"
        f"Current YES midpoint price: {yes_mid:.4f}\n"
    )


def _has_position(engine: Engine, market: Market) -> bool:
    for outcome in ("yes", "no"):
        pos = engine.db.get_position(market.condition_id, outcome)
        if pos is not None and pos.shares > 0:
            return True
    return False


def run(engine: Engine) -> None:
    """Scan markets, ask Jev its probability, buy the side Jev favors."""
    jev = _load_jev()
    markets = [
        m for m in engine.api.search_markets(QUERY)
        if not m.closed and _is_binary(m)
    ]

    for market in markets[:MAX_MARKETS]:
        if _has_position(engine, market):
            print(f"[jev_edge] {market.slug}: already held — skip")
            continue
        if len(engine.db.get_open_positions()) >= MAX_POSITIONS:
            print(f"[jev_edge] position cap ({MAX_POSITIONS}) reached — stop")
            break

        try:
            yes_price = engine.api.get_midpoint(market.yes_token_id)
        except SimError:
            yes_price = market.yes_price

        if not (MIN_PRICE <= yes_price <= MAX_PRICE):
            print(f"[jev_edge] {market.slug}: price {yes_price:.2f} outside "
                  f"[{MIN_PRICE:.2f}, {MAX_PRICE:.2f}] — skip")
            continue

        try:
            answers = jev.query_jev(
                JEV_MODEL, _market_state(market, yes_price), JEV_QUESTIONS
            )
            jev_p = jev.noul_probability(answers, QUESTION_ID)
        except jev.JevError as e:
            print(f"[jev_edge] {market.slug}: Jev error: {e} — skip")
            continue

        diff = jev_p - yes_price
        print(
            f"[jev_edge] {market.slug}: jev={jev_p:.2f} "
            f"mid={yes_price:.2f} diff={diff:+.2f}"
        )

        if diff > EDGE:
            outcome = "yes"
        elif -diff > EDGE:
            outcome = "no"
        else:
            continue

        try:
            engine.buy(market.slug, outcome, POSITION_SIZE_USD)
            print(
                f"[jev_edge] {market.slug}: BUY {outcome.upper()} "
                f"${POSITION_SIZE_USD:.0f} (edge {abs(diff):.2f})"
            )
        except SimError as e:
            print(f"[jev_edge] {market.slug}: buy {outcome} failed: {e}")
