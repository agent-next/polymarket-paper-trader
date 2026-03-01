"""Trading routes: server-side buy/sell execution.

The server fetches real order books from Polymarket and executes trades
using pm_trader pure functions.  Mirrors pm_trader/engine.py flow exactly.
"""
from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from server.auth import get_current_user
from server.db import DB
from server.adapters.polymarket import (
    simulate_buy_fill,
    simulate_sell_fill,
    validate_outcome,
    Market,
    OrderRejectedError,
    InsufficientBalanceError,
    MarketClosedError,
    NoPositionError,
    InvalidOutcomeError,
)

router = APIRouter(prefix="/trade", tags=["trading"])

MIN_ORDER_USD = 1.00


def get_db(request: Request) -> DB:
    return request.app.state.db


def _get_polymarket(request: Request):
    return request.app.state.polymarket


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _err(error: str, code: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail={"error": error, "code": code})


def _validate_account_ownership(account: dict, user: dict) -> None:
    if account["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your account")


# -- Request models --

class BuyRequest(BaseModel):
    account_id: int
    market_slug: str
    outcome: str
    amount_usd: float
    order_type: str = "fok"

    @field_validator("order_type")
    @classmethod
    def valid_order_type(cls, v: str) -> str:
        if v not in ("fok", "fak"):
            raise ValueError("order_type must be 'fok' or 'fak'")
        return v

    @field_validator("amount_usd")
    @classmethod
    def positive_amount(cls, v: float) -> float:
        if v < MIN_ORDER_USD:
            raise ValueError(f"Minimum order size is ${MIN_ORDER_USD:.2f}")
        return v


