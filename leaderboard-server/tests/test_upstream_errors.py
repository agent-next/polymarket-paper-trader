"""Upstream failures and bad numeric input return JSON errors, never 500s."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.adapters.polymarket import ApiError, MarketNotFoundError
from server.app import app
from tests.conftest import (
    MockPolymarketClient,
    _buy,
    _register_and_create_account,
    _sell,
)


class _RaisingClient(MockPolymarketClient):
    def __init__(self, exc, where="get_market", **kw):
        super().__init__(**kw)
        self._exc = exc
        self._where = where

    def get_market(self, slug):
        if self._where == "get_market":
            raise self._exc
        return super().get_market(slug)

    def get_order_book(self, token_id):
        if self._where == "get_order_book":
            raise self._exc
        return super().get_order_book(token_id)

    def get_midpoint(self, token_id):
        if self._where == "get_midpoint":
            raise self._exc
        return super().get_midpoint(token_id)


def _client(pm):
    app.state.polymarket = pm
    app.state.scheduler = None
    return TestClient(app)


def _detail(resp):
    return resp.json()["detail"]


class TestTradeUpstream:
    def test_no_client_is_503(self):
        with _client(None) as c:
            _, acct, h = _register_and_create_account(c)
            key = h["Authorization"].split()[1]
            for resp in (_buy(c, key, acct["id"]), _sell(c, key, acct["id"], 1.0)):
                assert resp.status_code == 503
                assert _detail(resp)["code"] == "UPSTREAM_UNAVAILABLE"

    def test_unknown_market_is_404(self):
        with _client(_RaisingClient(MarketNotFoundError("nope"))) as c:
            _, acct, h = _register_and_create_account(c)
            resp = _buy(c, h["Authorization"].split()[1], acct["id"])
            assert resp.status_code == 404
            assert _detail(resp)["code"] == "MARKET_NOT_FOUND"

    def test_api_error_is_503(self):
        with _client(_RaisingClient(ApiError("boom"))) as c:
            _, acct, h = _register_and_create_account(c)
            resp = _buy(c, h["Authorization"].split()[1], acct["id"])
            assert resp.status_code == 503
            assert _detail(resp)["code"] == "UPSTREAM_UNAVAILABLE"

    def test_unexpected_error_is_503(self):
        with _client(_RaisingClient(ConnectionError("down"), "get_order_book")) as c:
            _, acct, h = _register_and_create_account(c)
            resp = _buy(c, h["Authorization"].split()[1], acct["id"])
            assert resp.status_code == 503
            assert _detail(resp)["code"] == "UPSTREAM_UNAVAILABLE"


class TestPortfolioUpstream:
    def _with_position(self, c):
        _, acct, h = _register_and_create_account(c)
        assert _buy(c, h["Authorization"].split()[1], acct["id"]).status_code == 200
        return acct, h

    def test_midpoint_failure_is_503(self):
        with _client(MockPolymarketClient()) as c:
            acct, h = self._with_position(c)
            app.state.polymarket = _RaisingClient(ConnectionError("x"), "get_midpoint")
            for path in ("portfolio", "balance", "stats"):
                resp = c.get(f"/accounts/{acct['id']}/{path}", headers=h)
                assert resp.status_code == 503
                assert _detail(resp)["code"] == "UPSTREAM_UNAVAILABLE"

    def test_no_client_with_positions_is_503(self):
        with _client(MockPolymarketClient()) as c:
            acct, h = self._with_position(c)
            app.state.polymarket = None
            resp = c.get(f"/accounts/{acct['id']}/portfolio", headers=h)
            assert resp.status_code == 503

    def test_no_client_without_positions_ok(self):
        with _client(None) as c:
            _, acct, h = _register_and_create_account(c)
            assert c.get(f"/accounts/{acct['id']}/portfolio", headers=h).status_code == 200


class TestNonFiniteNumbers:
    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_buy_and_sell_reject_non_finite(self, client, bad):
        _, acct, h = _register_and_create_account(client)
        for path, field in (("buy", "amount_usd"), ("sell", "shares")):
            body = (
                '{"account_id": %d, "market_slug": "will-bitcoin-hit-100k", '
                '"outcome": "yes", "%s": %s}' % (acct["id"], field, bad)
            )
            resp = client.post(
                f"/trade/{path}", content=body,
                headers={**h, "Content-Type": "application/json"},
            )
            assert resp.status_code == 422
