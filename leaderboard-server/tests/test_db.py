"""Test database layer."""
from __future__ import annotations

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
        cancelled = db.cancel_order(order["id"])
        assert cancelled["status"] == "cancelled"

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
        user = db.create_user("bot")
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
        assert accounts[0]["trade_count"] == 10
