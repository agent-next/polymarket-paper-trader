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
