"""Test SDK Agent class against real server."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from sdk import Agent
from sdk.client import AgentError
from server.app import app
from tests.conftest import MockPolymarketClient


@pytest.fixture
def tc():
    """TestClient that can be passed as http_client to Agent."""
    app.state.polymarket = MockPolymarketClient()
    app.state.scheduler = None
    with TestClient(app) as c:
        yield c


def _agent(tc, **kwargs):
    """Create an Agent using the TestClient as transport."""
    return Agent("http://testserver", http_client=tc, **kwargs)


class TestRegistration:
    def test_register_new_agent(self, tc):
        agent = _agent(tc, name="sdk-bot")
        assert agent.api_key.startswith("lb_sk_")
        assert agent.account_id is not None
        assert agent.agent_name == "sdk-bot"

    def test_register_with_model(self, tc):
        agent = _agent(tc, name="model-bot", model="claude-opus-4")
        assert agent.agent_name == "model-bot"

    def test_duplicate_name_raises(self, tc):
        _agent(tc, name="unique-bot")
        with pytest.raises(AgentError, match="already taken"):
            _agent(tc, name="unique-bot")

    def test_no_name_no_key_raises(self, tc):
        with pytest.raises(AgentError, match="Provide either"):
            _agent(tc)


class TestReconnect:
    def test_reconnect_with_api_key(self, tc):
        agent1 = _agent(tc, name="reconnect-bot")
        api_key = agent1.api_key
        account_id = agent1.account_id

        agent2 = _agent(tc, api_key=api_key)
        assert agent2.account_id == account_id

    def test_reconnect_creates_account_if_missing(self, tc):
        agent1 = _agent(tc, name="multi-acc-bot")
        api_key = agent1.api_key

        agent2 = _agent(tc, api_key=api_key, account_name="experimental")
        assert agent2.account_id != agent1.account_id

    def test_reconnect_invalid_key_raises(self, tc):
        with pytest.raises(AgentError, match="Invalid API key"):
            _agent(tc, api_key="lb_sk_bad_key")


class TestTrading:
    def test_buy(self, tc):
        agent = _agent(tc, name="trade-bot")
        result = agent.buy("will-bitcoin-hit-100k", "yes", 10.0)
        assert "trade" in result
        assert result["trade"]["side"] == "buy"

    def test_sell_after_buy(self, tc):
        agent = _agent(tc, name="sell-bot")
        buy_result = agent.buy("will-bitcoin-hit-100k", "yes", 10.0)
        shares = buy_result["trade"]["shares"]
        sell_result = agent.sell("will-bitcoin-hit-100k", "yes", shares)
        assert sell_result["trade"]["side"] == "sell"


class TestPortfolio:
    def test_portfolio_empty(self, tc):
        agent = _agent(tc, name="port-bot")
        positions = agent.portfolio()
        assert positions == []

    def test_portfolio_after_buy(self, tc):
        agent = _agent(tc, name="port-buy-bot")
        agent.buy("will-bitcoin-hit-100k", "yes", 10.0)
        positions = agent.portfolio()
        assert len(positions) == 1
        assert positions[0]["outcome"] == "yes"

    def test_balance(self, tc):
        agent = _agent(tc, name="bal-bot")
        bal = agent.balance()
        assert bal["cash"] == 10000.0
        assert bal["starting_balance"] == 10000.0

    def test_balance_after_buy(self, tc):
        agent = _agent(tc, name="bal-buy-bot")
        agent.buy("will-bitcoin-hit-100k", "yes", 10.0)
        bal = agent.balance()
        assert bal["cash"] < 10000.0

    def test_history_empty(self, tc):
        agent = _agent(tc, name="hist-bot")
        trades = agent.history()
        assert trades == []

    def test_history_after_trade(self, tc):
        agent = _agent(tc, name="hist-trade-bot")
        agent.buy("will-bitcoin-hit-100k", "yes", 10.0)
        trades = agent.history()
        assert len(trades) == 1

    def test_stats_after_trade(self, tc):
        agent = _agent(tc, name="stats-bot")
        agent.buy("will-bitcoin-hit-100k", "yes", 10.0)
        stats = agent.stats()
        assert "total_trades" in stats
        assert stats["total_trades"] == 1


class TestLeaderboard:
    def test_leaderboard_empty(self, tc):
        agent = _agent(tc, name="lb-sdk-bot")
        data = agent.leaderboard()
        assert data == []


class TestContextManager:
    def test_context_manager(self, tc):
        with _agent(tc, name="ctx-bot") as agent:
            bal = agent.balance()
            assert bal["cash"] == 10000.0

    def test_close_with_owned_client(self):
        """Agent that creates its own httpx.Client can close it."""
        # We can't connect to a real server, but we can verify close()
        # doesn't error when _owns_http is True.
        app.state.polymarket = MockPolymarketClient()
        app.state.scheduler = None
        with TestClient(app) as tc:
            agent = _agent(tc, name="owned-bot")
        # After TestClient closes, agent.close() is safe because _owns_http=False
        agent.close()

        # Now test with _owns_http=True path
        app.state.polymarket = MockPolymarketClient()
        app.state.scheduler = None
        with TestClient(app) as tc:
            agent = _agent(tc, name="force-close-bot")
            agent._owns_http = True
            agent.close()


class TestErrorPaths:
    def test_buy_insufficient_balance(self, tc):
        agent = _agent(tc, name="err-buy-bot")
        with pytest.raises(AgentError):
            agent.buy("will-bitcoin-hit-100k", "yes", 99999.0)

    def test_sell_no_position(self, tc):
        agent = _agent(tc, name="err-sell-bot")
        with pytest.raises(AgentError):
            agent.sell("will-bitcoin-hit-100k", "yes", 100.0)

    def test_resolve_account_finds_existing(self, tc):
        agent = _agent(tc, name="resolve-bot")
        api_key = agent.api_key
        agent2 = _agent(tc, api_key=api_key, account_name="default")
        assert agent2.account_id == agent.account_id

    def test_leaderboard_error(self, tc):
        """Cover leaderboard error branch (line 119)."""
        agent = _agent(tc, name="lb-err-bot")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "server error"}
        agent._http = MagicMock()
        agent._http.get.return_value = mock_resp
        with pytest.raises(AgentError, match="server error"):
            agent.leaderboard()

    def test_register_non_409_error(self, tc):
        """Cover _register error branch (line 136)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"ok": False, "error": "internal error"}
        mock_http = MagicMock()
        mock_http.post.return_value = mock_resp
        with pytest.raises(AgentError, match="internal error"):
            Agent("http://testserver", http_client=mock_http, name="fail-bot")

    def test_create_account_error(self, tc):
        """Cover _create_account error branch (line 149)."""
        agent = _agent(tc, name="acct-err-bot")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "db error"}
        agent._http = MagicMock()
        agent._http.post.return_value = mock_resp
        with pytest.raises(AgentError, match="db error"):
            agent._create_account("broken")

    def test_resolve_account_list_error(self, tc):
        """Cover _resolve_account error branch (line 158)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False, "error": "list failed"}
        mock_http = MagicMock()
        mock_http.get.return_value = mock_resp
        with pytest.raises(AgentError, match="list failed"):
            Agent("http://testserver", http_client=mock_http, api_key="lb_sk_x")

    def test_get_error(self, tc):
        """Cover _get error branch (line 171)."""
        agent = _agent(tc, name="get-err-bot")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "error": "not found", "code": "NOT_FOUND"}
        agent._http = MagicMock()
        agent._http.get.return_value = mock_resp
        with pytest.raises(AgentError, match="not found"):
            agent.portfolio()
