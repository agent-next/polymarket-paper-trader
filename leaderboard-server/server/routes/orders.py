"""Limit order CRUD routes: place, list, cancel."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from server.adapters.polymarket import InvalidOutcomeError, validate_outcome
from server.auth import get_current_user
from server.db import DB
from server.routes.trading import _err, _get_polymarket, _upstream

router = APIRouter(tags=["orders"])

MAX_PENDING_ORDERS = 100


def get_db(request: Request) -> DB:
    return request.app.state.db


class PlaceOrderRequest(BaseModel):
    market_slug: str
    market_condition_id: str
    outcome: str
    side: str
    amount: float = Field(gt=0, allow_inf_nan=False)
    limit_price: float = Field(allow_inf_nan=False)
    order_type: str = "gtc"
    expires_at: str | None = None

    @field_validator("side")
    @classmethod
    def valid_side(cls, v: str) -> str:
        if v not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        return v

    @field_validator("limit_price")
    @classmethod
    def valid_price(cls, v: float) -> float:
        if not (0 < v < 1):
            raise ValueError("limit_price must be between 0 and 1")
        return v

    @field_validator("expires_at")
    @classmethod
    def valid_expires_at(cls, v: str | None) -> str | None:
        """Normalize to an aware UTC ISO string so it sorts against now(utc)."""
        if v is None:
            return None
        try:
            parsed = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("expires_at must be an ISO 8601 datetime")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    @field_validator("order_type")
    @classmethod
    def valid_order_type(cls, v: str) -> str:
        if v not in ("gtc", "gtd"):
            raise ValueError("order_type must be 'gtc' or 'gtd'")
        return v


@router.post("/accounts/{account_id}/orders")
def place_order(account_id: int, req: PlaceOrderRequest, request: Request,
                user: dict = Depends(get_current_user), db: DB = Depends(get_db)):
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(404, detail="Account not found")
    if account["user_id"] != user["id"]:
        raise HTTPException(403, detail="Not your account")

    if req.order_type == "gtd" and not req.expires_at:
        raise HTTPException(400, detail={"error": "expires_at required for GTD orders", "code": "VALIDATION_ERROR"})

    if len(db.get_pending_orders(account_id)) >= MAX_PENDING_ORDERS:
        _err(f"Too many pending orders (max {MAX_PENDING_ORDERS})", "TOO_MANY_ORDERS")

    # Identity comes from the real market, never from client-supplied ids.
    market = _upstream(_get_polymarket(request), "get_market", req.market_slug)
    try:
        outcome = validate_outcome(req.outcome, market)
    except InvalidOutcomeError as e:
        _err(str(e), "INVALID_OUTCOME")
    if market.closed:
        _err(f"Market '{market.slug}' is closed", "MARKET_CLOSED")

    order = db.create_limit_order(
        account_id=account_id,
        market_slug=market.slug,
        market_condition_id=market.condition_id,
        outcome=outcome,
        side=req.side,
        amount=req.amount,
        limit_price=req.limit_price,
        order_type=req.order_type,
        expires_at=req.expires_at,
    )
    return {"ok": True, "data": order}


@router.get("/accounts/{account_id}/orders")
def list_orders(account_id: int, user: dict = Depends(get_current_user),
                db: DB = Depends(get_db)):
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(404, detail="Account not found")
    if account["user_id"] != user["id"]:
        raise HTTPException(403, detail="Not your account")

    orders = db.get_pending_orders(account_id)
    return {"ok": True, "data": orders}


@router.delete("/accounts/{account_id}/orders/{order_id}")
def cancel_order(account_id: int, order_id: int,
                 user: dict = Depends(get_current_user), db: DB = Depends(get_db)):
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(404, detail="Account not found")
    if account["user_id"] != user["id"]:
        raise HTTPException(403, detail="Not your account")

    cancelled = db.cancel_order(order_id, account_id)
    if cancelled is None:
        raise HTTPException(404, detail="Order not found or already processed")
    return {"ok": True, "data": cancelled}
