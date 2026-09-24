"""Account routes: create, list, get."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from server.auth import get_current_user
from server.db import DB

router = APIRouter(prefix="/accounts", tags=["accounts"])


def get_db(request: Request) -> DB:
    return request.app.state.db


class CreateAccountRequest(BaseModel):
    name: str = "default"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty")
        return v.strip()


@router.post("")
def create_account(req: CreateAccountRequest, user: dict = Depends(get_current_user),
                   db: DB = Depends(get_db)):
    try:
        account = db.create_account(user["id"], req.name)
        return {"ok": True, "data": account}
    except Exception:
        raise HTTPException(409, detail="Account name already exists for this user")


@router.get("")
def list_accounts(user: dict = Depends(get_current_user), db: DB = Depends(get_db)):
    accounts = db.get_user_accounts(user["id"])
    return {"ok": True, "data": accounts}


@router.get("/{account_id}")
def get_account(account_id: int, user: dict = Depends(get_current_user),
                db: DB = Depends(get_db)):
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(404, detail="Account not found")
    if account["user_id"] != user["id"]:
        raise HTTPException(403, detail="Not your account")
    return {"ok": True, "data": account}
