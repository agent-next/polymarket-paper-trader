"""Test auth routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from server.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestRegister:
    def test_register_success(self, client):
        resp = client.post("/auth/register", json={"agent_name": "test-bot"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["agent_name"] == "test-bot"
        assert data["data"]["api_key"].startswith("lb_sk_")

    def test_register_duplicate(self, client):
        client.post("/auth/register", json={"agent_name": "dupe"})
        resp = client.post("/auth/register", json={"agent_name": "dupe"})
        assert resp.status_code == 409

    def test_register_empty_name(self, client):
        resp = client.post("/auth/register", json={"agent_name": ""})
        assert resp.status_code == 422

    def test_register_whitespace_name(self, client):
        resp = client.post("/auth/register", json={"agent_name": "   "})
        assert resp.status_code == 422
