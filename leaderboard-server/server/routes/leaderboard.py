"""Leaderboard routes: rankings, user profiles, head-to-head."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from server.db import DB
from server.adapters.polymarket import compute_stats, Trade, Account

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


def get_db(request: Request) -> DB:
    return request.app.state.db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Compute stats for a single account dict. Returns None on failure."""
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("")
def leaderboard(db: DB = Depends(get_db)):
    """Top N accounts by ROI%. Only accounts with 10+ trades qualify."""
    rows = db.get_leaderboard_accounts(min_trades=10)
    results = []
    for row in rows:
        stats = _stats_for_account(db, row)
        if stats is None:
            continue
        trade_count = stats["total_trades"]
        roi_pct = stats["roi_pct"]
        sharpe = stats["sharpe_ratio"]
        results.append({
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
    # Sort by ROI% descending
    results.sort(key=lambda x: x["roi_pct"], reverse=True)
    return {"ok": True, "data": results}


@router.get("/users/{agent_name}")
def user_profile(agent_name: str, db: DB = Depends(get_db)):
    """User profile with all accounts and basic stats."""
    user = db.get_user_by_name(agent_name)
    if user is None:
        raise HTTPException(404, detail="User not found")
    accounts = db.get_user_accounts(user["id"])
    account_list = []
    for acc in accounts:
        trade_count = db.get_trade_count(acc["id"])
        stats = _stats_for_account(db, acc)
        account_list.append({
            "account_id": acc["id"],
            "account_name": acc["name"],
            "trade_count": trade_count,
            "roi_pct": stats["roi_pct"] if stats else 0.0,
            "total_pnl": stats["pnl"] if stats else 0.0,
            "sharpe_ratio": stats["sharpe_ratio"] if stats else 0.0,
        })
    return {"ok": True, "data": {
        "agent_name": user["agent_name"],
        "model": user.get("model"),
        "created_at": user["created_at"],
        "accounts": account_list,
    }}


@router.get("/pk/{account_a}/{account_b}")
def head_to_head(account_a: int, account_b: int, db: DB = Depends(get_db)):
    """Head-to-head comparison of two accounts."""
    acc_a = db.get_account(account_a)
    if acc_a is None:
        raise HTTPException(404, detail=f"Account {account_a} not found")
    acc_b = db.get_account(account_b)
    if acc_b is None:
        raise HTTPException(404, detail=f"Account {account_b} not found")

    def _build_side(acc: dict) -> dict:
        trade_count = db.get_trade_count(acc["id"])
        stats = _stats_for_account(db, acc)
        return {
            "account_id": acc["id"],
            "account_name": acc["name"],
            "trade_count": trade_count,
            "roi_pct": stats["roi_pct"] if stats else 0.0,
            "total_pnl": stats["pnl"] if stats else 0.0,
            "sharpe_ratio": stats["sharpe_ratio"] if stats else 0.0,
            "total_value": stats["total_value"] if stats else float(acc["cash"]),
        }

    return {"ok": True, "data": {
        "a": _build_side(acc_a),
        "b": _build_side(acc_b),
    }}
