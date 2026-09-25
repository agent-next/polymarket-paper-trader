"""Contract tests: validate JSON response shapes for every endpoint.

These tests ensure API consumers get consistent, predictable responses.
All @pytest.mark.integration — they require a running TestClient.
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
class TestRegisterContract:
    def test_register_response_shape(self, client):
        resp = client.post("/auth/register", json={"agent_name": "contract-bot"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert isinstance(data["api_key"], str)
        assert data["api_key"].startswith("lb_sk_")
        assert isinstance(data["agent_name"], str)
        assert isinstance(data["user_id"], int)


@pytest.mark.integration
class TestAccountContract:
    def test_create_account_response_shape(self, client):
        user = _register(client)
        resp = client.post(
            "/accounts", json={"name": "default"},
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert isinstance(data["id"], int)
        assert isinstance(data["cash"], (int, float))
        assert data["cash"] == 10000.0
        assert isinstance(data["starting_balance"], (int, float))
        assert "created_at" in data
        assert "user_id" in data


@pytest.mark.integration
class TestBuyContract:
    def test_buy_response_shape(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        resp = _buy(client, user["api_key"], account["id"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        trade = body["data"]["trade"]
        assert isinstance(trade["id"], int)
        assert trade["side"] == "buy"
        assert isinstance(trade["outcome"], str)
        assert isinstance(trade["market_slug"], str)
        assert isinstance(trade["avg_price"], (int, float))
        assert isinstance(trade["amount_usd"], (int, float))
        assert isinstance(trade["shares"], (int, float))
        assert isinstance(trade["fee"], (int, float))
        assert isinstance(trade["fee_rate_bps"], int)
        assert isinstance(trade["slippage"], (int, float))
        assert isinstance(trade["levels_filled"], int)
        assert "is_partial" in trade
        assert "created_at" in trade

        acct = body["data"]["account"]
        assert isinstance(acct["id"], int)
        assert isinstance(acct["cash"], (int, float))


@pytest.mark.integration
class TestSellContract:
    def test_sell_response_shape(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        buy_resp = _buy(client, user["api_key"], account["id"])
        shares = buy_resp.json()["data"]["trade"]["shares"]

        resp = _sell(client, user["api_key"], account["id"], shares=shares)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        trade = body["data"]["trade"]
        assert trade["side"] == "sell"
        assert isinstance(trade["shares"], (int, float))
        assert isinstance(trade["avg_price"], (int, float))

        acct = body["data"]["account"]
        assert isinstance(acct["cash"], (int, float))


@pytest.mark.integration
class TestPortfolioContract:
    def test_portfolio_response_shape(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        _buy(client, user["api_key"], account["id"])

        resp = client.get(
            f"/accounts/{account['id']}/portfolio",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) >= 1

        pos = body["data"][0]
        assert isinstance(pos["outcome"], str)
        assert isinstance(pos["shares"], (int, float))
        assert isinstance(pos["live_price"], (int, float))
        assert isinstance(pos["current_value"], (int, float))
        assert isinstance(pos["unrealized_pnl"], (int, float))
        assert "market_slug" in pos

    def test_portfolio_empty_shape(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        resp = client.get(
            f"/accounts/{account['id']}/portfolio",
            headers=_headers(user["api_key"]),
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["data"] == []


@pytest.mark.integration
class TestBalanceContract:
    def test_balance_response_shape(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        resp = client.get(
            f"/accounts/{account['id']}/balance",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        data = body["data"]
        for key in ("cash", "positions_value", "total_value",
                    "starting_balance", "pnl", "roi_pct"):
            assert key in data
            assert isinstance(data[key], (int, float))


@pytest.mark.integration
class TestHistoryContract:
    def test_history_response_shape(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        _buy(client, user["api_key"], account["id"])

        resp = client.get(
            f"/accounts/{account['id']}/history",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1

        trade = body["data"][0]
        assert isinstance(trade["id"], int)
        assert "side" in trade
        assert "outcome" in trade
        assert "created_at" in trade


@pytest.mark.integration
class TestStatsContract:
    def test_stats_response_shape(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        _buy(client, user["api_key"], account["id"])

        resp = client.get(
            f"/accounts/{account['id']}/stats",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        stats = body["data"]
        for key in ("total_trades", "buy_count", "sell_count", "win_rate",
                    "sharpe_ratio", "max_drawdown", "total_fees", "avg_trade_size",
                    "starting_balance", "cash", "positions_value", "total_value",
                    "pnl", "roi_pct"):
            assert key in stats


@pytest.mark.integration
class TestLeaderboardContract:
    def test_leaderboard_response_shape(self, client):
        user, account, headers = _register_and_create_account(client, "lb-contract")
        _insert_trades(client, account["id"], count=10)

        resp = client.get("/leaderboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) >= 1

        entry = body["data"][0]
        for key in ("agent_name", "account_id", "account_name", "trade_count",
                    "roi_pct", "total_pnl", "sharpe_ratio", "tier"):
            assert key in entry
        assert entry["tier"] in ("bronze", "silver", "gold", "diamond")

    def test_leaderboard_empty_shape(self, client):
        body = client.get("/leaderboard").json()
        assert body["ok"] is True
        assert body["data"] == []


@pytest.mark.integration
class TestUserProfileContract:
    def test_user_profile_response_shape(self, client):
        user, account, headers = _register_and_create_account(client, "profile-bot")
        resp = client.get(f"/leaderboard/users/profile-bot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["agent_name"] == "profile-bot"
        assert "created_at" in data
        assert isinstance(data["accounts"], list)
        assert len(data["accounts"]) == 1
        acct = data["accounts"][0]
        for key in ("account_id", "account_name", "trade_count",
                    "roi_pct", "total_pnl", "sharpe_ratio"):
            assert key in acct


@pytest.mark.integration
class TestErrorContract:
    def test_error_response_shape_400(self, client):
        user = _register(client)
        account = _create_account(client, user["api_key"])
        # Sell without position → 400
        resp = _sell(client, user["api_key"], account["id"], shares=10.0)
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "code" in detail
        assert "error" in detail

    def test_error_response_shape_404(self, client):
        resp = client.get("/leaderboard/users/nonexistent")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_error_response_shape_401(self, client):
        resp = client.post(
            "/accounts", json={"name": "default"},
            headers={"Authorization": "Bearer fake_key"},
        )
        assert resp.status_code == 401
