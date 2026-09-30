"""Tests for limit order CRUD routes."""
from __future__ import annotations

import json

import pytest

import server.routes.orders as orders_mod

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

    def test_idor_cancel_other_users_order_is_404(self, client):
        """Cross-account cancel: order_id not under the path's account → 404."""
        user1, account1, headers1 = _register_and_create_account(client, "idor-bot")
        user2, account2, headers2 = _register_and_create_account(client, "victim-bot")
        resp = client.post(f"/accounts/{account2['id']}/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 500,
            "limit_price": 0.45,
        }, headers=headers2)
        order_id = resp.json()["data"]["id"]
        resp = client.delete(
            f"/accounts/{account1['id']}/orders/{order_id}", headers=headers1
        )
        assert resp.status_code == 404
        pending = client.get(
            f"/accounts/{account2['id']}/orders", headers=headers2
        ).json()["data"]
        assert [o["id"] for o in pending] == [order_id]

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


class TestPlaceOrderValidation:
    BODY = {
        "market_slug": "will-bitcoin-hit-100k",
        "market_condition_id": "0xabc123",
        "outcome": "yes",
        "side": "buy",
        "amount": 500,
        "limit_price": 0.45,
    }

    def _post(self, client, headers, account, **over):
        return client.post(
            f"/accounts/{account['id']}/orders", json={**self.BODY, **over},
            headers=headers,
        )

    def test_identity_comes_from_market_not_client(self, client):
        _, account, headers = _register_and_create_account(client, "order-bot")
        resp = self._post(
            client, headers, account,
            market_slug="forged-slug", market_condition_id="0xforged", outcome=" YES ",
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["market_condition_id"] == "0xabc123"
        assert data["market_slug"] == "will-bitcoin-hit-100k"
        assert data["outcome"] == "yes"

    def test_invalid_outcome_rejected(self, client):
        _, account, headers = _register_and_create_account(client, "order-bot")
        resp = self._post(client, headers, account, outcome="zzz")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_OUTCOME"

    def test_closed_market_rejected(self, client_closed_market):
        c = client_closed_market
        _, account, headers = _register_and_create_account(c, "order-bot")
        resp = self._post(c, headers, account, outcome="yes")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "MARKET_CLOSED"

    @pytest.mark.parametrize("field", ["amount", "limit_price"])
    @pytest.mark.parametrize("bad", ["NaN", "Infinity"])
    def test_non_finite_rejected(self, client, field, bad):
        _, account, headers = _register_and_create_account(client, "order-bot")
        raw = json.dumps({**self.BODY, field: "@@"}).replace('"@@"', bad)
        resp = client.post(
            f"/accounts/{account['id']}/orders", content=raw,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    def test_non_positive_amount_rejected(self, client):
        _, account, headers = _register_and_create_account(client, "order-bot")
        assert self._post(client, headers, account, amount=0).status_code == 422

    @pytest.mark.parametrize("raw,expected", [
        ("2026-12-31T23:59:59Z", "2026-12-31T23:59:59+00:00"),
        ("2026-12-31", "2026-12-31T00:00:00+00:00"),
        ("2026-12-31T23:59:59+02:00", "2026-12-31T21:59:59+00:00"),
    ])
    def test_expires_at_normalized_to_utc(self, client, raw, expected):
        _, account, headers = _register_and_create_account(client, "order-bot")
        resp = self._post(client, headers, account, order_type="gtd", expires_at=raw)
        assert resp.json()["data"]["expires_at"] == expected

    def test_expires_at_omitted_for_gtc(self, client):
        _, account, headers = _register_and_create_account(client, "order-bot")
        assert self._post(client, headers, account).json()["data"]["expires_at"] is None
        resp = self._post(client, headers, account, expires_at=None)
        assert resp.json()["data"]["expires_at"] is None

    @pytest.mark.parametrize("bad", ["zzz", "0", ""])
    def test_garbage_expires_at_rejected(self, client, bad):
        _, account, headers = _register_and_create_account(client, "order-bot")
        resp = self._post(client, headers, account, order_type="gtd", expires_at=bad)
        assert resp.status_code == 422

    def test_pending_orders_capped(self, client, monkeypatch):
        monkeypatch.setattr(orders_mod, "MAX_PENDING_ORDERS", 2)
        _, account, headers = _register_and_create_account(client, "order-bot")
        assert self._post(client, headers, account).status_code == 200
        assert self._post(client, headers, account).status_code == 200
        resp = self._post(client, headers, account)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "TOO_MANY_ORDERS"

    def test_upstream_unavailable(self, client):
        client.app.state.polymarket = None
        _, account, headers = _register_and_create_account(client, "order-bot")
        resp = self._post(client, headers, account)
        assert resp.status_code == 503
