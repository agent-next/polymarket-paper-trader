"""Test website pages."""
from __future__ import annotations

import pytest

from tests.conftest import (
    _register, _headers, _register_and_create_account, _insert_trades,
)


class TestHomepage:
    def test_homepage_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Leaderboard" in resp.text

    def test_homepage_no_entries(self, client):
        resp = client.get("/")
        assert "No qualified accounts" in resp.text

    def test_homepage_shows_model_column(self, client):
        _, account, _ = _register_and_create_account(client, "model-col-bot")
        _insert_trades(client, account["id"], count=10)
        resp = client.get("/")
        assert "Model" in resp.text

    def test_homepage_shows_activity_feed(self, client):
        resp = client.get("/")
        assert "Activity Feed" in resp.text

    def test_homepage_feed_with_trades(self, client):
        _, account, _ = _register_and_create_account(client, "feed-web-bot")
        _insert_trades(client, account["id"], count=2)
        resp = client.get("/")
        assert "feed-web-bot" in resp.text


class TestUserPage:
    def test_user_page(self, client):
        _register(client, "web-bot")
        resp = client.get("/u/web-bot")
        assert resp.status_code == 200
        assert "web-bot" in resp.text

    def test_user_not_found(self, client):
        resp = client.get("/u/nonexistent")
        assert resp.status_code == 404


class TestAccountPage:
    def test_account_page(self, client):
        resp = client.post("/auth/register", json={"agent_name": "acc-bot"})
        headers = {"Authorization": f"Bearer {resp.json()['data']['api_key']}"}
        resp = client.post("/accounts", json={"name": "default"}, headers=headers)
        account_id = resp.json()["data"]["id"]
        resp = client.get(f"/a/{account_id}")
        assert resp.status_code == 200
        assert "default" in resp.text

    def test_account_not_found(self, client):
        resp = client.get("/a/999")
        assert resp.status_code == 404


class TestHealthCheck:
    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
