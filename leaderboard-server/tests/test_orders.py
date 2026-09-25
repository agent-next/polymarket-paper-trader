"""Tests for limit order CRUD routes."""
from __future__ import annotations

import pytest

from tests.conftest import _register_and_create_account, _headers


class TestPlaceOrder:
    def test_place_gtc_order(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test-market",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["data"]["status"] == "pending"
        assert data["data"]["limit_price"] == 0.45

    def test_place_gtd_order(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test-market",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
            "order_type": "gtd",
            "expires_at": "2026-12-31T23:59:59Z",
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["order_type"] == "gtd"

    def test_gtd_without_expires_at(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test-market",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
            "order_type": "gtd",
        }, headers=headers)
        assert resp.status_code == 400

    def test_invalid_price(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test-market",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 1.5,
        }, headers=headers)
        assert resp.status_code == 422

    def test_invalid_side(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test-market",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "hold",
            "amount": 500,
            "limit_price": 0.45,
        }, headers=headers)
        assert resp.status_code == 422

    def test_not_your_account(self, client):
        user1, account1, headers1 = _register_and_create_account(client, "order-bot")
        resp = client.post("/auth/register", json={"agent_name": "other-bot"})
        user2 = resp.json()["data"]
        headers2 = {"Authorization": f"Bearer {user2['api_key']}"}
        resp = client.post(f"/accounts/{account1['id']}/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
        }, headers=headers2)
        assert resp.status_code == 403


class TestListOrders:
    def test_list_empty(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.get(f"/accounts/{account['id']}/orders", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_list_pending(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
        }, headers=headers)
        resp = client.get(f"/accounts/{account['id']}/orders", headers=headers)
        assert len(resp.json()["data"]) == 1


class TestCancelOrder:
    def test_cancel_success(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
        }, headers=headers)
        order_id = resp.json()["data"]["id"]
        resp = client.delete(f"/accounts/{account['id']}/orders/{order_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "cancelled"

    def test_cancel_nonexistent(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.delete(f"/accounts/{account['id']}/orders/999", headers=headers)
        assert resp.status_code == 404

    def test_cancel_already_cancelled(self, client):
        user, account, headers = _register_and_create_account(client, "order-bot")
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
        }, headers=headers)
        order_id = resp.json()["data"]["id"]
        client.delete(f"/accounts/{account['id']}/orders/{order_id}", headers=headers)
        resp = client.delete(f"/accounts/{account['id']}/orders/{order_id}", headers=headers)
        assert resp.status_code == 404
