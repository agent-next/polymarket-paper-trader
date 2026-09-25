"""Auth routes: registration and profile updates."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from server.auth import get_current_user
from server.db import DB

router = APIRouter(prefix="/auth", tags=["auth"])


def get_db(request: Request) -> DB:
    return request.app.state.db


class RegisterRequest(BaseModel):
    agent_name: str
    model: str | None = None

    @field_validator("agent_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("agent_name cannot be empty")
        return v.strip()


class UpdateModelRequest(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def model_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model cannot be empty")
        return v.strip()


@router.post("/register")
def register(req: RegisterRequest, db: DB = Depends(get_db)):
    try:
        user = db.create_user(req.agent_name, model=req.model)
        return {"ok": True, "data": {
            "user_id": user["id"],
            "api_key": user["api_key"],
            "agent_name": user["agent_name"],
            "model": user["model"],
        }}
    except Exception:
        raise HTTPException(409, detail="agent_name already taken")


@router.patch("/model")
def update_model(
    req: UpdateModelRequest,
    user: dict = Depends(get_current_user),
    db: DB = Depends(get_db),
):
    updated = db.update_model(user["id"], req.model)
    return {"ok": True, "data": {"model": updated["model"]}}
