"""Comprehensive tests for pm_trader.db."""

from __future__ import annotations

from pathlib import Path

import pytest

from pm_trader.db import Database
from pm_trader.models import Account, Position, Trade


@pytest.fixture
def db(tmp_data_dir: Path) -> Database:
    """Return an initialized Database instance."""
    database = Database(tmp_data_dir)
    database.init_schema()
    return database


# ======================================================================
# Schema
# ======================================================================

class TestInitSchema:
    def test_creates_tables(self, db: Database) -> None:
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = sorted(row["name"] for row in tables)
        assert "account" in table_names
        assert "trades" in table_names
        assert "positions" in table_names
        assert "market_cache" in table_names

    def test_idempotent(self, db: Database) -> None:
        """Calling init_schema twice should not raise."""
        db.init_schema()
        db.init_schema()


# ======================================================================
# Account
# ======================================================================

class TestAccount:
    def test_init_account_default_balance(self, db: Database) -> None:
        account = db.init_account()
        assert account.id == 1
        assert account.starting_balance == 10000.0
        assert account.cash == 10000.0
        assert account.created_at is not None

    def test_init_account_custom_balance(self, db: Database) -> None:
        account = db.init_account(balance=50000.0)
        assert account.starting_balance == 50000.0
        assert account.cash == 50000.0

    def test_get_account_none_before_init(self, db: Database) -> None:
        assert db.get_account() is None

    def test_get_account_returns_account(self, db: Database) -> None:
        db.init_account(balance=25000.0)
        account = db.get_account()
        assert isinstance(account, Account)
        assert account.cash == 25000.0

    def test_update_cash(self, db: Database) -> None:
        db.init_account(balance=10000.0)
        db.update_cash(7500.0)
        account = db.get_account()
        assert account.cash == 7500.0

    def test_update_cash_to_zero(self, db: Database) -> None:
        db.init_account(balance=10000.0)
        db.update_cash(0.0)
        account = db.get_account()
        assert account.cash == 0.0

    def test_init_account_replaces_existing(self, db: Database) -> None:
        db.init_account(balance=10000.0)
        db.update_cash(5000.0)
        db.init_account(balance=20000.0)
        account = db.get_account()
        assert account.starting_balance == 20000.0
        assert account.cash == 20000.0


# ======================================================================
# Reset
# ======================================================================

class TestReset:
    def test_reset_clears_all_data(self, db: Database) -> None:
        db.init_account(balance=10000.0)
        db.insert_trade(
            market_condition_id="0xabc",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            side="buy",
            order_type="fok",
            avg_price=0.65,
            amount_usd=100.0,
            shares=153.85,
            fee_rate_bps=0,
            fee=0.0,
            slippage=0.0,
            levels_filled=1,
            is_partial=False,
        )
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            shares=153.85,
            avg_entry_price=0.65,
            total_cost=100.0,
        )
        db.set_cache("test_key", {"foo": "bar"})

        db.reset()

        assert db.get_account() is None
        assert db.get_trades() == []
        assert db.get_open_positions() == []
        assert db.get_cache("test_key") is None

    def test_reset_clears_limit_orders(self, db: Database) -> None:
        from pm_trader.orders import create_order, get_pending_orders, init_orders_schema

        init_orders_schema(db.conn)
        create_order(
            db.conn,
            market_slug="m",
            market_condition_id="0x1",
            outcome="yes",
            side="buy",
            amount=100.0,
            limit_price=0.50,
        )
        assert len(get_pending_orders(db.conn)) == 1

        db.reset()
        init_orders_schema(db.conn)

        assert len(get_pending_orders(db.conn)) == 0


# ======================================================================
# Trades
# ======================================================================

