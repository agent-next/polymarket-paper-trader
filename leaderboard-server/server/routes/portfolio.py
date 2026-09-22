"""Read-only portfolio routes: positions, balance, history, stats."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from server.auth import get_current_user
from server.db import DB
from server.adapters.polymarket import compute_stats, Trade, Account


router = APIRouter(prefix="/accounts", tags=["portfolio"])


def get_db(request: Request) -> DB:
    return request.app.state.db


def _get_polymarket(request: Request):
    return request.app.state.polymarket


def _ok(data: dict | list) -> dict:
    return {"ok": True, "data": data}


def _validate_account_ownership(account: dict, user: dict) -> None:
    if account["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your account")


def _get_account_or_404(db: DB, account_id: int, user: dict) -> dict:
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _validate_account_ownership(account, user)
    return account


# -- Dataclass converters for compute_stats --

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


# -- Portfolio: enrich positions with live prices --

def _enrich_positions(positions: list[dict], polymarket) -> list[dict]:
    """Add live_price, current_value, unrealized_pnl to each position."""
    market_cache: dict[str, object] = {}
    enriched = []
    for pos in positions:
        cid = pos["market_condition_id"]
        outcome = pos["outcome"]

        # Cache market lookup within request
        if cid not in market_cache:
            market_cache[cid] = polymarket.get_market(pos["market_slug"])
        market = market_cache[cid]

        token_id = market.get_token_id(outcome)
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


# -- Routes --

@router.get("/{account_id}/portfolio")
def get_portfolio(
    account_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: DB = Depends(get_db),
):
    """Open positions with live prices."""
    account = _get_account_or_404(db, account_id, user)
    polymarket = _get_polymarket(request)

    positions = db.get_open_positions(account_id)
    enriched = _enrich_positions(positions, polymarket)
    return _ok(enriched)


@router.get("/{account_id}/balance")
def get_balance(
    account_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: DB = Depends(get_db),
):
    """Cash + total portfolio value + P&L."""
    account = _get_account_or_404(db, account_id, user)
    polymarket = _get_polymarket(request)

    positions = db.get_open_positions(account_id)
    enriched = _enrich_positions(positions, polymarket)

    cash = float(account["cash"])
    positions_value = sum(p["current_value"] for p in enriched)
    total_value = cash + positions_value
    starting_balance = float(account["starting_balance"])
    pnl = total_value - starting_balance
    roi_pct = (pnl / starting_balance * 100) if starting_balance else 0.0

    return _ok({
        "cash": cash,
        "positions_value": positions_value,
        "total_value": total_value,
        "starting_balance": starting_balance,
        "pnl": pnl,
        "roi_pct": roi_pct,
    })


@router.get("/{account_id}/history")
def get_history(
    account_id: int,
    user: dict = Depends(get_current_user),
    db: DB = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=1000),
):
    """Trade log, newest first."""
    _get_account_or_404(db, account_id, user)
    trades = db.get_trades(account_id, limit=limit)
    return _ok(trades)


@router.get("/{account_id}/stats")
def get_stats(
    account_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: DB = Depends(get_db),
):
    """Analytics stats computed from trade history."""
    account = _get_account_or_404(db, account_id, user)
    polymarket = _get_polymarket(request)

    # Get all trades (no limit) for stats
    trade_dicts = db.get_trades(account_id, limit=10000)
    positions = db.get_open_positions(account_id)
    enriched = _enrich_positions(positions, polymarket)
    positions_value = sum(p["current_value"] for p in enriched)

    # Convert to dataclass instances for compute_stats
    trade_objs = [_dict_to_trade(t) for t in trade_dicts]
    account_obj = _dict_to_account(account)

    stats = compute_stats(trade_objs, account_obj, positions_value=positions_value)
    return _ok(stats)
