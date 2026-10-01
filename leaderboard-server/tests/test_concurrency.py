"""Concurrency: one account's trades must never double-spend or double-sell."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.db import DB

from tests.conftest import (
    E2E_BOOK,
    E2E_MARKET,
    MockPolymarketClient,
    _buy,
    _create_account,
    _register,
    _sell,
)

THREADS = 8


class _SlowBook(MockPolymarketClient):
    """Widen the window between the route's account read and its writes."""

    def get_order_book(self, token_id):
        time.sleep(0.1)
        return super().get_order_book(token_id)


@pytest.fixture
def slow_client():
    app.state.polymarket = _SlowBook(market=E2E_MARKET, book=E2E_BOOK, midpoint=0.70)
    app.state.scheduler = None
    with TestClient(app) as c:
        yield c


def _run_parallel(fn):
    with ThreadPoolExecutor(THREADS) as pool:
        return [f.result() for f in [pool.submit(fn) for _ in range(THREADS)]]


class TestTradeConcurrency:
    def test_parallel_buys_cannot_double_spend(self, slow_client):
        user = _register(slow_client)
        account = _create_account(slow_client, user["api_key"])
        db = slow_client.app.state.db
        db.update_cash(account["id"], 15.0)

        results = _run_parallel(
            lambda: _buy(slow_client, user["api_key"], account["id"], amount=10.0,
                         slug="e2e-test-market")
        )

        assert sorted(r.status_code for r in results) == [200] + [400] * (THREADS - 1)
        assert db.get_account(account["id"])["cash"] == pytest.approx(5.0)
        assert db.get_trade_count(account["id"]) == 1
        position = db.get_open_positions(account["id"])[0]
        assert position["total_cost"] == pytest.approx(10.0)

    def test_parallel_sells_cannot_oversell(self, slow_client):
        user = _register(slow_client)
        account = _create_account(slow_client, user["api_key"])
        db = slow_client.app.state.db
        assert _buy(slow_client, user["api_key"], account["id"], amount=10.0,
                    slug="e2e-test-market").status_code == 200
        held = db.get_open_positions(account["id"])[0]["shares"]
        cash_before = db.get_account(account["id"])["cash"]

        results = _run_parallel(
            lambda: _sell(slow_client, user["api_key"], account["id"], held,
                          slug="e2e-test-market")
        )

        assert sorted(r.status_code for r in results) == [200] + [400] * (THREADS - 1)
        assert db.get_trade_count(account["id"]) == 2
        proceeds = db.get_account(account["id"])["cash"] - cash_before
        assert proceeds == pytest.approx(held * 0.69, rel=0.02)
        assert db.get_position(account["id"], "0xe2e", "yes")["shares"] == pytest.approx(0.0)

    def test_buy_debit_refusal_rolls_back(self, slow_client):
        """The authoritative debit failing (lost race) leaves no snapshot/trade/position."""
        user = _register(slow_client)
        account = _create_account(slow_client, user["api_key"])
        db = slow_client.app.state.db
        with patch.object(DB, "debit_cash", return_value=False):
            resp = _buy(slow_client, user["api_key"], account["id"], amount=10.0,
                        slug="e2e-test-market")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INSUFFICIENT_BALANCE"
        assert db.get_account(account["id"])["cash"] == 10000.0
        assert db.get_trade_count(account["id"]) == 0
        assert db._conn.execute("SELECT COUNT(*) FROM book_snapshots").fetchone()[0] == 0

    def test_sell_position_gone_is_rejected(self, slow_client):
        user = _register(slow_client)
        account = _create_account(slow_client, user["api_key"])
        db = slow_client.app.state.db
        assert _buy(slow_client, user["api_key"], account["id"], amount=10.0,
                    slug="e2e-test-market").status_code == 200
        held = db.get_open_positions(account["id"])[0]["shares"]
        real_get = DB.get_position
        calls = []

        def vanishing(self, *a):
            calls.append(a)
            return real_get(self, *a) if len(calls) == 1 else None

        with patch.object(DB, "get_position", vanishing):
            resp = _sell(slow_client, user["api_key"], account["id"], held,
                         slug="e2e-test-market")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "ORDER_REJECTED"
        assert db.get_trade_count(account["id"]) == 1