class TestTrades:
    def test_insert_trade_returns_trade(self, db: Database) -> None:
        trade = db.insert_trade(
            market_condition_id="0xabc123",
            market_slug="will-bitcoin-hit-100k",
            market_question="Will Bitcoin hit $100k?",
            outcome="yes",
            side="buy",
            order_type="fok",
            avg_price=0.6647,
            amount_usd=100.0,
            shares=150.45,
            fee_rate_bps=0,
            fee=0.0,
            slippage=1.5,
            levels_filled=2,
            is_partial=False,
        )
        assert isinstance(trade, Trade)
        assert trade.id == 1
        assert trade.market_condition_id == "0xabc123"
        assert trade.market_slug == "will-bitcoin-hit-100k"
        assert trade.market_question == "Will Bitcoin hit $100k?"
        assert trade.outcome == "yes"
        assert trade.side == "buy"
        assert trade.order_type == "fok"
        assert trade.avg_price == 0.6647
        assert trade.amount_usd == 100.0
        assert trade.shares == 150.45
        assert trade.fee_rate_bps == 0
        assert trade.fee == 0.0
        assert trade.slippage == 1.5
        assert trade.levels_filled == 2
        assert trade.is_partial is False
        assert trade.created_at is not None

    def test_insert_trade_sell_with_fee(self, db: Database) -> None:
        trade = db.insert_trade(
            market_condition_id="0xdef456",
            market_slug="sports-game",
            market_question="Will team A win?",
            outcome="no",
            side="sell",
            order_type="fak",
            avg_price=0.636,
            amount_usd=63.60,
            shares=100.0,
            fee_rate_bps=175,
            fee=0.67,
            slippage=2.3,
            levels_filled=2,
            is_partial=True,
        )
        assert trade.side == "sell"
        assert trade.order_type == "fak"
        assert trade.fee_rate_bps == 175
        assert trade.fee == 0.67
        assert trade.is_partial is True

    def test_insert_trade_autoincrement(self, db: Database) -> None:
        trade1 = db.insert_trade(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="yes",
            side="buy",
            order_type="fok",
            avg_price=0.5,
            amount_usd=50.0,
            shares=100.0,
            fee_rate_bps=0,
            fee=0.0,
            slippage=0.0,
            levels_filled=1,
            is_partial=False,
        )
        trade2 = db.insert_trade(
            market_condition_id="0xdef",
            market_slug="m2",
            market_question="Q2?",
            outcome="no",
            side="sell",
            order_type="fak",
            avg_price=0.3,
            amount_usd=30.0,
            shares=100.0,
            fee_rate_bps=200,
            fee=0.5,
            slippage=1.0,
            levels_filled=1,
            is_partial=False,
        )
        assert trade1.id == 1
        assert trade2.id == 2

    def test_get_trades_newest_first(self, db: Database) -> None:
        for i in range(5):
            db.insert_trade(
                market_condition_id=f"0x{i}",
                market_slug=f"market-{i}",
                market_question=f"Question {i}?",
                outcome="yes",
                side="buy",
                order_type="fok",
                avg_price=0.5,
                amount_usd=10.0,
                shares=20.0,
                fee_rate_bps=0,
                fee=0.0,
                slippage=0.0,
                levels_filled=1,
                is_partial=False,
            )
        trades = db.get_trades()
        assert len(trades) == 5
        assert trades[0].id == 5
        assert trades[4].id == 1

    def test_get_trades_with_limit(self, db: Database) -> None:
        for i in range(10):
            db.insert_trade(
                market_condition_id=f"0x{i}",
                market_slug=f"market-{i}",
                market_question=f"Question {i}?",
                outcome="yes",
                side="buy",
                order_type="fok",
                avg_price=0.5,
                amount_usd=10.0,
                shares=20.0,
                fee_rate_bps=0,
                fee=0.0,
                slippage=0.0,
                levels_filled=1,
                is_partial=False,
            )
        trades = db.get_trades(limit=3)
        assert len(trades) == 3
        assert trades[0].id == 10

    def test_get_trades_empty(self, db: Database) -> None:
        assert db.get_trades() == []


# ======================================================================
# Positions
# ======================================================================