class SellRequest(BaseModel):
    account_id: int
    market_slug: str
    outcome: str
    shares: float
    order_type: str = "fok"

    @field_validator("order_type")
    @classmethod
    def valid_order_type(cls, v: str) -> str:
        if v not in ("fok", "fak"):
            raise ValueError("order_type must be 'fok' or 'fak'")
        return v

    @field_validator("shares")
    @classmethod
    def positive_shares(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("shares must be positive")
        return v


# -- Position update helpers (mirrors engine.py exactly) --

def _update_position_after_buy(
    db: DB,
    *,
    account_id: int,
    market: Market,
    outcome: str,
    new_shares: float,
    cost: float,
    avg_fill_price: float,
) -> dict:
    existing = db.get_position(account_id, market.condition_id, outcome)
    if existing and existing["shares"] > 0:
        total_shares = existing["shares"] + new_shares
        total_cost = existing["total_cost"] + cost
        avg_entry = total_cost / total_shares if total_shares > 0 else 0.0
    else:
        total_shares = new_shares
        total_cost = cost
        avg_entry = avg_fill_price

    return db.upsert_position(
        account_id=account_id,
        market_condition_id=market.condition_id,
        market_slug=market.slug,
        market_question=market.question,
        outcome=outcome,
        shares=total_shares,
        avg_entry_price=avg_entry,
        total_cost=total_cost,
        realized_pnl=existing["realized_pnl"] if existing else 0.0,
    )


def _update_position_after_sell(
    db: DB,
    *,
    account_id: int,
    market: Market,
    outcome: str,
    sold_shares: float,
    proceeds: float,
) -> dict | None:
    existing = db.get_position(account_id, market.condition_id, outcome)
    if existing is None:
        return None

    remaining_shares = existing["shares"] - sold_shares
    cost_of_sold = (
        existing["avg_entry_price"] * sold_shares
        if existing["shares"] > 0
        else 0.0
    )
    realized_pnl = existing["realized_pnl"] + (proceeds - cost_of_sold)
    remaining_cost = existing["total_cost"] - cost_of_sold

    return db.upsert_position(
        account_id=account_id,
        market_condition_id=market.condition_id,
        market_slug=market.slug,
        market_question=market.question,
        outcome=outcome,
        shares=max(remaining_shares, 0.0),
        avg_entry_price=existing["avg_entry_price"],
        total_cost=max(remaining_cost, 0.0),
        realized_pnl=realized_pnl,
    )


# -- Routes --

@router.post("/buy")
def buy(
    req: BuyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: DB = Depends(get_db),
):
    """Execute a buy order against the real Polymarket order book."""
    polymarket = _get_polymarket(request)

    # 1. Validate account ownership
    account = db.get_account(req.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _validate_account_ownership(account, user)

    # 2. Fetch market
    market = polymarket.get_market(req.market_slug)

    # 3. Validate outcome
    try:
        outcome = validate_outcome(req.outcome, market)
    except InvalidOutcomeError as e:
        _err(str(e), "INVALID_OUTCOME")

    # 4. Check market not closed
    if market.closed:
        _err(f"Market '{market.slug}' is closed", "MARKET_CLOSED")

    # 5. Get token_id
    token_id = market.get_token_id(outcome)

    # 6. Fetch book (NEVER cached)
    book = polymarket.get_order_book(token_id)

    # 7. Fetch fee rate
    fee_rate_bps = polymarket.get_fee_rate(token_id)

    # 8. Simulate fill
    fill = simulate_buy_fill(book, req.amount_usd, fee_rate_bps, req.order_type)

    # 9. Check fill
    if not fill.filled and not fill.is_partial:
        _err("Insufficient liquidity in order book (FOK rejected)", "ORDER_REJECTED")

    # 10. Check cash
    total_outflow = fill.total_cost + fill.fee
    if total_outflow > account["cash"]:
        _err(
            f"Insufficient balance: need ${total_outflow:.2f}, have ${account['cash']:.2f}",
            "INSUFFICIENT_BALANCE",
        )

    # 11. Save book snapshot
    book_snapshot = dataclasses.asdict(book)
    snapshot_id = db.save_book_snapshot(token_id, book_snapshot)

    # 12. Update cash
    new_cash = account["cash"] - total_outflow
    db.update_cash(req.account_id, new_cash)

    # 13. Insert trade
    trade = db.insert_trade(
        account_id=req.account_id,
        market_condition_id=market.condition_id,
        market_slug=market.slug,
        market_question=market.question,
        outcome=outcome,
        side="buy",
        order_type=req.order_type,
        avg_price=fill.avg_price,
        amount_usd=fill.total_cost,
        shares=fill.total_shares,
        fee_rate_bps=fee_rate_bps,
        fee=fill.fee,
        slippage=fill.slippage_bps,
        levels_filled=fill.levels_filled,
        is_partial=fill.is_partial,
        book_snapshot_id=snapshot_id,
    )

    # 14. Update position
    _update_position_after_buy(
        db,
        account_id=req.account_id,
        market=market,
        outcome=outcome,
        new_shares=fill.total_shares,
        cost=fill.total_cost + fill.fee,
        avg_fill_price=fill.avg_price,
    )

    # 15. Return result
    updated_account = db.get_account(req.account_id)
    return _ok({"trade": trade, "account": updated_account})


@router.post("/sell")
def sell(
    req: SellRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: DB = Depends(get_db),
):
    """Execute a sell order against the real Polymarket order book."""
    polymarket = _get_polymarket(request)

    # 1. Validate account ownership
    account = db.get_account(req.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    _validate_account_ownership(account, user)

    # 2. Fetch market and validate outcome
    market = polymarket.get_market(req.market_slug)
    try:
        outcome = validate_outcome(req.outcome, market)
    except InvalidOutcomeError as e:
        _err(str(e), "INVALID_OUTCOME")

    # 3. Check position exists and has enough shares
    position = db.get_position(req.account_id, market.condition_id, outcome)
    if position is None or position["shares"] <= 0:
        _err(f"No position in '{market.slug}' {outcome}", "NO_POSITION")

    if req.shares > position["shares"]:
        _err(
            f"Cannot sell {req.shares:.4f} shares, only hold {position['shares']:.4f}",
            "ORDER_REJECTED",
        )

    # 4. Check market not closed
    if market.closed:
        _err(f"Market '{market.slug}' is closed", "MARKET_CLOSED")

    # 5. Fetch book and fee rate
    token_id = market.get_token_id(outcome)
    book = polymarket.get_order_book(token_id)
    fee_rate_bps = polymarket.get_fee_rate(token_id)

    # 6. Simulate fill
    fill = simulate_sell_fill(book, req.shares, fee_rate_bps, req.order_type)

    # 7. Check fill
    if not fill.filled and not fill.is_partial:
        _err("Insufficient liquidity in order book (FOK rejected)", "ORDER_REJECTED")

    # 8. Net proceeds
    net_proceeds = fill.total_cost - fill.fee

    # 9. Save book snapshot
    book_snapshot = dataclasses.asdict(book)
    snapshot_id = db.save_book_snapshot(token_id, book_snapshot)

    # 10. Update cash
    new_cash = account["cash"] + net_proceeds
    db.update_cash(req.account_id, new_cash)

    # 11. Insert trade
    trade = db.insert_trade(
        account_id=req.account_id,
        market_condition_id=market.condition_id,
        market_slug=market.slug,
        market_question=market.question,
        outcome=outcome,
        side="sell",
        order_type=req.order_type,
        avg_price=fill.avg_price,
        amount_usd=fill.total_cost,
        shares=fill.total_shares,
        fee_rate_bps=fee_rate_bps,
        fee=fill.fee,
        slippage=fill.slippage_bps,
        levels_filled=fill.levels_filled,
        is_partial=fill.is_partial,
        book_snapshot_id=snapshot_id,
    )

    # 12. Update position
    _update_position_after_sell(
        db,
        account_id=req.account_id,
        market=market,
        outcome=outcome,
        sold_shares=fill.total_shares,
        proceeds=net_proceeds,
    )

    # 13. Return result
    updated_account = db.get_account(req.account_id)
    return _ok({"trade": trade, "account": updated_account})
