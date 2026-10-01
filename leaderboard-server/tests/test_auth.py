"""Test auth routes."""
from __future__ import annotations

import pytest

from tests.conftest import _register, _headers


class TestRegister:
    def test_register_success(self, client):
        resp = client.post("/auth/register", json={"agent_name": "test-bot"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["agent_name"] == "test-bot"
        assert data["data"]["api_key"].startswith("lb_sk_")
        assert data["data"]["model"] is None

    def test_register_with_model(self, client):
        resp = client.post("/auth/register", json={
            "agent_name": "claude-bot",
            "model": "claude-opus-4",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["model"] == "claude-opus-4"

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


    def test_register_name_too_long(self, client):
        resp = client.post("/auth/register", json={"agent_name": "x" * 101})
        assert resp.status_code == 422

    def test_register_model_too_long(self, client):
        resp = client.post("/auth/register", json={"agent_name": "ok", "model": "m" * 101})
        assert resp.status_code == 422

    def test_register_internal_error_not_409(self, client):
        def boom(*a, **k):
            raise RuntimeError("db down")

        client.app.state.db.create_user = boom
        with pytest.raises(RuntimeError):
            client.post("/auth/register", json={"agent_name": "bot"})

    def test_missing_auth_header_is_401(self, client):
        resp = client.patch("/auth/model", json={"model": "gpt-4o"})
        assert resp.status_code == 401


class TestUpdateModel:
    def test_update_model(self, client):
        user = _register(client, "model-bot")
        resp = client.patch(
            "/auth/model",
            json={"model": "gpt-4o"},
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["model"] == "gpt-4o"

    def test_update_model_empty(self, client):
        user = _register(client, "empty-model-bot")
        resp = client.patch(
            "/auth/model",
            json={"model": ""},
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 422

    def test_update_model_invalid_key(self, client):
        resp = client.patch(
            "/auth/model",
            json={"model": "gpt-4o"},
            headers=_headers("lb_sk_fake"),
        )
        assert resp.status_code == 401
