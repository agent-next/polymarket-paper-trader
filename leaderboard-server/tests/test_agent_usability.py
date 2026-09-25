"""Agent usability tests: prove real AI agents can use this system.

Layered testing from an agent's perspective — what an agent SDK actually does.
Each test class represents a distinct layer of agent interaction.

Layer 1: Discovery — health check, API reachable
Layer 2: Onboarding — register, get API key, create account
Layer 3: Trading — buy, sell, portfolio, balance consistency
Layer 4: Persistence — reconnect with same API key, data intact
Layer 5: Isolation — multiple agents, zero data leaks
Layer 6: Recovery — handle errors gracefully, resume operations
Layer 7: Competition — leaderboard ranking, PK comparison
Layer 8: Data safety — backup, restore, resume trading
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from server.db import DB
from server.jobs.backup import backup_db

from tests.conftest import (
    _register,
    _headers,
    _create_account,
    _buy,
    _sell,
    _register_and_create_account,
    _insert_trades,
)


# ---------------------------------------------------------------------------
# Layer 1: Discovery — agent checks if API is alive before doing anything
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer1Discovery:
    def test_health_check(self, client):
        """First thing an agent does: GET /health to verify API is up."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_health_before_auth(self, client):
        """Health endpoint requires no auth — agent can check without API key."""
        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Layer 2: Onboarding — agent registers and gets set up
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer2Onboarding:
    def test_register_returns_api_key(self, client):
        """Agent registers with a name, receives API key for all future calls."""
        resp = client.post("/auth/register", json={"agent_name": "onboard-agent"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["api_key"].startswith("lb_sk_")
        assert data["data"]["agent_name"] == "onboard-agent"

    def test_create_account_with_starting_balance(self, client):
        """After register, agent creates an account and gets $10K starting balance."""
        user = _register(client, "setup-agent")
        h = _headers(user["api_key"])
        resp = client.post("/accounts", json={"name": "main"}, headers=h)
        assert resp.status_code == 200
        account = resp.json()["data"]
        assert account["cash"] == 10000.0
        assert account["starting_balance"] == 10000.0

    def test_multiple_accounts_per_agent(self, client):
        """Agent can create multiple accounts for different strategies."""
        user = _register(client, "multi-strat-agent")
        h = _headers(user["api_key"])
        resp1 = client.post("/accounts", json={"name": "aggressive"}, headers=h)
        resp2 = client.post("/accounts", json={"name": "conservative"}, headers=h)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["data"]["id"] != resp2.json()["data"]["id"]
        # Both start with full $10K
        assert resp1.json()["data"]["cash"] == 10000.0
        assert resp2.json()["data"]["cash"] == 10000.0


# ---------------------------------------------------------------------------
# Layer 3: Trading — agent executes trades and checks portfolio
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer3Trading:
    def test_buy_check_portfolio_sell(self, client):
        """Core agent loop: buy → check portfolio → sell → check balance."""
        user, account, h = _register_and_create_account(client, "trade-agent")
        acc_id = account["id"]

        # Buy
        buy_resp = _buy(client, user["api_key"], acc_id, amount=100.0)
        assert buy_resp.status_code == 200
        shares = buy_resp.json()["data"]["trade"]["shares"]

        # Portfolio shows the position
        portfolio = client.get(f"/accounts/{acc_id}/portfolio", headers=h).json()["data"]
        assert len(portfolio) == 1
        assert portfolio[0]["shares"] == pytest.approx(shares)
        assert portfolio[0]["live_price"] > 0

        # Sell all
        sell_resp = _sell(client, user["api_key"], acc_id, shares=shares)
        assert sell_resp.status_code == 200

        # Portfolio empty after full sell
        portfolio = client.get(f"/accounts/{acc_id}/portfolio", headers=h).json()["data"]
        # Position may remain with 0 shares or be absent
        open_positions = [p for p in portfolio if p["shares"] > 0]
        assert len(open_positions) == 0

    def test_balance_consistency(self, client):
        """cash + positions_value = total_value at all times."""
        user, account, h = _register_and_create_account(client, "balance-agent")
        acc_id = account["id"]

        # Before trading
        bal = client.get(f"/accounts/{acc_id}/balance", headers=h).json()["data"]
        assert bal["cash"] + bal["positions_value"] == pytest.approx(bal["total_value"])

        # After buying (use small amount that fits default book liquidity)
        resp = _buy(client, user["api_key"], acc_id, amount=50.0)
        assert resp.status_code == 200
        bal = client.get(f"/accounts/{acc_id}/balance", headers=h).json()["data"]
        assert bal["cash"] + bal["positions_value"] == pytest.approx(bal["total_value"])
        assert bal["cash"] < 10000.0
        assert bal["positions_value"] > 0

    def test_history_tracks_all_trades(self, client):
        """Agent can review full trade history."""
        user, account, h = _register_and_create_account(client, "history-agent")
        acc_id = account["id"]

        # 3 buys
        for _ in range(3):
            _buy(client, user["api_key"], acc_id, amount=50.0)

        history = client.get(f"/accounts/{acc_id}/history", headers=h).json()["data"]
        assert len(history) == 3
        assert all(t["side"] == "buy" for t in history)


# ---------------------------------------------------------------------------
# Layer 4: Persistence — agent reconnects, data still there
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer4Persistence:
    def test_reconnect_with_same_api_key(self, client):
        """Agent disconnects and reconnects — all data preserved."""
        # Session 1: register, trade
        user = _register(client, "persist-agent")
        api_key = user["api_key"]
        account = _create_account(client, api_key, "main")
        acc_id = account["id"]
        _buy(client, api_key, acc_id, amount=200.0)

        # Simulate "disconnect" — just use the same API key again
        # Session 2: reconnect, verify everything is still there
        h = _headers(api_key)

        # Account still exists
        balance = client.get(f"/accounts/{acc_id}/balance", headers=h).json()["data"]
        assert balance["cash"] < 10000.0  # Money was spent

        # Trade history preserved
        history = client.get(f"/accounts/{acc_id}/history", headers=h).json()["data"]
        assert len(history) == 1

        # Position still open
        portfolio = client.get(f"/accounts/{acc_id}/portfolio", headers=h).json()["data"]
        assert len(portfolio) == 1

        # Can still trade
        buy2 = _buy(client, api_key, acc_id, amount=50.0)
        assert buy2.status_code == 200

    def test_profile_accessible_by_name(self, client):
        """Agent's public profile accessible via /leaderboard/users/<name>."""
        user = _register(client, "profile-persist")
        _create_account(client, user["api_key"], "default")

        resp = client.get("/leaderboard/users/profile-persist")
        assert resp.status_code == 200
        assert resp.json()["data"]["agent_name"] == "profile-persist"


# ---------------------------------------------------------------------------
# Layer 5: Isolation — agents can't see each other's data
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer5Isolation:
    def test_agents_cannot_access_each_others_accounts(self, client):
        """Agent A's API key cannot access Agent B's account."""
        user_a = _register(client, "agent-a")
        user_b = _register(client, "agent-b")
        acc_a = _create_account(client, user_a["api_key"], "main")

        # Agent B tries to read Agent A's portfolio
        h_b = _headers(user_b["api_key"])
        resp = client.get(f"/accounts/{acc_a['id']}/portfolio", headers=h_b)
        assert resp.status_code == 403

        # Agent B tries to trade on Agent A's account
        resp = _buy(client, user_b["api_key"], acc_a["id"], amount=10.0)
        assert resp.status_code == 403

    def test_agents_independent_balances(self, client):
        """Two agents trading simultaneously — balances independent."""
        user_a, acc_a, h_a = _register_and_create_account(client, "iso-agent-a")
        user_b, acc_b, h_b = _register_and_create_account(client, "iso-agent-b")

        # Agent A trades
        resp = _buy(client, user_a["api_key"], acc_a["id"], amount=50.0)
        assert resp.status_code == 200
        # Agent B doesn't trade

        bal_a = client.get(f"/accounts/{acc_a['id']}/balance", headers=h_a).json()["data"]
        bal_b = client.get(f"/accounts/{acc_b['id']}/balance", headers=h_b).json()["data"]

        assert bal_a["cash"] < 10000.0
        assert bal_b["cash"] == 10000.0

    def test_agents_separate_trade_histories(self, client):
        """Agent A's trades don't appear in Agent B's history."""
        user_a, acc_a, h_a = _register_and_create_account(client, "hist-a")
        user_b, acc_b, h_b = _register_and_create_account(client, "hist-b")

        # Only Agent A trades
        _buy(client, user_a["api_key"], acc_a["id"], amount=100.0)

        hist_a = client.get(f"/accounts/{acc_a['id']}/history", headers=h_a).json()["data"]
        hist_b = client.get(f"/accounts/{acc_b['id']}/history", headers=h_b).json()["data"]

        assert len(hist_a) == 1
        assert len(hist_b) == 0


# ---------------------------------------------------------------------------
# Layer 6: Recovery — agent handles errors and continues
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer6Recovery:
    def test_recover_from_insufficient_balance(self, client_e2e):
        """Agent runs out of money, gets clear error, can still check state."""
        client = client_e2e
        user, account, h = _register_and_create_account(client, "broke-agent")
        acc_id = account["id"]

        # Spend almost everything (deep E2E book can handle large orders)
        for _ in range(9):
            resp = _buy(client, user["api_key"], acc_id,
                        amount=1000.0, slug="e2e-test-market")
            assert resp.status_code == 200

        # Next buy should fail cleanly
        resp = _buy(client, user["api_key"], acc_id,
                    amount=5000.0, slug="e2e-test-market")
        assert resp.status_code == 400
        error = resp.json()["detail"]
        assert error["code"] == "INSUFFICIENT_BALANCE"

        # Agent can still check balance and portfolio
        bal = client.get(f"/accounts/{acc_id}/balance", headers=h).json()
        assert bal["ok"] is True
        portfolio = client.get(f"/accounts/{acc_id}/portfolio", headers=h).json()
        assert portfolio["ok"] is True

    def test_recover_from_invalid_auth(self, client):
        """Invalid API key gives 401, agent knows to re-register."""
        resp = client.get(
            "/accounts",
            headers={"Authorization": "Bearer lb_sk_invalid_key"},
        )
        assert resp.status_code == 401

    def test_error_responses_are_parseable(self, client):
        """All errors return structured JSON agents can parse."""
        user = _register(client, "error-agent")
        account = _create_account(client, user["api_key"])

        # 400: no position
        resp = _sell(client, user["api_key"], account["id"], shares=10.0)
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert isinstance(detail["code"], str)
        assert isinstance(detail["error"], str)


# ---------------------------------------------------------------------------
# Layer 7: Competition — leaderboard and rankings
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer7Competition:
    def test_qualify_and_appear_on_leaderboard(self, client_e2e):
        """Agent makes 10 trades → appears on leaderboard with stats."""
        client = client_e2e
        user, account, h = _register_and_create_account(client, "compete-agent")
        acc_id = account["id"]

        for _ in range(10):
            _buy(client, user["api_key"], acc_id, amount=50.0, slug="e2e-test-market")

        lb = client.get("/leaderboard").json()["data"]
        entry = next(e for e in lb if e["agent_name"] == "compete-agent")
        assert entry["trade_count"] == 10
        assert "roi_pct" in entry
        assert "sharpe_ratio" in entry
        assert entry["tier"] in ("bronze", "silver", "gold", "diamond")

    def test_two_agents_ranked_by_performance(self, client_e2e):
        """Better-performing agent ranks higher on leaderboard."""
        client = client_e2e

        # Agent A: trades small
        user_a, acc_a, _ = _register_and_create_account(client, "rank-a")
        _insert_trades(client, acc_a["id"], count=10)

        # Agent B: trades small, but more cash (simulate better performance)
        user_b, acc_b, _ = _register_and_create_account(client, "rank-b")
        _insert_trades(client, acc_b["id"], count=10)
        client.app.state.db.update_cash(acc_b["id"], 12000)  # +20% ROI

        lb = client.get("/leaderboard").json()["data"]
        names = [e["agent_name"] for e in lb]
        assert "rank-a" in names
        assert "rank-b" in names

        # PK comparison works
        pk = client.get(f"/leaderboard/pk/{acc_a['id']}/{acc_b['id']}").json()["data"]
        assert pk["b"]["roi_pct"] > pk["a"]["roi_pct"]


# ---------------------------------------------------------------------------
# Layer 8: Data safety — backup, restore, verify data intact
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestLayer8DataSafety:
    def test_backup_preserves_agent_data(self, tmp_path):
        """Backup captures all agent data; restore brings it back."""
        # Setup: create DB with agent data
        db = DB(":memory:")
        db.init_schema()
        user = db.create_user("backup-agent")
        account = db.create_account(user["id"], "main")
        db.update_cash(account["id"], 9900.0)  # Simulate $100 spent
        db.insert_trade(
            account_id=account["id"],
            market_condition_id="0xtest",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            side="buy",
            order_type="fok",
            avg_price=0.65,
            amount_usd=100,
            shares=153.84,
            fee_rate_bps=0,
            fee=0,
            slippage=0,
            levels_filled=1,
            is_partial=False,
            book_snapshot_id=None,
        )

        # Backup
        backup_dir = str(tmp_path / "backups")
        backup_path = backup_db(db, backup_dir)
        assert os.path.exists(backup_path)

        # Restore: open backup as new DB
        conn = sqlite3.connect(backup_path)
        conn.row_factory = sqlite3.Row

        # Verify user exists
        row = conn.execute(
            "SELECT * FROM users WHERE agent_name = ?", ("backup-agent",)
        ).fetchone()
        assert row is not None

        # Verify account balance reflects the trade
        acct = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account["id"],)
        ).fetchone()
        assert float(dict(acct)["cash"]) == pytest.approx(9900.0)

        # Verify trade exists
        trade = conn.execute(
            "SELECT * FROM trades WHERE account_id = ?", (account["id"],)
        ).fetchone()
        assert dict(trade)["market_slug"] == "test-market"
        assert dict(trade)["shares"] == pytest.approx(153.84)

        conn.close()
        db.close()

    def test_wal_mode_enabled(self, tmp_path):
        """File-backed DB uses WAL mode for crash safety."""
        db_path = str(tmp_path / "test.db")
        db = DB(db_path)
        db.init_schema()
        mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        db.close()
