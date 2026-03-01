"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from server.db import DB


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup, close on shutdown."""
    db = DB(":memory:")  # Will be configured via env in production
    db.init_schema()
    app.state.db = db
    yield
    db.close()


app = FastAPI(title="Polymarket Leaderboard", lifespan=lifespan)


def get_db(request: Request) -> DB:
    """Dependency to get DB from app state."""
    return request.app.state.db


# Import and include routers after app is created
from server.routes.auth import router as auth_router  # noqa: E402
from server.routes.accounts import router as accounts_router  # noqa: E402

app.include_router(auth_router)
app.include_router(accounts_router)
