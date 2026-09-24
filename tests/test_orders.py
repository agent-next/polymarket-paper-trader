"""Tests for limit order management."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pm_trader.orders import (
    _migrate_orders_schema_if_needed,
    cancel_all_orders,
    cancel_order,
    create_order,
    expire_orders,
    get_pending_orders,
    get_reserved_buy_notional,
    init_orders_schema,
    mark_filled,
    mark_partially_filled,
    reject_order,
    should_fill,
    LimitOrder,
)


@pytest.fixture
def conn():
    """In-memory SQLite connection with orders schema."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_orders_schema(c)
    return c


def _create(conn, **overrides):
    defaults = dict(
        market_slug="test-market",
        market_condition_id="0xabc",
        outcome="yes",
        side="buy",
        amount=100.0,
        limit_price=0.55,
        order_type="gtc",
        expires_at=None,
    )
    defaults.update(overrides)
    return create_order(conn, **defaults)


class TestCreateOrder:
    def test_creates_pending_order(self, conn):
        order = _create(conn)
        assert order.id == 1
        assert order.status == "pending"
        assert order.market_slug == "test-market"
        assert order.limit_price == 0.55
        assert order.order_type == "gtc"
        assert order.remaining_amount == 100.0

    def test_auto_increments_id(self, conn):
        o1 = _create(conn)
        o2 = _create(conn)
        assert o2.id == o1.id + 1

    def test_gtd_with_expiry(self, conn):
        expires = "2026-03-01T00:00:00Z"
        order = _create(conn, order_type="gtd", expires_at=expires)
        assert order.order_type == "gtd"
        # Z is normalized to +00:00 for consistent TEXT comparison
        assert order.expires_at == "2026-03-01T00:00:00+00:00"

    def test_gtd_z_and_plus00_are_equivalent(self, conn):
        """Bug #5: 'Z' and '+00:00' must be treated as the same instant."""
        from pm_trader.orders import expire_orders
        from datetime import datetime, timezone
        # Create an order with Z-suffix that has already expired
        order = _create(
            conn, order_type="gtd", expires_at="2020-01-01T00:00:00Z",
        )
        expired = expire_orders(conn)
        assert len(expired) == 1
        assert expired[0].id == order.id


class TestGetPendingOrders:
    def test_empty(self, conn):
        assert get_pending_orders(conn) == []

    def test_returns_pending_only(self, conn):
        _create(conn)
        _create(conn)
        cancel_order(conn, 1)
        pending = get_pending_orders(conn)
        assert len(pending) == 1
        assert pending[0].id == 2

    def test_includes_partially_filled(self, conn):
        _create(conn, amount=120.0)
        updated = mark_partially_filled(conn, 1, 40.0)
        assert updated.status == "partially_filled"
        assert updated.remaining_amount == 40.0
        pending = get_pending_orders(conn)
        assert len(pending) == 1
        assert pending[0].id == 1
        assert pending[0].status == "partially_filled"
        assert pending[0].remaining_amount == 40.0


class TestReservedBuyNotional:
    def test_empty_table_returns_zero(self, conn):
        assert get_reserved_buy_notional(conn) == 0.0

    def test_sums_open_buys(self, conn):
        """pending 50 + partially_filled 30 -> 80.0 reserved."""
        _create(conn, amount=50.0)
        _create(conn, amount=100.0)
        mark_partially_filled(conn, 2, 30.0)
        assert get_reserved_buy_notional(conn) == pytest.approx(80.0)

    def test_ignores_terminal_and_sells(self, conn):
        """filled/cancelled/expired/rejected buys and open sells reserve 0."""
        _create(conn, amount=10.0)
        cancel_order(conn, 1)
        _create(conn, amount=20.0)
        mark_filled(conn, 2)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _create(conn, amount=30.0, order_type="gtd", expires_at=past)
        expire_orders(conn)
        _create(conn, amount=40.0)
        reject_order(conn, 4)
        _create(conn, amount=50.0, side="sell")  # open sell: needs shares, not cash
        assert get_reserved_buy_notional(conn) == 0.0


