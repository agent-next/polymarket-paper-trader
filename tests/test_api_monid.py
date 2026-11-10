"""Opt-in Monid search tests using HTTPX's in-process transport."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from pm_trader.api import PolymarketClient
from pm_trader.db import Database
from pm_trader.models import ApiError


@pytest.fixture
def client(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> PolymarketClient:
    monkeypatch.setenv("MONID_API_KEY", "test-key")
    monkeypatch.delenv("POLYMARKET_API_KEY", raising=False)
    db = Database(tmp_data_dir)
    db.init_schema()
    c = PolymarketClient(db)
    yield c
    c.close()
    db.close()


def test_monid_key_only(client: PolymarketClient) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.monid.ai/v1/run"
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.headers["Content-Type"] == "application/json; charset=utf-8"
        assert "比特币".encode() in request.content
        assert json.loads(request.content) == {
            "provider": "surf", "endpoint": "/search/prediction-market",
            "input": {"queryParams": {
                "platform": "polymarket", "q": "比特币", "limit": 100,
            }, "body": {}, "pathParams": {}},
        }
        return httpx.Response(200, json={"output": {"data": [{
            "condition_id": "0xabc", "market_slug": "bitcoin",
            "question": "Bitcoin?", "outcome_label": "Yes",
            "latest_price": 0.6, "token_id": "yes-token", "status": "active",
        }, {"question": "missing id"}]}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client._http = http
        market, = client.search_markets("比特币", limit=101)
    assert (market.condition_id, market.slug, market.question) == ("0xabc", "bitcoin", "Bitcoin?")
    assert market.outcomes == ["Yes"]
    assert market.outcome_prices == pytest.approx([0.6])
    assert market.yes_token_id == "yes-token"
    assert market.active and not market.closed and not market.accepting_orders


@pytest.mark.parametrize("status", [400, 404, 429])
def test_provider_error(client: PolymarketClient, status: int) -> None:
    run = {"status": "COMPLETED", "providerResponse": {
        "httpStatus": status, "error": "vendor error",
    }}
    with patch.object(client._http, "post", return_value=httpx.Response(
        200, json=run, request=httpx.Request("POST", "https://api.monid.ai/v1/run"),
    )):
        with pytest.raises(ApiError, match=f"Gamma API error: {status} vendor error") as exc:
            client.search_markets("bitcoin")
    assert exc.value.status_code == status


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "TIMED_OUT", "STOPPED", "BLOCKED"])
def test_async(client: PolymarketClient, terminal: str) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(202, json={"runId": "run-123"})
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={
            "status": "RUNNING" if len(calls) == 2 else terminal,
            "output": {"data": []},
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client._http = http
        with patch("pm_trader.api.time.sleep"):
            if terminal == "COMPLETED":
                assert client.search_markets("bitcoin") == []
            else:
                with pytest.raises(ApiError, match=f"Monid run {terminal}"):
                    client.search_markets("bitcoin")
    assert calls == ["https://api.monid.ai/v1/run"] + [
        "https://api.monid.ai/v1/runs/run-123",
    ] * 2


def test_poll_deadline(client: PolymarketClient) -> None:
    response = httpx.Response(202, json={"runId": "run-123"}, request=httpx.Request(
        "POST", "https://api.monid.ai/v1/run",
    ))
    with patch.object(client._http, "post", return_value=response):
        with patch("pm_trader.api.time.monotonic", side_effect=[0, 61]):
            with pytest.raises(ApiError, match="Monid run TIMED_OUT"):
                client.search_markets("bitcoin")


@pytest.mark.parametrize("poll", [False, True])
@pytest.mark.parametrize("failure", ["http", "request"])
def test_transport_errors(client: PolymarketClient, poll: bool, failure: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if poll and request.method == "POST":
            return httpx.Response(202, json={"runId": "run-123"})
        if failure == "request":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(401, text="bad key")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client._http = http
        with patch("pm_trader.api.time.sleep"):
            with pytest.raises(ApiError, match="Monid API"):
                client.search_markets("bitcoin")


def test_unset_key(client: PolymarketClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONID_API_KEY")
    with patch.object(client, "_gamma_get", return_value={"events": []}) as gamma:
        assert client.search_markets("bitcoin", limit=7) == []
    gamma.assert_called_once_with("/public-search", params={"q": "bitcoin", "limit_per_type": 7})


def test_sparse_results(client: PolymarketClient) -> None:
    response = httpx.Response(200, json={"output": {"data": [
        {"condition_id": "0xclosed", "status": "finalized"},
    ]}}, request=httpx.Request("POST", "https://api.monid.ai/v1/run"))
    with patch.object(client._http, "post", return_value=response):
        market, = client.search_markets("bitcoin")
    assert market.closed and not market.active
    assert market.tokens == [] and market.slug == ""


def test_structured_provider_error(client: PolymarketClient) -> None:
    response = httpx.Response(200, json={"providerResponse": {
        "httpStatus": 400, "error": {"message": "bad input"},
    }}, request=httpx.Request("POST", "https://api.monid.ai/v1/run"))
    with patch.object(client._http, "post", return_value=response):
        with pytest.raises(ApiError, match='Gamma API error: 400 {"message": "bad input"}'):
            client.search_markets("bitcoin")
