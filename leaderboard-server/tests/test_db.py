"""Test database layer."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest
from server.db import DB


@pytest.fixture
def db():
    _db = DB(":memory:")
    _db.init_schema()
    yield _db
    _db.close()


class TestUsers:
    def test_create_user(self, db):
        user = db.create_user("alpha-trader")
        assert user["agent_name"] == "alpha-trader"
        assert user["api_key"].startswith("lb_sk_")
        assert user["model"] is None

    def test_create_user_with_model(self, db):
        user = db.create_user("model-bot", model="claude-opus-4")
        assert user["model"] == "claude-opus-4"

    def test_update_model(self, db):
        user = db.create_user("updatable")
        updated = db.update_model(user["id"], "gpt-4o")
        assert updated["model"] == "gpt-4o"

    def test_duplicate_name_rejected(self, db):
        db.create_user("alpha-trader")
        with pytest.raises(Exception):
            db.create_user("alpha-trader")

    def test_get_user_by_api_key(self, db):
        user = db.create_user("bot")
        found = db.get_user_by_api_key(user["api_key"])
        assert found["agent_name"] == "bot"

    def test_get_user_by_api_key_not_found(self, db):
        found = db.get_user_by_api_key("lb_sk_nonexistent")
        assert found is None

    def test_get_user_by_name(self, db):
        db.create_user("finder")
        found = db.get_user_by_name("finder")
        assert found["agent_name"] == "finder"


class TestAccounts:
    def test_create_account(self, db):
        user = db.create_user("bot")
        account = db.create_account(user["id"], "default")
        assert account["cash"] == 10000.0
        assert account["starting_balance"] == 10000.0

    def test_update_cash(self, db):
        user = db.create_user("bot")
        account = db.create_account(user["id"], "default")
        db.update_cash(account["id"], 9500.0)
        updated = db.get_account(account["id"])
        assert updated["cash"] == 9500.0

    def test_get_user_accounts(self, db):
        user = db.create_user("bot")
        db.create_account(user["id"], "account1")
        db.create_account(user["id"], "account2")
        accounts = db.get_user_accounts(user["id"])
        assert len(accounts) == 2

    def test_duplicate_account_name_rejected(self, db):
        user = db.create_user("bot")
        db.create_account(user["id"], "default")
        with pytest.raises(Exception):
            db.create_account(user["id"], "default")


class TestTrades:
    def _make_account(self, db):
        user = db.create_user("bot")
        return db.create_account(user["id"], "default")

    def test_insert_trade(self, db):
        account = self._make_account(db)
        trade = db.insert_trade(
            account_id=account["id"],
            market_condition_id="0xabc",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            side="buy",
            order_type="fok",
            avg_price=0.5,
            amount_usd=500.0,
            shares=1000.0,
            fee_rate_bps=200,
            fee=5.0,
            slippage=10.0,
            levels_filled=2,
            is_partial=False,
            book_snapshot_id=None,
        )
        assert trade["side"] == "buy"
        assert trade["shares"] == 1000.0

    def test_get_trades(self, db):
        account = self._make_account(db)
        db.insert_trade(
            account_id=account["id"],
            market_condition_id="0xabc", market_slug="test", market_question="T?",
            outcome="yes", side="buy", order_type="fok",
            avg_price=0.5, amount_usd=500, shares=1000,
            fee_rate_bps=200, fee=5, slippage=10,
            levels_filled=1, is_partial=False, book_snapshot_id=None,
        )
        trades = db.get_trades(account["id"])
        assert len(trades) == 1

    def test_get_trade_count(self, db):
        account = self._make_account(db)
        assert db.get_trade_count(account["id"]) == 0
        db.insert_trade(
            account_id=account["id"],
            market_condition_id="0xabc", market_slug="test", market_question="T?",
            outcome="yes", side="buy", order_type="fok",
            avg_price=0.5, amount_usd=500, shares=1000,
            fee_rate_bps=200, fee=5, slippage=10,
            levels_filled=1, is_partial=False, book_snapshot_id=None,
        )
        assert db.get_trade_count(account["id"]) == 1


class TestPositions:
    def _make_account(self, db):
        user = db.create_user("bot")
        return db.create_account(user["id"], "default")

    def test_upsert_position_insert(self, db):
        account = self._make_account(db)
        pos = db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc",
            market_slug="test",
            market_question="T?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.5,
            total_cost=50.0,
            realized_pnl=0.0,
        )
        assert pos["shares"] == 100.0

    def test_upsert_position_update(self, db):
        account = self._make_account(db)
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc", market_slug="test",
            market_question="T?", outcome="yes",
            shares=100, avg_entry_price=0.5, total_cost=50, realized_pnl=0,
        )
        updated = db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc", market_slug="test",
            market_question="T?", outcome="yes",
            shares=200, avg_entry_price=0.45, total_cost=90, realized_pnl=0,
        )
        assert updated["shares"] == 200.0

    def test_get_open_positions(self, db):
        account = self._make_account(db)
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc", market_slug="test",
            market_question="T?", outcome="yes",
            shares=100, avg_entry_price=0.5, total_cost=50, realized_pnl=0,
        )
        positions = db.get_open_positions(account["id"])
        assert len(positions) == 1

    def test_resolve_position(self, db):
        account = self._make_account(db)
        pos = db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc", market_slug="test",
            market_question="T?", outcome="yes",
            shares=100, avg_entry_price=0.5, total_cost=50, realized_pnl=0,
        )
        resolved = db.resolve_position(pos["id"], 50.0)
        assert resolved["is_resolved"] == 1
        assert resolved["realized_pnl"] == 50.0

    def test_resolve_position_credits_once(self, db):
        account = self._make_account(db)
        pos = db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc", market_slug="test",
            market_question="T?", outcome="yes",
            shares=100, avg_entry_price=0.5, total_cost=50, realized_pnl=0,
        )
        assert db.resolve_position(pos["id"], 50.0, credit=100.0) is not None
        assert db.resolve_position(pos["id"], 50.0, credit=100.0) is None
        assert db.get_account(account["id"])["cash"] == 10100.0
        assert db.get_position(account["id"], "0xabc", "yes")["realized_pnl"] == 50.0


class TestLimitOrders:
    def _make_account(self, db):
        user = db.create_user("bot")
        return db.create_account(user["id"], "default")

    def test_create_and_get_pending(self, db):
        account = self._make_account(db)
        order = db.create_limit_order(
            account_id=account["id"],
            market_slug="test", market_condition_id="0xabc",
            outcome="yes", side="buy", amount=500,
            limit_price=0.45,
        )
        assert order["status"] == "pending"
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 1

    def test_cancel_order(self, db):
        account = self._make_account(db)
        order = db.create_limit_order(
            account_id=account["id"],
            market_slug="test", market_condition_id="0xabc",
            outcome="yes", side="buy", amount=500,
            limit_price=0.45,
        )
        cancelled = db.cancel_order(order["id"], account["id"])
        assert cancelled["status"] == "cancelled"

    def test_cancel_order_wrong_account(self, db):
        account = self._make_account(db)
        order = db.create_limit_order(
            account_id=account["id"],
            market_slug="test", market_condition_id="0xabc",
            outcome="yes", side="buy", amount=500,
            limit_price=0.45,
        )
        assert db.cancel_order(order["id"], account["id"] + 1) is None
        pending = db.get_pending_orders(account["id"])
        assert len(pending) == 1

    def test_fill_order(self, db):
        account = self._make_account(db)
        order = db.create_limit_order(
            account_id=account["id"],
            market_slug="test", market_condition_id="0xabc",
            outcome="yes", side="buy", amount=500,
            limit_price=0.45,
        )
        filled = db.fill_order(order["id"])
        assert filled["status"] == "filled"


class TestBookSnapshots:
    def test_save_snapshot(self, db):
        snapshot_id = db.save_book_snapshot("token123", {"bids": [], "asks": []})
        assert isinstance(snapshot_id, int)


class TestLeaderboard:
    def test_get_leaderboard_accounts_empty(self, db):
        accounts = db.get_leaderboard_accounts()
        assert accounts == []

    def test_get_leaderboard_accounts_with_trades(self, db):
        user = db.create_user("bot", model="claude-opus-4")
        account = db.create_account(user["id"], "default")
        # Insert 10 trades to qualify
        for i in range(10):
            db.insert_trade(
                account_id=account["id"],
                market_condition_id="0xabc", market_slug="test",
                market_question="T?", outcome="yes", side="buy",
                order_type="fok", avg_price=0.5, amount_usd=100,
                shares=200, fee_rate_bps=200, fee=1, slippage=0,
                levels_filled=1, is_partial=False, book_snapshot_id=None,
            )
        accounts = db.get_leaderboard_accounts(min_trades=10)
        assert len(accounts) == 1
        assert accounts[0]["agent_name"] == "bot"
        assert accounts[0]["model"] == "claude-opus-4"
        assert accounts[0]["trade_count"] == 10

    def test_get_recent_trades_global_empty(self, db):
        trades = db.get_recent_trades_global()
        assert trades == []

    def test_get_recent_trades_global(self, db):
        user = db.create_user("feed-bot")
        account = db.create_account(user["id"], "default")
        for _ in range(3):
            db.insert_trade(
                account_id=account["id"],
                market_condition_id="0xabc", market_slug="test",
                market_question="T?", outcome="yes", side="buy",
                order_type="fok", avg_price=0.5, amount_usd=100,
                shares=200, fee_rate_bps=0, fee=0, slippage=0,
                levels_filled=1, is_partial=False, book_snapshot_id=None,
            )
        trades = db.get_recent_trades_global(limit=2)
        assert len(trades) == 2
        assert trades[0]["agent_name"] == "feed-bot"
        assert "account_name" in trades[0]


class TestApiKeyHashing:
    def test_register_stores_hash_not_plaintext(self, db):
        user = db.create_user("hashed")
        stored = db._conn.execute(
            "SELECT api_key FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0]
        assert stored == hashlib.sha256(user["api_key"].encode()).hexdigest()
        assert stored != user["api_key"]
        assert db.get_user_by_api_key(user["api_key"])["id"] == user["id"]

    def test_stored_hash_is_not_a_valid_credential(self, db):
        user = db.create_user("hashed")
        stored = db._conn.execute("SELECT api_key FROM users").fetchone()[0]
        assert db.get_user_by_api_key(stored) is None

    def test_legacy_plaintext_row_migrates_idempotently(self, tmp_path):
        path = str(tmp_path / "legacy.db")
        first = DB(path)
        first.init_schema()
        legacy = "lb_sk_legacykey"
        first._conn.execute(
            "INSERT INTO users (agent_name, api_key) VALUES ('old', ?)", (legacy,)
        )
        first._conn.commit()
        first.close()

        digest = hashlib.sha256(legacy.encode()).hexdigest()
        for _ in range(2):  # second init must leave the hash untouched
            again = DB(path)
            again.init_schema()
            stored = again._conn.execute("SELECT api_key FROM users").fetchone()[0]
            assert stored == digest
            assert again.get_user_by_api_key(legacy)["agent_name"] == "old"
            again.close()


class TestPruneBookSnapshots:
    def _snap(self, db, age_days):
        sid = db.save_book_snapshot("tok", {"bids": []})
        db._conn.execute(
            "UPDATE book_snapshots SET fetched_at = datetime('now', ?) WHERE id = ?",
            (f"-{age_days} days", sid),
        )
        return sid

    def _ids(self, db):
        return {r[0] for r in db._conn.execute("SELECT id FROM book_snapshots")}

    def test_prunes_old_unreferenced_keeps_recent_and_referenced(self, db):
        user = db.create_user("bot")
        account = db.create_account(user["id"], "main")
        old = self._snap(db, 10)
        referenced = self._snap(db, 10)
        recent = self._snap(db, 1)
        db.insert_trade(
            account_id=account["id"], market_condition_id="0x", market_slug="m",
            market_question="Q?", outcome="yes", side="buy", order_type="fok",
            avg_price=0.5, amount_usd=1, shares=2, fee_rate_bps=0, fee=0,
            slippage=0, levels_filled=1, is_partial=False,
            book_snapshot_id=referenced,
        )
        assert db.prune_book_snapshots(older_than_days=7) == 1
        assert self._ids(db) == {referenced, recent}
        assert old not in self._ids(db)

    def test_default_cutoff_is_seven_days(self, db):
        old, recent = self._snap(db, 8), self._snap(db, 6)
        assert db.prune_book_snapshots() == 1
        assert self._ids(db) == {recent}