class TestCancelOrder:
    def test_cancel_pending(self, conn):
        _create(conn)
        order = cancel_order(conn, 1)
        assert order.status == "cancelled"

    def test_cancel_nonexistent(self, conn):
        assert cancel_order(conn, 999) is None

    def test_cancel_already_cancelled(self, conn):
        _create(conn)
        cancel_order(conn, 1)
        assert cancel_order(conn, 1) is None

    def test_cancel_partially_filled(self, conn):
        _create(conn)
        mark_partially_filled(conn, 1, 40.0)
        order = cancel_order(conn, 1)
        assert order.status == "cancelled"


class TestExpireOrders:
    def test_expires_past_gtd(self, conn):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _create(conn, order_type="gtd", expires_at=past)
        expired = expire_orders(conn)
        assert len(expired) == 1

    def test_does_not_expire_future_gtd(self, conn):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _create(conn, order_type="gtd", expires_at=future)
        expired = expire_orders(conn)
        assert len(expired) == 0

    def test_does_not_expire_gtc(self, conn):
        _create(conn, order_type="gtc")
        expired = expire_orders(conn)
        assert len(expired) == 0


class TestCancelAllOrders:
    def test_cancel_all_empty(self, conn):
        result = cancel_all_orders(conn)
        assert result == []

    def test_cancel_all_cancels_pending(self, conn):
        _create(conn)
        _create(conn)
        _create(conn)
        cancelled = cancel_all_orders(conn)
        assert len(cancelled) == 3
        assert all(o.status == "cancelled" for o in cancelled)
        pending = get_pending_orders(conn)
        assert len(pending) == 0

    def test_cancel_all_skips_non_pending(self, conn):
        _create(conn)
        _create(conn)
        cancel_order(conn, 1)  # manually cancel #1
        cancelled = cancel_all_orders(conn)
        assert len(cancelled) == 1  # only #2 was pending
        assert cancelled[0].id == 2
        assert cancelled[0].status == "cancelled"


class TestShouldFill:
    def test_buy_at_limit(self):
        order = LimitOrder(
            id=1, market_slug="m", market_condition_id="0x1",
            outcome="yes", side="buy", amount=100, remaining_amount=100,
            limit_price=0.55,
            order_type="gtc", expires_at=None, status="pending",
            created_at="", filled_at=None,
        )
        assert should_fill(order, 0.55) is True
        assert should_fill(order, 0.50) is True
        assert should_fill(order, 0.60) is False

    def test_sell_at_limit(self):
        order = LimitOrder(
            id=1, market_slug="m", market_condition_id="0x1",
            outcome="yes", side="sell", amount=50, remaining_amount=50,
            limit_price=0.70,
            order_type="gtc", expires_at=None, status="pending",
            created_at="", filled_at=None,
        )
        assert should_fill(order, 0.70) is True
        assert should_fill(order, 0.80) is True
        assert should_fill(order, 0.60) is False


