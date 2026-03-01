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

    # Polymarket client: set in production startup; tests override with mock
    if not hasattr(app.state, "polymarket"):
        try:
            from server.adapters.polymarket import create_polymarket_client
            app.state.polymarket = create_polymarket_client()
        except Exception:
            app.state.polymarket = None

    yield

    if hasattr(app.state, "polymarket") and app.state.polymarket is not None:
        try:
            app.state.polymarket.close()
        except Exception:
            pass
    db.close()


app = FastAPI(title="Polymarket Leaderboard", lifespan=lifespan)


def get_db(request: Request) -> DB:
    """Dependency to get DB from app state."""
    return request.app.state.db


# Import and include routers after app is created
from server.routes.auth import router as auth_router  # noqa: E402
from server.routes.accounts import router as accounts_router  # noqa: E402
from server.routes.trading import router as trading_router  # noqa: E402
from server.routes.portfolio import router as portfolio_router  # noqa: E402
from server.routes.orders import router as orders_router  # noqa: E402
from server.routes.leaderboard import router as leaderboard_router  # noqa: E402

app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(trading_router)
app.include_router(portfolio_router)
app.include_router(orders_router)
app.include_router(leaderboard_router)
