"""Auth dependency for FastAPI routes."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Header, Request

from server.db import DB


def get_db(request: Request) -> DB:
    return request.app.state.db


def get_current_user(authorization: str | None = Header(default=None), db: DB = Depends(get_db)) -> dict:
    if authorization is None:
        raise HTTPException(401, detail="Missing authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, detail="Invalid authorization header")
    api_key = authorization[7:]
    user = db.get_user_by_api_key(api_key)
    if user is None:
        raise HTTPException(401, detail="Invalid API key")
    return user
