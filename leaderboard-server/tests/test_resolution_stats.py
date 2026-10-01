"""Leaderboard stats count market resolutions as closing events."""
from __future__ import annotations

import pytest

from server.db import DB
from server.routes.leaderboard import _stats_for_account


@pytest.fixture
def db():
    d = DB(":memory:")
    d.init_schema()
    yield d
    d.close()


def _buy_and_resolve(db: DB, won: bool) -> dict:
    user = db.create_user("resolver")
    account = db.create_account(user["id"], "default")
    common = dict(account_id=account["id"], market_condition_id="0xabc",
                  market_slug="m", market_question="Q?", outcome="yes")
    db.insert_trade(**common, side="buy", order_type="fok", avg_price=0.5,
                    amount_usd=50.0, shares=100.0, fee_rate_bps=0, fee=0.0,
                    slippage=0.0, levels_filled=1, is_partial=False,
                    book_snapshot_id=None)
    db.debit_cash(account["id"], 50.0)
    pos = db.upsert_position(**common, shares=100.0, avg_entry_price=0.5,
                             total_cost=50.0, realized_pnl=0.0)
    payout = 100.0 if won else 0.0
    db.resolve_position(pos["id"], payout - 50.0, credit=payout)
    return db.get_account(account["id"])


def test_winning_resolution_counts_as_a_win(db):
    account = _buy_and_resolve(db, won=True)
    stats = _stats_for_account(db, None, account)
    assert stats["win_rate"] == 1.0
    assert stats["pnl"] == pytest.approx(50.0)


def test_losing_resolution_counts_as_a_loss(db):
    account = _buy_and_resolve(db, won=False)
    stats = _stats_for_account(db, None, account)
    assert stats["win_rate"] == 0.0
    assert stats["max_drawdown"] > 0
