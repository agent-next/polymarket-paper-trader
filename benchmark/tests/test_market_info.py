"""Tests for pm_benchmark.market_info."""
from __future__ import annotations

import httpx
import pytest
import respx

from pm_benchmark.market_info import (
    GAMMA_BASE,
    MarketInfo,
    MarketInfoError,
    _parse_list,
    _parse_market,
    fetch_market_info,
    fetch_prices,
    fetch_resolution,
)


SAMPLE_API_RESPONSE = {
    "slug": "will-bitcoin-hit-100k-2025",
    "question": "Will Bitcoin hit $100k in 2025?",
    "description": "Resolves Yes if BTC reaches $100,000.",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.65", "0.35"]',
    "volume": "1500000",
    "liquidity": "250000",
    "endDate": "2025-12-31T23:59:59Z",
    "active": True,
    "closed": False,
}


class TestParseList:
    def test_json_string(self):
        assert _parse_list('["Yes", "No"]') == ["Yes", "No"]

    def test_actual_list(self):
        assert _parse_list(["Yes", "No"]) == ["Yes", "No"]

    def test_numeric_list(self):
        assert _parse_list([0.65, 0.35]) == ["0.65", "0.35"]

    def test_invalid_json(self):
        assert _parse_list("not json") == []

    def test_json_non_list(self):
        assert _parse_list('{"a": 1}') == []


class TestParseMarket:
    def test_full(self):
        info = _parse_market(SAMPLE_API_RESPONSE)
        assert info.slug == "will-bitcoin-hit-100k-2025"
        assert info.question == "Will Bitcoin hit $100k in 2025?"
        assert info.outcomes == ["Yes", "No"]
        assert info.outcome_prices == [0.65, 0.35]
        assert info.volume == 1_500_000.0
        assert info.liquidity == 250_000.0
        assert info.active is True
        assert info.closed is False

    def test_missing_fields(self):
        info = _parse_market({})
        assert info.slug == ""
        assert info.outcomes == []
        assert info.outcome_prices == []
        assert info.volume == 0.0


class TestFetchMarketInfo:
    @respx.mock
    def test_success(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test-market"}).mock(
            return_value=httpx.Response(200, json=[SAMPLE_API_RESPONSE])
        )
        info = fetch_market_info("test-market")
        assert isinstance(info, MarketInfo)
        assert info.slug == "will-bitcoin-hit-100k-2025"

    @respx.mock
    def test_single_dict_response(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=SAMPLE_API_RESPONSE)
        )
        info = fetch_market_info("test")
        assert info.slug == "will-bitcoin-hit-100k-2025"

    @respx.mock
    def test_empty_list(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "missing"}).mock(
            return_value=httpx.Response(200, json=[])
        )
        with pytest.raises(MarketInfoError, match="not found"):
            fetch_market_info("missing")

    @respx.mock
    def test_http_error(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        with pytest.raises(MarketInfoError, match="API error 500"):
            fetch_market_info("test")

    @respx.mock
    def test_request_error(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(MarketInfoError, match="Request failed"):
            fetch_market_info("test")

    @respx.mock
    def test_with_custom_client(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=[SAMPLE_API_RESPONSE])
        )
        client = httpx.Client()
        try:
            info = fetch_market_info("test", http_client=client)
            assert info.slug == "will-bitcoin-hit-100k-2025"
        finally:
            client.close()


RESOLVED_YES_RESPONSE = {
    **SAMPLE_API_RESPONSE,
    "closed": True,
    "active": False,
    "outcomePrices": '["0.99", "0.01"]',
}

RESOLVED_NO_RESPONSE = {
    **SAMPLE_API_RESPONSE,
    "closed": True,
    "active": False,
    "outcomePrices": '["0.01", "0.99"]',
}

CLOSED_AMBIGUOUS_RESPONSE = {
    **SAMPLE_API_RESPONSE,
    "closed": True,
    "active": False,
    "outcomePrices": '["0.50", "0.50"]',
}


class TestFetchPrices:
    @respx.mock
    def test_success(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=[SAMPLE_API_RESPONSE])
        )
        prices = fetch_prices("test")
        assert prices == {"Yes": 0.65, "No": 0.35}


class TestFetchResolution:
    @respx.mock
    def test_yes_resolved(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=[RESOLVED_YES_RESPONSE])
        )
        assert fetch_resolution("test") == 1.0

    @respx.mock
    def test_no_resolved(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=[RESOLVED_NO_RESPONSE])
        )
        assert fetch_resolution("test") == 0.0

    @respx.mock
    def test_not_closed(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=[SAMPLE_API_RESPONSE])
        )
        assert fetch_resolution("test") is None

    @respx.mock
    def test_closed_ambiguous(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=[CLOSED_AMBIGUOUS_RESPONSE])
        )
        assert fetch_resolution("test") is None

    @respx.mock
    def test_closed_no_prices(self):
        closed_no_prices = {
            **SAMPLE_API_RESPONSE,
            "closed": True,
            "outcomePrices": "[]",
        }
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(200, json=[closed_no_prices])
        )
        assert fetch_resolution("test") is None

    @respx.mock
    def test_api_error_propagates(self):
        respx.get(f"{GAMMA_BASE}/markets", params={"slug": "test"}).mock(
            return_value=httpx.Response(500, text="Server Error")
        )
        with pytest.raises(MarketInfoError):
            fetch_resolution("test")
