"""Behavior tests: agent-perspective workflows.

No internal knowledge — only HTTP calls and JSON responses.
All @pytest.mark.integration.
"""
from __future__ import annotations

import pytest

from tests.conftest import (
    _register,
    _headers,
    _create_account,
    _buy,
    _sell,
    _register_and_create_account,
    _insert_trades,
)


@pytest.mark.integration
class TestBuySellRoundtrip:
    def test_buy_sell_roundtrip_cash_recovery(self, client):
        """Buy YES, sell all, verify cash recovery minus spread."""
        user = _register(client)
        account = _create_account(client, user["api_key"])
        starting_cash = account["cash"]

        # Buy
        buy_resp = _buy(client, user["api_key"], account["id"], amount=100.0)
        assert buy_resp.status_code == 200
        shares = buy_resp.json()["data"]["trade"]["shares"]
        cash_after_buy = buy_resp.json()["data"]["account"]["cash"]
        assert cash_after_buy < starting_cash

        # Sell all
        sell_resp = _sell(client, user["api_key"], account["id"], shares=shares)
        assert sell_resp.status_code == 200
        cash_after_sell = sell_resp.json()["data"]["account"]["cash"]

        # Cash should recover, but not fully (bid < ask = spread)
        assert cash_after_sell > cash_after_buy
        assert cash_after_sell < starting_cash  # spread means loss


@pytest.mark.integration
class TestLimitOrderWorkflow:
    def test_place_check_cancel_workflow(self, client):
        """Place GTC buy -> check pending -> cancel -> verify cash unchanged."""
        user = _register(client)
        account = _create_account(client, user["api_key"])
        h = _headers(user["api_key"])
        starting_cash = account["cash"]

        # Place limit order
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "will-bitcoin-hit-100k",
            "market_condition_id": "0xabc123",
            "outcome": "yes",
            "side": "buy",
            "amount": 200,
            "limit_price": 0.40,
        }, headers=h)
        assert resp.status_code == 200
        order_id = resp.json()["data"]["id"]

        # Check pending
        resp = client.get(f"/accounts/{account['id']}/orders", headers=h)
        assert len(resp.json()["data"]) == 1
        assert resp.json()["data"][0]["id"] == order_id

        # Cancel
        resp = client.delete(f"/accounts/{account['id']}/orders/{order_id}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "cancelled"

        # Cash unchanged (limit orders don't deduct on placement)
        resp = client.get(f"/accounts/{account['id']}/balance", headers=h)
        assert resp.json()["data"]["cash"] == starting_cash


@pytest.mark.integration
class TestMarketResolutionPayout:
    def test_buy_yes_resolve_yes_gets_payout(self, client_e2e):
        """Buy YES -> resolve YES -> verify $1/share credit."""
        from server.jobs.auto_resolve import auto_resolve_job
        from server.adapters.polymarket import Market, OrderBook

        client = client_e2e
        user = _register(client, "resolve-agent")
        account = _create_account(client, user["api_key"])
        h = _headers(user["api_key"])

        # Buy YES shares
        buy_resp = _buy(client, user["api_key"], account["id"],
                       amount=100.0, slug="e2e-test-market")
        shares = buy_resp.json()["data"]["trade"]["shares"]
        cash_after_buy = buy_resp.json()["data"]["account"]["cash"]

        # Simulate market resolution
        resolved = Market(
            condition_id="0xe2e",
            slug="e2e-test-market",
            question="Will this E2E test pass?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes_e2e", "outcome": "Yes"},
                {"token_id": "tok_no_e2e", "outcome": "No"},
            ],
            active=False, closed=True,
            volume=0, liquidity=0,
            end_date="2026-12-31", fee_rate_bps=200, tick_size=0.01,
        )
        from tests.conftest import MockPolymarketClient
        mock = MockPolymarketClient(resolved, OrderBook(bids=[], asks=[]))

        db = client.app.state.db
        count = auto_resolve_job(db, mock)
        assert count >= 1

        # Check balance — should have cash_after_buy + shares * $1
        updated_account = db.get_account(account["id"])
        expected = cash_after_buy + shares * 1.0
        assert float(updated_account["cash"]) == pytest.approx(expected)


@pytest.mark.integration
class TestLeaderboardQualification:
    def test_10_trades_qualifies_for_leaderboard(self, client):
        user, account, headers = _register_and_create_account(client, "qualify-bot")
        _insert_trades(client, account["id"], count=10)

        resp = client.get("/leaderboard")
        assert resp.status_code == 200
        lb = resp.json()["data"]
        assert any(e["agent_name"] == "qualify-bot" for e in lb)

    def test_9_trades_does_not_qualify(self, client):
        user, account, headers = _register_and_create_account(client, "almost-bot")
        _insert_trades(client, account["id"], count=9)

        resp = client.get("/leaderboard")
        lb = resp.json()["data"]
        assert not any(e["agent_name"] == "almost-bot" for e in lb)