class TestPositions:
    def test_upsert_position_insert(self, db: Database) -> None:
        position = db.upsert_position(
            market_condition_id="0xabc123",
            market_slug="will-bitcoin-hit-100k",
            market_question="Will Bitcoin hit $100k?",
            outcome="yes",
            shares=150.45,
            avg_entry_price=0.6647,
            total_cost=100.0,
        )
        assert isinstance(position, Position)
        assert position.market_condition_id == "0xabc123"
        assert position.market_slug == "will-bitcoin-hit-100k"
        assert position.outcome == "yes"
        assert position.shares == 150.45
        assert position.avg_entry_price == 0.6647
        assert position.total_cost == 100.0
        assert position.realized_pnl == 0.0
        assert position.is_resolved is False
        assert position.resolved_at is None

    def test_upsert_position_update(self, db: Database) -> None:
        db.upsert_position(
            market_condition_id="0xabc123",
            market_slug="will-bitcoin-hit-100k",
            market_question="Will Bitcoin hit $100k?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.65,
            total_cost=65.0,
        )
        position = db.upsert_position(
            market_condition_id="0xabc123",
            market_slug="will-bitcoin-hit-100k",
            market_question="Will Bitcoin hit $100k?",
            outcome="yes",
            shares=250.0,
            avg_entry_price=0.66,
            total_cost=165.0,
        )
        assert position.shares == 250.0
        assert position.avg_entry_price == 0.66
        assert position.total_cost == 165.0

    def test_get_position_none(self, db: Database) -> None:
        assert db.get_position("nonexistent", "yes") is None

    def test_get_position_existing(self, db: Database) -> None:
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="test",
            market_question="Test?",
            outcome="no",
            shares=50.0,
            avg_entry_price=0.35,
            total_cost=17.5,
        )
        position = db.get_position("0xabc", "no")
        assert position is not None
        assert position.outcome == "no"
        assert position.shares == 50.0

    def test_get_open_positions(self, db: Database) -> None:
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.65,
            total_cost=65.0,
        )
        db.upsert_position(
            market_condition_id="0xdef",
            market_slug="m2",
            market_question="Q2?",
            outcome="no",
            shares=50.0,
            avg_entry_price=0.40,
            total_cost=20.0,
        )
        # This one has zero shares -- should NOT appear
        db.upsert_position(
            market_condition_id="0xghi",
            market_slug="m3",
            market_question="Q3?",
            outcome="yes",
            shares=0.0,
            avg_entry_price=0.50,
            total_cost=0.0,
        )
        open_positions = db.get_open_positions()
        assert len(open_positions) == 2
        condition_ids = {p.market_condition_id for p in open_positions}
        assert condition_ids == {"0xabc", "0xdef"}

    def test_get_open_positions_excludes_resolved(self, db: Database) -> None:
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.65,
            total_cost=65.0,
        )
        db.resolve_position("0xabc", "yes", payout=100.0)
        open_positions = db.get_open_positions()
        assert len(open_positions) == 0

    def test_get_positions_for_market(self, db: Database) -> None:
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.65,
            total_cost=65.0,
        )
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="no",
            shares=30.0,
            avg_entry_price=0.35,
            total_cost=10.5,
        )
        db.upsert_position(
            market_condition_id="0xother",
            market_slug="m2",
            market_question="Q2?",
            outcome="yes",
            shares=50.0,
            avg_entry_price=0.50,
            total_cost=25.0,
        )
        positions = db.get_positions_for_market("0xabc")
        assert len(positions) == 2
        outcomes = {p.outcome for p in positions}
        assert outcomes == {"yes", "no"}

    def test_get_positions_for_market_empty(self, db: Database) -> None:
        positions = db.get_positions_for_market("nonexistent")
        assert positions == []

    def test_upsert_position_with_realized_pnl(self, db: Database) -> None:
        position = db.upsert_position(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="yes",
            shares=50.0,
            avg_entry_price=0.60,
            total_cost=30.0,
            realized_pnl=5.0,
        )
        assert position.realized_pnl == 5.0


# ======================================================================
# Resolution
# ======================================================================

class TestResolvePosition:
    def test_resolve_winning_position(self, db: Database) -> None:
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.65,
            total_cost=65.0,
        )
        position = db.resolve_position("0xabc", "yes", payout=100.0)
        assert position.is_resolved is True
        assert position.resolved_at is not None
        assert position.shares == 0.0
        assert position.realized_pnl == pytest.approx(35.0)

    def test_resolve_losing_position(self, db: Database) -> None:
        db.upsert_position(
            market_condition_id="0xabc",
            market_slug="m1",
            market_question="Q1?",
            outcome="no",
            shares=100.0,
            avg_entry_price=0.35,
            total_cost=35.0,
        )
        position = db.resolve_position("0xabc", "no", payout=0.0)
        assert position.is_resolved is True
        assert position.shares == 0.0
        assert position.realized_pnl == pytest.approx(-35.0)

    def test_resolve_nonexistent_position_raises(self, db: Database) -> None:
        with pytest.raises(ValueError, match="No position"):
            db.resolve_position("nonexistent", "yes", payout=100.0)


# ======================================================================
# Cache
# ======================================================================

class TestCache:
    def test_set_and_get_cache_dict(self, db: Database) -> None:
        data = {"condition_id": "0xabc", "slug": "test-market"}
        db.set_cache("market:test-market", data)
        result = db.get_cache("market:test-market")
        assert result == data

    def test_set_and_get_cache_list(self, db: Database) -> None:
        data = [{"id": 1}, {"id": 2}]
        db.set_cache("markets:list", data)
        result = db.get_cache("markets:list")
        assert result == data

    def test_get_cache_missing_key(self, db: Database) -> None:
        assert db.get_cache("nonexistent") is None

    def test_set_cache_overwrites(self, db: Database) -> None:
        db.set_cache("key", {"version": 1})
        db.set_cache("key", {"version": 2})
        result = db.get_cache("key")
        assert result == {"version": 2}

    def test_cache_with_nested_data(self, db: Database) -> None:
        data = {
            "market": {
                "condition_id": "0xabc",
                "tokens": [
                    {"token_id": "tok1", "outcome": "Yes"},
                    {"token_id": "tok2", "outcome": "No"},
                ],
            },
            "fee_rate_bps": 175,
        }
        db.set_cache("complex_key", data)
        result = db.get_cache("complex_key")
        assert result == data


