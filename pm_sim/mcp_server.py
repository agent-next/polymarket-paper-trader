"""MCP server exposing pm-sim as tools for AI agents.

Run with:
    pm-sim mcp                  # stdio transport (default)
    python -m pm_sim.mcp_server # direct execution
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from pm_sim.engine import Engine

DEFAULT_DATA_DIR = Path.home() / ".pm-sim" / "default"

mcp = FastMCP("pm-sim", json_response=True)

# ---------------------------------------------------------------------------
# Engine lifecycle — one Engine per server session
# ---------------------------------------------------------------------------

_engine: Engine | None = None


def _get_engine(account: str = "default") -> Engine:
    """Return the current Engine, creating if needed."""
    global _engine
    data_dir = Path.home() / ".pm-sim" / account
    if _engine is None or _engine.db.data_dir != data_dir:
        if _engine is not None:
            _engine.close()
        _engine = Engine(data_dir)
    return _engine


def _ok(data: object) -> str:
    """Wrap data in the standard pm-sim JSON envelope."""
    return json.dumps({"ok": True, "data": data})


def _err(msg: str, code: str = "error") -> str:
    """Return an error envelope."""
    return json.dumps({"ok": False, "error": msg, "code": code})


# ---------------------------------------------------------------------------
# Account tools
# ---------------------------------------------------------------------------


@mcp.tool()
def init_account(balance: float = 10_000.0, account: str = "default") -> str:
    """Initialize a paper trading account with starting balance (USD).

    Creates a new account or resets an existing one.
    """
    engine = _get_engine(account)
    acct = engine.init_account(balance)
    return _ok({
        "cash": acct.cash,
        "starting_balance": acct.starting_balance,
    })


@mcp.tool()
def get_balance(account: str = "default") -> str:
    """Get current account balance, positions value, and P&L."""
    try:
        engine = _get_engine(account)
        bal = engine.get_balance()
        return _ok(bal)
    except Exception as e:
        return _err(str(e), "not_initialized")


@mcp.tool()
def reset_account(account: str = "default") -> str:
    """Reset account — deletes all trades, positions, and balance."""
    engine = _get_engine(account)
    engine.reset()
    return _ok({"reset": True})


# ---------------------------------------------------------------------------
# Market data tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_markets(query: str, limit: int = 10) -> str:
    """Search Polymarket for markets matching a query string."""
    engine = _get_engine()
    markets = engine.api.search_markets(query, limit=limit)
    return _ok([
        {
            "slug": m.slug,
            "question": m.question,
            "condition_id": m.condition_id,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "closed": m.closed,
            "end_date": m.end_date,
        }
        for m in markets
    ])


@mcp.tool()
def list_markets(limit: int = 20, sort_by: str = "volume") -> str:
    """List active Polymarket markets sorted by volume or liquidity."""
    engine = _get_engine()
    markets = engine.api.list_markets(limit=limit, sort_by=sort_by)
    return _ok([
        {
            "slug": m.slug,
            "question": m.question,
            "condition_id": m.condition_id,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "closed": m.closed,
        }
        for m in markets
    ])


@mcp.tool()
def get_market(slug_or_id: str) -> str:
    """Get detailed info for a specific market by slug or condition ID."""
    try:
        engine = _get_engine()
        m = engine.api.get_market(slug_or_id)
        return _ok({
            "slug": m.slug,
            "question": m.question,
            "condition_id": m.condition_id,
            "outcomes": m.outcomes,
            "outcome_prices": m.outcome_prices,
            "volume": m.volume,
            "liquidity": m.liquidity,
            "closed": m.closed,
            "end_date": m.end_date,
        })
    except Exception as e:
        return _err(str(e), "market_not_found")


@mcp.tool()
def get_order_book(slug_or_id: str, outcome: str = "yes") -> str:
    """Get the live order book for a market outcome (asks and bids)."""
    try:
        engine = _get_engine()
        market = engine.api.get_market(slug_or_id)
        token_id = market.get_token_id(outcome)
        book = engine.api.get_order_book(token_id)
        return _ok({
            "market_slug": market.slug,
            "outcome": outcome.lower(),
            "asks": [{"price": a.price, "size": a.size} for a in book.asks],
            "bids": [{"price": b.price, "size": b.size} for b in book.bids],
        })
    except Exception as e:
        return _err(str(e), "order_book_error")


@mcp.tool()
def watch_prices(
    slugs: str, outcomes: str = "yes",
) -> str:
    """Watch live midpoint prices for one or more markets.

    slugs: comma-separated market slugs or condition IDs
    outcomes: comma-separated outcomes (default: "yes")
    """
    try:
        engine = _get_engine()
        slug_list = [s.strip() for s in slugs.split(",")]
        outcome_list = [o.strip() for o in outcomes.split(",")]
        prices = engine.watch_prices(slug_list, outcome_list)
        return _ok(prices)
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Trading tools
# ---------------------------------------------------------------------------


@mcp.tool()
def buy(
    slug_or_id: str,
    outcome: str,
    amount_usd: float,
    order_type: str = "fok",
    account: str = "default",
) -> str:
    """Buy shares in a Polymarket outcome.

    Spends amount_usd to buy shares at the best available ask prices.
    order_type: "fok" (fill-or-kill) or "fak" (fill-and-kill, allows partial).
    """
    try:
        engine = _get_engine(account)
        result = engine.buy(slug_or_id, outcome, amount_usd, order_type)
        return _ok({
            "trade": {
                "id": result.trade.id,
                "market_slug": result.trade.market_slug,
                "outcome": result.trade.outcome,
                "side": result.trade.side,
                "avg_price": result.trade.avg_price,
                "amount_usd": result.trade.amount_usd,
                "shares": result.trade.shares,
                "fee": result.trade.fee,
                "slippage_bps": result.trade.slippage,
            },
            "account": {
                "cash": result.account.cash,
            },
        })
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def sell(
    slug_or_id: str,
    outcome: str,
    shares: float,
    order_type: str = "fok",
    account: str = "default",
) -> str:
    """Sell shares in a Polymarket outcome.

    Sells shares at the best available bid prices.
    order_type: "fok" (fill-or-kill) or "fak" (fill-and-kill, allows partial).
    """
    try:
        engine = _get_engine(account)
        result = engine.sell(slug_or_id, outcome, shares, order_type)
        return _ok({
            "trade": {
                "id": result.trade.id,
                "market_slug": result.trade.market_slug,
                "outcome": result.trade.outcome,
                "side": result.trade.side,
                "avg_price": result.trade.avg_price,
                "amount_usd": result.trade.amount_usd,
                "shares": result.trade.shares,
                "fee": result.trade.fee,
                "slippage_bps": result.trade.slippage,
            },
            "account": {
                "cash": result.account.cash,
            },
        })
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Portfolio tools
# ---------------------------------------------------------------------------


@mcp.tool()
def portfolio(account: str = "default") -> str:
    """Get all open positions with live prices and unrealized P&L."""
    try:
        engine = _get_engine(account)
        positions = engine.get_portfolio()
        return _ok(positions)
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def history(limit: int = 50, account: str = "default") -> str:
    """Get recent trade history."""
    try:
        engine = _get_engine(account)
        trades = engine.get_history(limit)
        return _ok([
            {
                "id": t.id,
                "market_slug": t.market_slug,
                "outcome": t.outcome,
                "side": t.side,
                "avg_price": t.avg_price,
                "amount_usd": t.amount_usd,
                "shares": t.shares,
                "fee": t.fee,
                "created_at": t.created_at,
            }
            for t in trades
        ])
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Limit order tools
# ---------------------------------------------------------------------------


@mcp.tool()
def place_limit_order(
    slug_or_id: str,
    outcome: str,
    side: str,
    amount: float,
    limit_price: float,
    order_type: str = "gtc",
    expires_at: str | None = None,
    account: str = "default",
) -> str:
    """Place a GTC or GTD limit order.

    side: "buy" or "sell"
    limit_price: target price between 0 and 1
    order_type: "gtc" (good-til-cancelled) or "gtd" (good-til-date)
    expires_at: ISO timestamp for GTD orders (required if order_type="gtd")
    """
    try:
        engine = _get_engine(account)
        order = engine.place_limit_order(
            slug_or_id, outcome, side, amount, limit_price,
            order_type, expires_at,
        )
        return _ok(order)
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def list_orders(account: str = "default") -> str:
    """List all pending limit orders."""
    engine = _get_engine(account)
    orders = engine.get_pending_orders()
    return _ok(orders)


@mcp.tool()
def cancel_order(order_id: int, account: str = "default") -> str:
    """Cancel a pending limit order by ID."""
    engine = _get_engine(account)
    order = engine.cancel_limit_order(order_id)
    if order is None:
        return _err(f"Order {order_id} not found or not pending", "not_found")
    return _ok(order)


@mcp.tool()
def check_orders(account: str = "default") -> str:
    """Check all pending limit orders against live prices and execute fills.

    Call this periodically to trigger limit order evaluation.
    """
    try:
        engine = _get_engine(account)
        results = engine.check_orders()
        return _ok(results)
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Analytics tools
# ---------------------------------------------------------------------------


@mcp.tool()
def stats(account: str = "default") -> str:
    """Get performance analytics: Sharpe ratio, win rate, max drawdown, ROI%."""
    try:
        engine = _get_engine(account)
        from pm_sim.analytics import compute_stats

        acct = engine.get_account()
        trades = engine.db.get_trades(limit=10_000)
        portfolio_items = engine.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio_items)

        result = compute_stats(trades, acct, positions_value)
        return _ok(result)
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Resolution tools
# ---------------------------------------------------------------------------


@mcp.tool()
def resolve(slug_or_id: str, account: str = "default") -> str:
    """Resolve a market's positions, paying out $1/share for winning outcome."""
    try:
        engine = _get_engine(account)
        results = engine.resolve_market(slug_or_id)
        return _ok([
            {
                "outcome": r.position.outcome,
                "shares": r.position.shares,
                "payout": r.payout,
                "cash_after": r.account.cash,
            }
            for r in results
        ])
    except Exception as e:
        return _err(str(e), type(e).__name__)


