"""Test leaderboard routes."""
from __future__ import annotations

import pytest

from tests.conftest import _register_and_create_account, _insert_trades


class TestLeaderboard:
    def test_leaderboard_empty(self, client):
        resp = client.get("/leaderboard")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["data"] == []

    def test_leaderboard_below_minimum(self, client):
        user, account, headers = _register_and_create_account(client, "lb-bot")
        _insert_trades(client, account["id"], count=5)
        resp = client.get("/leaderboard")
        assert resp.json()["data"] == []

    def test_leaderboard_qualified(self, client):
        user, account, headers = _register_and_create_account(client, "lb-bot")
        _insert_trades(client, account["id"], count=10)
        resp = client.get("/leaderboard")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["agent_name"] == "lb-bot"
        assert data[0]["trade_count"] >= 10
        assert "roi_pct" in data[0]
        assert "tier" in data[0]
        assert "model" in data[0]

    def test_leaderboard_sorted_by_roi(self, client):
        # Create two users with different performance
        user1, acc1, h1 = _register_and_create_account(client, "bot-a")
        user2, acc2, h2 = _register_and_create_account(client, "bot-b")
        _insert_trades(client, acc1["id"], count=10)
        _insert_trades(client, acc2["id"], count=10)
        # Give bot-b more cash to simulate better performance
        client.app.state.db.update_cash(acc2["id"], 12000)
        resp = client.get("/leaderboard")
        data = resp.json()["data"]
        assert len(data) == 2
        # bot-b should rank higher (more cash = higher ROI)
        assert data[0]["agent_name"] == "bot-b"

    def test_leaderboard_counts_open_positions_in_total_value(self, client):
        user, account, headers = _register_and_create_account(client, "positions-bot")
        _insert_trades(client, account["id"], count=10)

        db = client.app.state.db
        db.update_cash(account["id"], 9000.0)
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc123",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.5,
            total_cost=50.0,
            realized_pnl=0.0,
        )

        resp = client.get("/leaderboard")
        data = resp.json()["data"]
        assert len(data) == 1
        # Mock midpoint in tests is 0.65 -> position value 65.
        # Total value = cash 9000 + 65 => pnl = -935 from 10k start.
        assert data[0]["total_pnl"] == pytest.approx(-935.0)
        assert data[0]["roi_pct"] == pytest.approx(-9.35)

    def test_leaderboard_positions_value_graceful_when_polymarket_missing(self, client):
        user, account, headers = _register_and_create_account(client, "no-poly-bot")
        _insert_trades(client, account["id"], count=10)
        client.app.state.polymarket = None

        resp = client.get("/leaderboard")
        data = resp.json()["data"]
        assert len(data) == 1
        # With no polymarket client, open positions are treated as 0 mark-to-market.
        assert data[0]["total_pnl"] == pytest.approx(0.0)

    def test_leaderboard_positions_value_skips_position_errors(self, client):
        user, account, headers = _register_and_create_account(client, "skip-pos-bot")
        _insert_trades(client, account["id"], count=10)
        db = client.app.state.db
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xbad",
            market_slug="bad-market",
            market_question="Bad?",
            outcome="yes",
            shares=100.0,
            avg_entry_price=0.5,
            total_cost=50.0,
            realized_pnl=0.0,
        )

        class BrokenPoly:
            def get_market(self, slug):
                raise RuntimeError("boom")

            def get_midpoint(self, token_id):
                return 0.5

        client.app.state.polymarket = BrokenPoly()
        resp = client.get("/leaderboard")
        data = resp.json()["data"]
        assert len(data) == 1
        # Position lookup failure is swallowed; ranking still responds.
        assert data[0]["agent_name"] == "skip-pos-bot"


class TestUserProfile:
    def test_user_profile(self, client):
        user, account, headers = _register_and_create_account(client, "lb-bot")
        resp = client.get(f"/leaderboard/users/{user['agent_name']}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["agent_name"] == user["agent_name"]
        assert "model" in data
        assert len(data["accounts"]) == 1

    def test_user_not_found(self, client):
        resp = client.get("/leaderboard/users/nonexistent")
        assert resp.status_code == 404


class TestPK:
    def test_head_to_head(self, client):
        user1, acc1, h1 = _register_and_create_account(client, "pk-bot-a")
        user2, acc2, h2 = _register_and_create_account(client, "pk-bot-b")
        _insert_trades(client, acc1["id"], count=5)
        _insert_trades(client, acc2["id"], count=5)
        resp = client.get(f"/leaderboard/pk/{acc1['id']}/{acc2['id']}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "a" in data
        assert "b" in data

    def test_pk_account_not_found(self, client):
        user1, acc1, h1 = _register_and_create_account(client, "pk-bot-c")
        resp = client.get(f"/leaderboard/pk/{acc1['id']}/999")
        assert resp.status_code == 404


class TestActivityFeed:
    def test_feed_empty(self, client):
        resp = client.get("/leaderboard/feed")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["data"] == []

    def test_feed_with_trades(self, client):
        user, account, headers = _register_and_create_account(client, "feed-bot")
        _insert_trades(client, account["id"], count=3)
        resp = client.get("/leaderboard/feed")
        data = resp.json()["data"]
        assert len(data) == 3
        assert data[0]["agent_name"] == "feed-bot"

    def test_feed_limit(self, client):
        user, account, headers = _register_and_create_account(client, "feed-limit-bot")
        _insert_trades(client, account["id"], count=5)
        resp = client.get("/leaderboard/feed?limit=2")
        data = resp.json()["data"]
        assert len(data) == 2

    def test_feed_limit_clamped(self, client):
        resp = client.get("/leaderboard/feed?limit=0")
        assert resp.status_code == 200
        resp = client.get("/leaderboard/feed?limit=999")
        assert resp.status_code == 200
