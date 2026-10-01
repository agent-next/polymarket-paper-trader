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
        with pytest.raises(AgentError, match="no account") as e:
            Agent(BASE, name="bot")
        assert e.value.api_key == "lb_sk_x"

    @respx.mock
    def test_account_creation_retried_once(self):
        respx.post(f"{BASE}/auth/register").mock(
            return_value=_ok({"api_key": "lb_sk_x", "agent_name": "bot"})
        )
        route = respx.post(f"{BASE}/accounts").mock(
            side_effect=[httpx.ConnectError("down"), _ok({"id": 9})]
        )
        agent = Agent(BASE, name="bot")
        assert route.call_count == 2
        assert agent.account_id == 9
        assert agent.api_key == "lb_sk_x"


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


class TestServerErrorEnvelopes:
    @respx.mock
    def _err(self, response):
        respx.get(f"{BASE}/accounts").mock(return_value=_ok([{"name": "default", "id": 1}]))
        respx.post(f"{BASE}/trade/buy").mock(return_value=response)
        agent = Agent(BASE, api_key="lb_sk_x", http_client=httpx.Client(base_url=BASE))
        with pytest.raises(AgentError) as e:
            agent.buy("m", "yes", 1)
        return e.value

    def test_detail_dict(self):
        e = self._err(httpx.Response(400, json={"detail": {"error": "bad", "code": "X"}}))
        assert (str(e), e.code) == ("bad", "X")

    def test_detail_dict_missing_error(self):
        e = self._err(httpx.Response(400, json={"detail": {}}))
        assert (str(e), e.code) == ("Request failed", None)

    def test_detail_str(self):
        e = self._err(httpx.Response(403, json={"detail": "Not your account"}))
        assert (str(e), e.code) == ("Not your account", None)

    def test_detail_list(self):
        e = self._err(httpx.Response(422, json={"detail": [{"msg": "a"}, {"msg": "b"}, "c"]}))
        assert (str(e), e.code) == ("a; b; c", "VALIDATION_ERROR")

    def test_detail_empty_list(self):
        e = self._err(httpx.Response(422, json={"detail": []}))
        assert (str(e), e.code) == ("Request failed", "VALIDATION_ERROR")

    def test_non_json_body(self):
        e = self._err(httpx.Response(500, text="Internal Server Error"))
        assert str(e) == "HTTP 500: Internal Server Error"

    def test_empty_non_json_body(self):
        e = self._err(httpx.Response(502, text=""))
        assert str(e) == "HTTP 502: Request failed"

    def test_non_object_json(self):
        e = self._err(httpx.Response(500, json=["x"]))
        assert str(e) == "Request failed"


def test_real_server_error_codes(monkeypatch):
    """Drive the client against the real FastAPI app (no mocked envelope)."""
    pytest.importorskip("fastapi")
    server_app = pytest.importorskip("server.app")
    from fastapi.testclient import TestClient

    from server.adapters.polymarket import Market

    market = Market(
        condition_id="0xabc", slug="m", question="Q?", description="",
        outcomes=["Yes", "No"], outcome_prices=[0.65, 0.35],
        tokens=[{"token_id": "ty", "outcome": "Yes"}, {"token_id": "tn", "outcome": "No"}],
        active=True, closed=False, volume=1.0, liquidity=1.0,
        end_date="2026-12-31T23:59:59Z", fee_rate_bps=0, tick_size=0.01,
    )

    class _PM:
        def get_market(self, slug):
            return market

        def close(self):
            pass

    monkeypatch.setattr(server_app.app.state, "polymarket", _PM(), raising=False)
    monkeypatch.setattr(server_app.app.state, "scheduler", None, raising=False)
    with TestClient(server_app.app) as tc:
        agent = Agent("http://testserver", name="envelope-bot", http_client=tc)
        with pytest.raises(AgentError) as e:
            agent.buy("m", "maybe", 1)
        assert e.value.code == "INVALID_OUTCOME"
        assert "maybe" in str(e.value)
        with pytest.raises(AgentError) as e:
            agent._post("/trade/buy", {})
        assert e.value.code == "VALIDATION_ERROR"
        agent._account_id = 10**6
        with pytest.raises(AgentError, match="Account not found"):
            agent.balance()