@pytest.mark.integration
class TestMultiAccountIsolation:
    def test_two_accounts_independent_balances(self, client):
        user = _register(client, "iso-bot")
        h = _headers(user["api_key"])

        # Create 2 accounts
        resp1 = client.post("/accounts", json={"name": "acc1"}, headers=h)
        acc1_id = resp1.json()["data"]["id"]
        resp2 = client.post("/accounts", json={"name": "acc2"}, headers=h)
        acc2_id = resp2.json()["data"]["id"]

        # Buy on acc1 only
        buy_resp = _buy(client, user["api_key"], acc1_id, amount=50.0)
        assert buy_resp.status_code == 200

        # Check acc1 balance decreased
        b1 = client.get(f"/accounts/{acc1_id}/balance", headers=h).json()["data"]
        assert b1["cash"] < 10000

        # acc2 balance unchanged
        b2 = client.get(f"/accounts/{acc2_id}/balance", headers=h).json()["data"]
        assert b2["cash"] == 10000.0


@pytest.mark.integration
class TestErrorRecovery:
    def test_failed_buy_no_state_change(self, client_empty_book):
        """Buy fails (empty book) -> verify no state change."""
        user = _register(client_empty_book)
        account = _create_account(client_empty_book, user["api_key"])
        h = _headers(user["api_key"])
        starting_cash = account["cash"]

        # This should fail — empty book
        resp = _buy(client_empty_book, user["api_key"], account["id"])
        assert resp.status_code == 400

        # No state change
        balance = client_empty_book.get(
            f"/accounts/{account['id']}/balance", headers=h,
        ).json()["data"]
        assert balance["cash"] == starting_cash
        assert balance["positions_value"] == 0.0

        # No trades recorded
        history = client_empty_book.get(
            f"/accounts/{account['id']}/history", headers=h,
        ).json()["data"]
        assert len(history) == 0


@pytest.mark.integration
class TestWebsiteReflectsTrades:
    def test_buy_appears_on_account_page(self, client_e2e):
        client = client_e2e
        user = _register(client, "web-agent")
        h = _headers(user["api_key"])
        resp = client.post("/accounts", json={"name": "web-acct"}, headers=h)
        acc_id = resp.json()["data"]["id"]

        _buy(client, user["api_key"], acc_id, amount=100.0, slug="e2e-test-market")

        resp = client.get(f"/a/{acc_id}")
        assert resp.status_code == 200
        assert "yes" in resp.text.lower()


@pytest.mark.integration
class TestPKComparison:
    def test_pk_shows_correct_winner(self, client):
        """2 agents with different ROI -> PK shows correct values."""
        user1, acc1, h1 = _register_and_create_account(client, "pk-winner")
        user2, acc2, h2 = _register_and_create_account(client, "pk-loser")

        _insert_trades(client, acc1["id"], count=5)
        _insert_trades(client, acc2["id"], count=5)

        # Give winner more cash = higher ROI
        client.app.state.db.update_cash(acc1["id"], 12000)
        client.app.state.db.update_cash(acc2["id"], 8000)

        resp = client.get(f"/leaderboard/pk/{acc1['id']}/{acc2['id']}")
        assert resp.status_code == 200
        pk = resp.json()["data"]

        # Winner should have higher ROI
        assert pk["a"]["roi_pct"] > pk["b"]["roi_pct"]
        assert pk["a"]["total_value"] > pk["b"]["total_value"]


@pytest.mark.integration
class TestFullLifecycle:
    def test_register_to_leaderboard(self, client_e2e):
        """Full lifecycle: register -> account -> buy -> sell -> stats -> leaderboard -> profile -> website."""
        client = client_e2e

        # 1. Register
        resp = client.post("/auth/register", json={"agent_name": "lifecycle-bot"})
        assert resp.status_code == 200
        api_key = resp.json()["data"]["api_key"]
        h = {"Authorization": f"Bearer {api_key}"}

        # 2. Create account
        resp = client.post("/accounts", json={"name": "main"}, headers=h)
        acc_id = resp.json()["data"]["id"]

        # 3. Buy 10 times to qualify for leaderboard
        for _ in range(10):
            resp = _buy(client, api_key, acc_id, amount=50.0, slug="e2e-test-market")
            assert resp.status_code == 200

        # 4. Check stats
        resp = client.get(f"/accounts/{acc_id}/stats", headers=h)
        assert resp.json()["data"]["total_trades"] == 10

        # 5. Leaderboard
        resp = client.get("/leaderboard")
        lb = resp.json()["data"]
        assert any(e["agent_name"] == "lifecycle-bot" for e in lb)

        # 6. Profile
        resp = client.get("/leaderboard/users/lifecycle-bot")
        assert resp.json()["data"]["agent_name"] == "lifecycle-bot"

        # 7. Website
        resp = client.get("/")
        assert "lifecycle-bot" in resp.text