class TestOrdersMigration:
    def test_noop_when_table_missing(self):
        """A fresh DB (no limit_orders table yet) needs no migration."""
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        _migrate_orders_schema_if_needed(c)
        tables = [
            r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        assert "limit_orders" not in tables
        assert "limit_orders_old" not in tables

    def test_migrates_legacy_schema_to_remaining_amount(self):
        """A pre-0.3.1 table is rebuilt: open orders keep their full size,
        terminal orders are zeroed, ids and timestamps survive."""
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript(
            """\
            CREATE TABLE limit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                market_condition_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (length(outcome) > 0),
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                amount REAL NOT NULL,
                limit_price REAL NOT NULL,
                order_type TEXT NOT NULL CHECK (order_type IN ('gtc', 'gtd')),
                expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'filled', 'cancelled', 'expired', 'rejected')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                filled_at TEXT
            );
            """
        )
        c.execute(
            """\
            INSERT INTO limit_orders (
                id, market_slug, market_condition_id, outcome, side,
                amount, limit_price, order_type, status, created_at
            ) VALUES (1, 'm', '0x1', 'yes', 'buy', 100.0, 0.55, 'gtc', 'pending', '2026-01-01')
            """
        )
        c.execute(
            """\
            INSERT INTO limit_orders (
                id, market_slug, market_condition_id, outcome, side,
                amount, limit_price, order_type, status, created_at, filled_at
            ) VALUES (2, 'm', '0x1', 'yes', 'buy', 50.0, 0.60, 'gtc', 'filled', '2026-01-02', '2026-01-03')
            """
        )
        c.commit()

        _migrate_orders_schema_if_needed(c)

        names = {
            r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "limit_orders_old" not in names

        pending = c.execute("SELECT * FROM limit_orders WHERE id = 1").fetchone()
        assert pending["status"] == "pending"
        assert pending["remaining_amount"] == 100.0
        assert pending["created_at"] == "2026-01-01"

        filled = c.execute("SELECT * FROM limit_orders WHERE id = 2").fetchone()
        assert filled["status"] == "filled"
        assert filled["remaining_amount"] == 0.0
        assert filled["filled_at"] == "2026-01-03"

        # Idempotent: a second pass is a no-op
        _migrate_orders_schema_if_needed(c)
        assert c.execute("SELECT COUNT(*) AS n FROM limit_orders").fetchone()["n"] == 2


class TestOrdersMigrationAtomicity:
    def test_failed_copy_rolls_back_completely(self):
        """A failure mid-migration must leave the ORIGINAL table untouched.

        Regression for the review finding: the non-transactional form committed
        the RENAME first, so a failed copy stranded every row in
        limit_orders_old with an empty live table — and a re-run saw the new
        DDL and did nothing. Data loss, silently.
        """
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript(
            """\
            CREATE TABLE limit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                market_condition_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (length(outcome) > 0),
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                amount REAL NOT NULL,
                limit_price REAL NOT NULL,
                order_type TEXT NOT NULL CHECK (order_type IN ('gtc', 'gtd')),
                expires_at TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                filled_at TEXT
            );
            """
        )
        # A status the NEW schema's CHECK rejects — the copy INSERT will fail
        # on it (the relaxed old CHECK lets us stage the corruption; the point
        # is what the migration does when the copy blows up mid-flight).
        c.execute(
            """INSERT INTO limit_orders
               (market_slug, market_condition_id, outcome, side, amount,
                limit_price, order_type, status, created_at)
               VALUES ('m', '0x1', 'yes', 'buy', 100.0, 0.60, 'gtc',
                       'bogus', '2026-09-23T00:00:00Z')"""
        )

        with pytest.raises(sqlite3.IntegrityError):
            _migrate_orders_schema_if_needed(c)

        # ROLLBACK restored the original single-row table, original schema.
        rows = c.execute("SELECT * FROM limit_orders").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "bogus"
        assert rows[0]["amount"] == 100.0
        leftovers = [
            r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        assert "limit_orders_old" not in leftovers
        ddl = c.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'limit_orders'"
        ).fetchone()["sql"]
        assert "remaining_amount" not in ddl

    def test_rollback_failure_still_raises_original(self):
        """Even when ROLLBACK itself fails, the original error propagates."""
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript(
            """\
            CREATE TABLE limit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                market_condition_id TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (length(outcome) > 0),
                side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
                amount REAL NOT NULL,
                limit_price REAL NOT NULL,
                order_type TEXT NOT NULL CHECK (order_type IN ('gtc', 'gtd')),
                expires_at TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                filled_at TEXT
            );
            INSERT INTO limit_orders
                (market_slug, market_condition_id, outcome, side, amount,
                 limit_price, order_type, status, created_at)
                VALUES ('m', '0x1', 'yes', 'buy', 5.0, 0.50, 'gtc',
                        'bogus', '2026-09-23T00:00:00Z');
            """
        )

        class NoRollbackConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *a):
                if sql.strip().upper().startswith("ROLLBACK"):
                    raise sqlite3.OperationalError("cannot rollback")
                return self._inner.execute(sql, *a)

            def executescript(self, script):
                # Delegate verbatim — one script, embedded BEGIN/COMMIT preserved.
                return self._inner.executescript(script)

        with pytest.raises(sqlite3.IntegrityError):
            _migrate_orders_schema_if_needed(NoRollbackConn(c))

    def test_expire_reread_only_touched_rows(self, conn):
        """A second expire_orders call must not re-report previously expired
        GTDs as newly expired (review finding: predicate re-read was wider
        than the UPDATE)."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _create(conn, order_type="gtd", expires_at=past)
        first = expire_orders(conn)
        assert [o.id for o in first] == [1]

        _create(conn, order_type="gtd", expires_at=past)  # id 2, also past
        second = expire_orders(conn)
        assert [o.id for o in second] == [2]
        assert all(o.status == "expired" for o in second)
