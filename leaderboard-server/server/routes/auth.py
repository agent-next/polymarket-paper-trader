"""Auth routes: registration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from server.db import DB

router = APIRouter(prefix="/auth", tags=["auth"])


def get_db(request: Request) -> DB:
    return request.app.state.db


class RegisterRequest(BaseModel):
    agent_name: str

    @field_validator("agent_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("agent_name cannot be empty")
        return v.strip()


@router.post("/register")
def register(req: RegisterRequest, db: DB = Depends(get_db)):
    try:
        user = db.create_user(req.agent_name)
        return {"ok": True, "data": {
            "user_id": user["id"],
            "api_key": user["api_key"],
            "agent_name": user["agent_name"],
        }}
    except Exception:
        raise HTTPException(409, detail="agent_name already taken")