# ======================================================================
# Database lifecycle
# ======================================================================

class TestDatabaseLifecycle:
    def test_creates_data_dir(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "nested" / "dir"
        database = Database(data_dir)
        assert data_dir.exists()

    def test_db_file_created_on_init(self, tmp_data_dir: Path) -> None:
        database = Database(tmp_data_dir)
        database.init_schema()
        assert (tmp_data_dir / "paper.db").exists()

    def test_close_and_reconnect(self, tmp_data_dir: Path) -> None:
        database = Database(tmp_data_dir)
        database.init_schema()
        database.init_account(balance=5000.0)
        database.close()

        database2 = Database(tmp_data_dir)
        database2.init_schema()
        account = database2.get_account()
        assert account is not None
        assert account.cash == 5000.0


# ======================================================================
# Constraint enforcement
# ======================================================================

class TestConstraints:
    def test_account_id_must_be_1(self, db: Database) -> None:
        """The account table enforces CHECK (id = 1)."""
        with pytest.raises(Exception):
            db.conn.execute(
                "INSERT INTO account (id, starting_balance, cash) VALUES (2, 10000, 10000)"
            )

    def test_trade_outcome_constraint(self, db: Database) -> None:
        """Outcome must be non-empty."""
        with pytest.raises(Exception):
            db.conn.execute(
                """\
                INSERT INTO trades (
                    market_condition_id, market_slug, market_question,
                    outcome, side, order_type,
                    avg_price, amount_usd, shares, fee_rate_bps
                ) VALUES ('0x1', 's', 'q', '', 'buy', 'fok', 0.5, 10, 20, 0)
                """
            )

    def test_trade_side_constraint(self, db: Database) -> None:
        """Side must be 'buy' or 'sell'."""
        with pytest.raises(Exception):
            db.conn.execute(
                """\
                INSERT INTO trades (
                    market_condition_id, market_slug, market_question,
                    outcome, side, order_type,
                    avg_price, amount_usd, shares, fee_rate_bps
                ) VALUES ('0x1', 's', 'q', 'yes', 'hold', 'fok', 0.5, 10, 20, 0)
                """
            )

    def test_trade_order_type_constraint(self, db: Database) -> None:
        """Order type must be 'fok' or 'fak'."""
        with pytest.raises(Exception):
            db.conn.execute(
                """\
                INSERT INTO trades (
                    market_condition_id, market_slug, market_question,
                    outcome, side, order_type,
                    avg_price, amount_usd, shares, fee_rate_bps
                ) VALUES ('0x1', 's', 'q', 'yes', 'buy', 'gtc', 0.5, 10, 20, 0)
                """
            )

    def test_position_outcome_constraint(self, db: Database) -> None:
        """Position outcome must be non-empty."""
        with pytest.raises(Exception):
            db.conn.execute(
                """\
                INSERT INTO positions (
                    market_condition_id, market_slug, market_question,
                    outcome, shares, avg_entry_price, total_cost
                ) VALUES ('0x1', 's', 'q', '', 10, 0.5, 5.0)
                """
            )


class TestAtomic:
    def test_atomic_blocks_do_not_nest(self, db):
        """Nesting is refused loudly: no call path needs it."""
        with pytest.raises(RuntimeError, match="do not nest"):
            with db.atomic():
                with db.atomic():
                    pass  # pragma: no cover — never reached

    def test_atomic_commits_and_rolls_back(self, db):
        """Writes inside atomic() commit on success; an error rolls back all."""
        import sqlite3 as _s

        db.init_account(100.0)
        with db.atomic():
            db.update_cash(80.0)
        assert db.get_account().cash == 80.0

        with pytest.raises(RuntimeError, match="boom"), db.atomic():
            db.update_cash(50.0)
            raise RuntimeError("boom")
        assert db.get_account().cash == 80.0
        # A fresh connection sees the committed state, not the rolled-back one
        raw = _s.connect(str(db.db_path))
        assert raw.execute("SELECT cash FROM account").fetchone()[0] == 80.0