class TestDbAtomics:
    @pytest.fixture
    def db(self):
        _db = DB(":memory:")
        _db.init_schema()
        yield _db
        _db.close()

    @pytest.fixture
    def account(self, db):
        return db.create_account(db.create_user("atomic")["id"], "default")

    def test_debit_cash_is_conditional(self, db, account):
        assert db.debit_cash(account["id"], 4000.0) is True
        assert db.debit_cash(account["id"], 7000.0) is False
        assert db.get_account(account["id"])["cash"] == 6000.0

    def test_credit_cash_is_relative(self, db, account):
        db.credit_cash(account["id"], 25.5)
        assert db.get_account(account["id"])["cash"] == pytest.approx(10025.5)

    def test_transaction_commits(self, db, account):
        with db.transaction():
            db.debit_cash(account["id"], 100.0)
            db.credit_cash(account["id"], 1.0)
        assert db.get_account(account["id"])["cash"] == 9901.0

    def test_transaction_rolls_back_on_error(self, db, account):
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.debit_cash(account["id"], 100.0)
                raise RuntimeError("boom")
        assert db.get_account(account["id"])["cash"] == 10000.0
        db.credit_cash(account["id"], 1.0)  # connection still usable, commits normally
        assert db.get_account(account["id"])["cash"] == 10001.0

    def test_fill_order_only_once(self, db, account):
        order = db.create_limit_order(
            account_id=account["id"], market_slug="m", market_condition_id="c",
            outcome="yes", side="buy", amount=5, limit_price=0.5,
        )
        assert db.fill_order(order["id"])["status"] == "filled"
        assert db.fill_order(order["id"]) is None
        db.create_limit_order(
            account_id=account["id"], market_slug="m", market_condition_id="c",
            outcome="yes", side="buy", amount=5, limit_price=0.5,
        )
        cancelled = db.get_pending_orders(account["id"])[0]
        db.cancel_order(cancelled["id"], account["id"])
        assert db.fill_order(cancelled["id"]) is None

    def test_failing_transaction_cannot_discard_other_threads_write(self, db):
        # Pause the writer after its statement ran but before its _commit():
        # that is the window in which another thread's rollback used to
        # discard the executed-but-uncommitted row.
        executed, resume, b_done = threading.Event(), threading.Event(), threading.Event()
        real_commit = db._commit

        def pausing_commit():
            if threading.current_thread().name == "writer":
                executed.set()
                resume.wait(5)
            real_commit()

        db._commit = pausing_commit

        def writer():
            db.create_user("racer")

        def failing_tx():
            with pytest.raises(RuntimeError):
                with db.transaction():
                    raise RuntimeError("boom")
            b_done.set()

        a = threading.Thread(target=writer, name="writer")
        a.start()
        assert executed.wait(5)
        b = threading.Thread(target=failing_tx)
        b.start()
        assert b_done.wait(5)  # B's rollback lands inside A's window
        resume.set()
        a.join(5)
        b.join(5)
        del db._commit
        assert db.get_user_by_name("racer") is not None

    def test_resolve_position_rolls_back_when_credit_fails(self, db):
        user = db.create_user("resolver")
        account = db.create_account(user["id"], "default")
        pos = db.upsert_position(
            account_id=account["id"], market_condition_id="0xabc",
            market_slug="test", market_question="T?", outcome="yes",
            shares=10.0, avg_entry_price=0.5, total_cost=5.0, realized_pnl=0.0,
        )
        real_execute = db._execute

        def failing_execute(sql, params=()):
            if sql.startswith("UPDATE accounts"):
                raise RuntimeError("credit failed")
            return real_execute(sql, params)

        db._execute = failing_execute
        with pytest.raises(RuntimeError):
            db.resolve_position(pos["id"], 5.0, credit=10.0)
        del db._execute
        after = db.get_position(account["id"], "0xabc", "yes")
        assert not after["is_resolved"]
        assert db.get_account(account["id"])["cash"] == account["cash"]
        # A retry still pays exactly once.
        assert db.resolve_position(pos["id"], 5.0, credit=10.0) is not None
        assert db.get_account(account["id"])["cash"] == account["cash"] + 10.0

    def test_nested_transaction_joins_the_outer_one(self, db):
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.create_user("outer")
                with db.transaction():
                    db.create_user("inner")
                raise RuntimeError("roll back both")
        assert db.get_user_by_name("outer") is None
        assert db.get_user_by_name("inner") is None
