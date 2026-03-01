"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.config import DATABASE_URL
from server.db import DB


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup, close on shutdown."""
    db = DB(DATABASE_URL)  # pragma: no cover (lifespan)
    db.init_schema()
    app.state.db = db

    # Polymarket client: set in production startup; tests override with mock
    if not hasattr(app.state, "polymarket"):  # pragma: no cover
        try:
            from server.adapters.polymarket import create_polymarket_client
            app.state.polymarket = create_polymarket_client()
        except Exception:
            app.state.polymarket = None

    # Background scheduler: tests pre-set scheduler=None to skip
    if not hasattr(app.state, "scheduler"):  # pragma: no cover
        from apscheduler.schedulers.background import BackgroundScheduler
        from server.config import CHECK_ORDERS_INTERVAL, AUTO_RESOLVE_INTERVAL
        from server.jobs.check_orders import check_orders_job
        from server.jobs.auto_resolve import auto_resolve_job

        if app.state.polymarket is not None:
            scheduler = BackgroundScheduler()
            scheduler.add_job(
                check_orders_job, "interval",
                seconds=CHECK_ORDERS_INTERVAL,
                args=[db, app.state.polymarket],
            )
            scheduler.add_job(
                auto_resolve_job, "interval",
                seconds=AUTO_RESOLVE_INTERVAL,
                args=[db, app.state.polymarket],
            )
            scheduler.start()
            app.state.scheduler = scheduler
        else:
            app.state.scheduler = None

    yield

    if hasattr(app.state, "scheduler") and app.state.scheduler is not None:  # pragma: no cover
        app.state.scheduler.shutdown(wait=False)
    if hasattr(app.state, "polymarket") and app.state.polymarket is not None:
        try:
            app.state.polymarket.close()
        except Exception:  # pragma: no cover
            pass
    db.close()


app = FastAPI(title="Polymarket Leaderboard", lifespan=lifespan)


# Import and include routers after app is created
from server.routes.auth import router as auth_router  # noqa: E402
from server.routes.accounts import router as accounts_router  # noqa: E402
from server.routes.trading import router as trading_router  # noqa: E402
from server.routes.portfolio import router as portfolio_router  # noqa: E402
from server.routes.orders import router as orders_router  # noqa: E402
from server.routes.leaderboard import router as leaderboard_router  # noqa: E402
from server.routes.website import router as website_router  # noqa: E402

app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(trading_router)
app.include_router(portfolio_router)
app.include_router(orders_router)
app.include_router(leaderboard_router)
app.include_router(website_router)


@app.get("/health")
def health():
    return {"status": "ok"}
