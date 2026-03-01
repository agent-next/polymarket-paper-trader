"""Test account routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from server.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _register(client, name="test-bot"):
    resp = client.post("/auth/register", json={"agent_name": name})
    return resp.json()["data"]


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


class TestCreateAccount:
    def test_create_account(self, client):
        user = _register(client)
        resp = client.post("/accounts", json={"name": "default"},
                          headers=_headers(user["api_key"]))
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["cash"] == 10000.0
        assert data["data"]["starting_balance"] == 10000.0

    def test_create_account_default_name(self, client):
        user = _register(client)
        resp = client.post("/accounts", json={},
                          headers=_headers(user["api_key"]))
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "default"

    def test_create_duplicate_account(self, client):
        user = _register(client)
        h = _headers(user["api_key"])
        client.post("/accounts", json={"name": "acc1"}, headers=h)
        resp = client.post("/accounts", json={"name": "acc1"}, headers=h)
        assert resp.status_code == 409

    def test_unauthorized(self, client):
        resp = client.post("/accounts", json={"name": "default"})
        assert resp.status_code == 422  # missing header

    def test_bad_api_key(self, client):
        resp = client.post("/accounts", json={"name": "default"},
                          headers={"Authorization": "Bearer fake"})
        assert resp.status_code == 401


class TestListAccounts:
    def test_list_accounts(self, client):
        user = _register(client)
        h = _headers(user["api_key"])
        client.post("/accounts", json={"name": "acc1"}, headers=h)
        client.post("/accounts", json={"name": "acc2"}, headers=h)
        resp = client.get("/accounts", headers=h)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    def test_list_empty(self, client):
        user = _register(client)
        resp = client.get("/accounts", headers=_headers(user["api_key"]))
        assert resp.json()["data"] == []


class TestGetAccount:
    def test_get_account(self, client):
        user = _register(client)
        h = _headers(user["api_key"])
        create_resp = client.post("/accounts", json={"name": "default"}, headers=h)
        account_id = create_resp.json()["data"]["id"]
        resp = client.get(f"/accounts/{account_id}", headers=h)
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "default"

    def test_get_account_not_found(self, client):
        user = _register(client)
        resp = client.get("/accounts/999", headers=_headers(user["api_key"]))
        assert resp.status_code == 404

    def test_get_other_users_account(self, client):
        user1 = _register(client, "bot1")
        user2 = _register(client, "bot2")
        create_resp = client.post("/accounts", json={"name": "default"},
                                 headers=_headers(user1["api_key"]))
        account_id = create_resp.json()["data"]["id"]
        resp = client.get(f"/accounts/{account_id}", headers=_headers(user2["api_key"]))
        assert resp.status_code == 403
