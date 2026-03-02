"""Web page routes: HTML views for leaderboard, user profiles, account pages."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from server.db import DB
from server.adapters.polymarket import compute_stats, Trade, Account

router = APIRouter(tags=["web"])

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db(request: Request) -> DB:
    return request.app.state.db


# -- Helpers (same converters as leaderboard.py) --

def _dict_to_trade(d: dict) -> Trade:
    return Trade(
        id=d["id"],
        market_condition_id=d["market_condition_id"],
        market_slug=d["market_slug"],
        market_question=d["market_question"],
        outcome=d["outcome"],
        side=d["side"],
        order_type=d["order_type"],
        avg_price=float(d["avg_price"]),
        amount_usd=float(d["amount_usd"]),
        shares=float(d["shares"]),
        fee_rate_bps=d["fee_rate_bps"],
        fee=float(d["fee"]),
        slippage=float(d["slippage"]),
        levels_filled=d["levels_filled"],
        is_partial=bool(d["is_partial"]),
        created_at=d["created_at"],
    )


def _dict_to_account(d: dict) -> Account:
    return Account(
        id=d["id"],
        starting_balance=float(d["starting_balance"]),
        cash=float(d["cash"]),
        created_at=d["created_at"],
    )


def _compute_tier(trade_count: int, roi_pct: float, sharpe: float) -> str:
    if trade_count >= 50 and roi_pct > 20 and sharpe > 1.5:
        return "diamond"
    if trade_count >= 30 and roi_pct > 10 and sharpe > 1.0:
        return "gold"
    if trade_count >= 20 and roi_pct > 5:
        return "silver"
    return "bronze"


def _stats_for_account(db: DB, account_dict: dict) -> dict | None:
    account_id = account_dict["id"]
    trade_dicts = db.get_trades(account_id, limit=10000)
    if not trade_dicts:
        return None
    try:
        trades = [_dict_to_trade(t) for t in trade_dicts]
        account = _dict_to_account(account_dict)
        stats = compute_stats(trades, account, positions_value=0.0)
        return stats
    except Exception:
        return None


# -- Routes --

@router.get("/", response_class=HTMLResponse)
def homepage(request: Request, db: DB = Depends(get_db)):
    """Leaderboard homepage."""
    rows = db.get_leaderboard_accounts(min_trades=10)
    entries = []
    for row in rows:
        stats = _stats_for_account(db, row)
        if stats is None:
            continue
        trade_count = stats["total_trades"]
        roi_pct = stats["roi_pct"]
        sharpe = stats["sharpe_ratio"]
        entries.append({
            "agent_name": row["agent_name"],
            "model": row.get("model"),
            "account_id": row["id"],
            "account_name": row["name"],
            "trade_count": trade_count,
            "roi_pct": roi_pct,
            "total_pnl": stats["pnl"],
            "sharpe_ratio": sharpe,
            "tier": _compute_tier(trade_count, roi_pct, sharpe),
        })
    entries.sort(key=lambda x: x["roi_pct"], reverse=True)
    feed = db.get_recent_trades_global(limit=10)
    return templates.TemplateResponse("leaderboard.html", {
        "request": request,
        "entries": entries,
        "feed": feed,
    })


@router.get("/u/{agent_name}", response_class=HTMLResponse)
def user_page(agent_name: str, request: Request, db: DB = Depends(get_db)):
    """User profile page."""
    user = db.get_user_by_name(agent_name)
    if user is None:
        raise HTTPException(404, detail="User not found")
    accounts = db.get_user_accounts(user["id"])
    account_list = []
    for acc in accounts:
        trade_count = db.get_trade_count(acc["id"])
        stats = _stats_for_account(db, acc)
        account_list.append({
            **acc,
            "trade_count": trade_count,
            "roi_pct": stats["roi_pct"] if stats else 0.0,
        })
    return templates.TemplateResponse("user.html", {
        "request": request,
        "user": user,
        "accounts": account_list,
    })


def _enrich_positions(positions: list[dict], polymarket) -> list[dict]:
    """Add live_price, current_value, unrealized_pnl to each position."""
    enriched = []
    market_cache: dict[str, object] = {}
    for pos in positions:
        cid = pos["market_condition_id"]
        if cid not in market_cache:
            market_cache[cid] = polymarket.get_market(pos["market_slug"])
        market = market_cache[cid]
        token_id = market.get_token_id(pos["outcome"])
        live_price = polymarket.get_midpoint(token_id)
        current_value = pos["shares"] * live_price
        unrealized_pnl = current_value - pos["total_cost"]
        enriched.append({
            **pos,
            "live_price": live_price,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
        })
    return enriched


@router.get("/a/{account_id}", response_class=HTMLResponse)
def account_page(account_id: int, request: Request, db: DB = Depends(get_db)):
    """Account detail page with stats, live prices, and P&L."""
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(404, detail="Account not found")
    user = db.get_user_by_id(account["user_id"])
    agent_name = user["agent_name"] if user else "unknown"
    polymarket = request.app.state.polymarket

    # Positions with live prices
    raw_positions = db.get_open_positions(account_id)
    positions = _enrich_positions(raw_positions, polymarket)

    # P&L summary
    cash = float(account["cash"])
    positions_value = sum(p["current_value"] for p in positions)
    total_value = cash + positions_value
    starting = float(account["starting_balance"])
    pnl = total_value - starting
    roi_pct = (pnl / starting * 100) if starting else 0.0

    # Stats from trade history
    stats = _stats_for_account(db, account)

    trades = db.get_trades(account_id, limit=50)
    return templates.TemplateResponse("account.html", {
        "request": request,
        "account": account,
        "agent_name": agent_name,
        "positions": positions,
        "trades": trades,
        "total_value": total_value,
        "positions_value": positions_value,
        "pnl": pnl,
        "roi_pct": roi_pct,
        "stats": stats,
    })