@mcp.tool()
def resolve_all(account: str = "default") -> str:
    """Resolve all open positions in closed/resolved markets."""
    try:
        engine = _get_engine(account)
        results = engine.resolve_all()
        return _ok([
            {
                "market_slug": r.position.market_slug,
                "outcome": r.position.outcome,
                "shares": r.position.shares,
                "payout": r.payout,
            }
            for r in results
        ])
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Backtesting tools
# ---------------------------------------------------------------------------


@mcp.tool()
def backtest(
    data_path: str,
    strategy_path: str,
    balance: float = 10_000.0,
    spread: float = 0.02,
    depth: float = 500.0,
) -> str:
    """Run a backtest with historical price data.

    data_path: path to CSV or JSON file with historical prices
    strategy_path: dotted Python path to strategy function (e.g. "mymod.my_strategy")
    balance: starting balance (USD)
    spread: synthetic order book spread
    depth: synthetic order book depth per level
    """
    try:
        import importlib
        from pathlib import Path as P

        from pm_sim.backtest import (
            load_snapshots_csv,
            load_snapshots_json,
            run_backtest,
        )

        data = P(data_path)
        if data.suffix == ".json":
            snapshots = load_snapshots_json(data)
        else:
            snapshots = load_snapshots_csv(data)

        # Import strategy
        parts = strategy_path.rsplit(".", 1)
        if len(parts) != 2:
            return _err("strategy_path must be module.function", "invalid_strategy")
        mod = importlib.import_module(parts[0])
        strategy_fn = getattr(mod, parts[1])

        from dataclasses import asdict
        result = run_backtest(
            snapshots, strategy_fn, strategy_path, balance, spread, depth,
        )
        return _ok(asdict(result))
    except Exception as e:
        return _err(str(e), type(e).__name__)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run MCP server on stdio transport."""
    mcp.run()


if __name__ == "__main__":
    main()
