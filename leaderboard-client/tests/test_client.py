"""Tests for pm_leaderboard_client.client."""
from __future__ import annotations

import httpx
import pytest
import respx

from pm_leaderboard_client import Agent, AgentError

BASE = "http://lb.test"


def _ok(data):
    return httpx.Response(200, json={"ok": True, "data": data})


class TestConstruction:
    def test_requires_name_or_key(self):
        with pytest.raises(AgentError, match="Provide either"):
            Agent(BASE)

    @respx.mock
    def test_register_new_agent(self):
        respx.post(f"{BASE}/auth/register").mock(
            return_value=_ok({"api_key": "lb_sk_x", "agent_name": "bot"})
        )
        respx.post(f"{BASE}/accounts").mock(return_value=_ok({"id": 7}))
        agent = Agent(BASE, name="bot", model="claude-opus-4")
        assert agent.api_key == "lb_sk_x"
        assert agent.account_id == 7
        assert agent.agent_name == "bot"

    @respx.mock
    def test_register_name_taken(self):
        respx.post(f"{BASE}/auth/register").mock(
            return_value=httpx.Response(409, json={"detail": "taken"})
        )
        with pytest.raises(AgentError, match="already taken") as e:
            Agent(BASE, name="bot")
        assert e.value.code == "AGENT_NAME_TAKEN"

    @respx.mock
    def test_register_not_ok(self):
        respx.post(f"{BASE}/auth/register").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "nope"})
        )
        with pytest.raises(AgentError, match="nope"):
            Agent(BASE, name="bot")

    @respx.mock
    def test_account_creation_failure(self):
        respx.post(f"{BASE}/auth/register").mock(
            return_value=_ok({"api_key": "lb_sk_x", "agent_name": "bot"})
        )
        respx.post(f"{BASE}/accounts").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "no account"})
        )
        with pytest.raises(AgentError, match="no account"):
            Agent(BASE, name="bot")


class TestReconnect:
    @respx.mock
    def test_matching_account(self):
        respx.get(f"{BASE}/accounts").mock(
            return_value=_ok([{"name": "default", "id": 3}])
        )
        agent = Agent(BASE, api_key="lb_sk_x")
        assert agent.account_id == 3

    @respx.mock
    def test_no_match_creates_account(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([]))
        respx.post(f"{BASE}/accounts").mock(return_value=_ok({"id": 9}))
        agent = Agent(BASE, api_key="lb_sk_x")
        assert agent.account_id == 9

    @respx.mock
    def test_invalid_key(self):
        respx.get(f"{BASE}/accounts").mock(return_value=httpx.Response(401))
        with pytest.raises(AgentError, match="Invalid API key") as e:
            Agent(BASE, api_key="bad")
        assert e.value.code == "INVALID_API_KEY"

    @respx.mock
    def test_list_not_ok(self):
        respx.get(f"{BASE}/accounts").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "boom"})
        )
        with pytest.raises(AgentError, match="boom"):
            Agent(BASE, api_key="lb_sk_x")


class TestOperations:
    def _agent(self):
        return Agent(BASE, api_key="lb_sk_x", http_client=httpx.Client(base_url=BASE))

    @respx.mock
    def test_trading_and_portfolio(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.post(f"{BASE}/trade/buy").mock(return_value=_ok({"filled": 1}))
        respx.post(f"{BASE}/trade/sell").mock(return_value=_ok({"filled": 2}))
        respx.get(f"{BASE}/accounts/1/portfolio").mock(return_value=_ok([{"m": "x"}]))
        respx.get(f"{BASE}/accounts/1/balance").mock(return_value=_ok({"cash": 10}))
        respx.get(f"{BASE}/accounts/1/history").mock(return_value=_ok([{"t": 1}]))
        respx.get(f"{BASE}/accounts/1/stats").mock(return_value=_ok({"roi": 1.0}))
        agent = self._agent()
        assert agent.buy("m", "yes", 100) == {"filled": 1}
        assert agent.sell("m", "yes", 5) == {"filled": 2}
        assert agent.portfolio() == [{"m": "x"}]
        assert agent.balance() == {"cash": 10}
        assert agent.history(limit=5) == [{"t": 1}]
        assert agent.stats() == {"roi": 1.0}

    @respx.mock
    def test_leaderboard_ok(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.get(f"{BASE}/leaderboard").mock(return_value=_ok([{"rank": 1}]))
        assert self._agent().leaderboard() == [{"rank": 1}]

    @respx.mock
    def test_leaderboard_error(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.get(f"{BASE}/leaderboard").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "x", "code": "C"})
        )
        with pytest.raises(AgentError) as e:
            self._agent().leaderboard()
        assert e.value.code == "C"

    @respx.mock
    def test_get_error_envelope(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.get(f"{BASE}/accounts/1/stats").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "nope", "code": "E"})
        )
        with pytest.raises(AgentError, match="nope") as e:
            self._agent().stats()
        assert e.value.code == "E"

    @respx.mock
    def test_post_error_envelope(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.post(f"{BASE}/trade/buy").mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "no funds"})
        )
        with pytest.raises(AgentError, match="no funds"):
            self._agent().buy("m", "yes", 100)

    @respx.mock
    def test_get_error_default_message(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.get(f"{BASE}/accounts/1/balance").mock(
            return_value=httpx.Response(200, json={"ok": False})
        )
        with pytest.raises(AgentError, match="Request failed"):
            self._agent().balance()

    @respx.mock
    def test_leaderboard_error_default_message(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.get(f"{BASE}/leaderboard").mock(
            return_value=httpx.Response(200, json={"ok": False})
        )
        with pytest.raises(AgentError, match="Unknown error"):
            self._agent().leaderboard()


class TestLifecycle:
    @respx.mock
    def test_close_owns_client(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        agent = Agent(BASE, api_key="lb_sk_x")
        agent.close()
        assert agent._http.is_closed

    @respx.mock
    def test_external_client_not_closed(self):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        external = httpx.Client(base_url=BASE)
        with Agent(BASE, api_key="lb_sk_x", http_client=external) as agent:
            assert agent.account_id == 1
        assert external.is_closed is False
        external.close()
