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
    _to_bool,
    fetch_market_info,
    fetch_prices,
    fetch_resolution,
    fetch_resolution_detail,
    list_markets,
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

    def test_string_booleans(self):
        """Gamma sends booleans as strings; "false" must parse as False."""
        info = _parse_market({
            **SAMPLE_API_RESPONSE, "active": "false", "closed": "true",
        })
        assert info.active is False
        assert info.closed is True
        info = _parse_market({
            **SAMPLE_API_RESPONSE, "active": "true", "closed": "false",
        })
        assert info.active is True
        assert info.closed is False

    def test_event_id_from_field(self):
        info = _parse_market({**SAMPLE_API_RESPONSE, "eventId": 12345})
        assert info.event_id == "12345"

    def test_event_id_from_events_list(self):
        info = _parse_market({
            **SAMPLE_API_RESPONSE,
            "events": [{"id": "ev-9", "slug": "big-event"}],
        })
        assert info.event_id == "ev-9"

    def test_event_id_absent(self):
        assert _parse_market(SAMPLE_API_RESPONSE).event_id is None


class TestToBool:
    def test_strings(self):
        assert _to_bool("true") is True
        assert _to_bool("false") is False
        assert _to_bool("True") is True
        assert _to_bool("1") is True
        assert _to_bool("yes") is True
        assert _to_bool("0") is False
        assert _to_bool("") is False

    def test_non_strings(self):
        assert _to_bool(True) is True
        assert _to_bool(False) is False
        assert _to_bool(None) is False
        assert _to_bool(1) is True


class TestListMarkets:
    @respx.mock
    def test_success(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[SAMPLE_API_RESPONSE])
        )
        markets = list_markets(limit=50)
        assert len(markets) == 1
        assert markets[0].slug == "will-bitcoin-hit-100k-2025"
        req = respx.calls.last.request
        assert req.url.params["order"] == "volumeNum"
        assert req.url.params["active"] == "true"
        assert req.url.params["closed"] == "false"
        assert req.url.params["ascending"] == "false"
        assert req.url.params["limit"] == "50"

    @respx.mock
    def test_dict_envelope(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json={"markets": [SAMPLE_API_RESPONSE]})
        )
        assert len(list_markets()) == 1

    @respx.mock
    def test_data_envelope(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json={"data": [SAMPLE_API_RESPONSE]})
        )
        assert len(list_markets()) == 1

    @respx.mock
    def test_non_list_returns_empty(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=42)
        )
        assert list_markets() == []

    @respx.mock
    def test_http_error(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(500, text="Server Error")
        )
        with pytest.raises(MarketInfoError, match="API error 500"):
            list_markets()

    @respx.mock
    def test_request_error(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            side_effect=httpx.ConnectError("refused")
        )
        with pytest.raises(MarketInfoError, match="Request failed"):
            list_markets()

    @respx.mock
    def test_with_custom_client(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[])
        )
        client = httpx.Client()
        try:
            assert list_markets(http_client=client) == []
        finally:
            client.close()

    @respx.mock
    def test_offset_param(self):
        """``offset`` pages past the 100-per-page cap (v1.2)."""
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[SAMPLE_API_RESPONSE])
        )
        assert len(list_markets(limit=100, offset=200)) == 1
        req = respx.calls.last.request
        assert req.url.params["offset"] == "200"


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

    @respx.mock
    def test_closed_market_found_via_retry(self):
        """Gamma defaults closed=false — a closed market needs the retry."""
        route = respx.get(f"{GAMMA_BASE}/markets").mock(
            side_effect=[
                httpx.Response(200, json=[]),
                httpx.Response(200, json=[RESOLVED_YES_RESPONSE]),
            ]
        )
        assert fetch_resolution("test") == 1.0
        assert route.call_count == 2
        assert route.calls[1].request.url.params["closed"] == "true"


class TestFetchResolutionDetail:
    @respx.mock
    def test_yes_mapped_by_outcome_label(self):
        """YES is read by label, not position (M7): outcomes ["No","Yes"]."""
        payload = {
            **SAMPLE_API_RESPONSE,
            "closed": True,
            "outcomes": '["No", "Yes"]',
            "outcomePrices": '["0.01", "0.99"]',
        }
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[payload])
        )
        res = fetch_resolution_detail("test")
        assert res.outcome == 1.0
        assert res.source == "price"
        assert res.closed is True

    @respx.mock
    def test_uma_resolved_non_yes_no_market(self):
        """Shape of a live UMA-settled payload (probed 2026-09-26): exact 0/1
        prices, ``umaResolutionStatus: "resolved"``, non-Yes/No labels."""
        payload = {
            **SAMPLE_API_RESPONSE,
            "closed": True,
            "outcomes": '["Over", "Under"]',
            "outcomePrices": '["0", "1"]',
            "umaResolutionStatus": "resolved",
        }
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[payload])
        )
        res = fetch_resolution_detail("test")
        assert res.outcome == 0.0
        assert res.source == "uma"
        assert res.closed is True

    @respx.mock
    def test_uma_pending_stays_pending(self):
        """Closed at an extreme price but the oracle has not settled: a
        closing price is not a settlement, so the market stays pending."""
        payload = {**RESOLVED_YES_RESPONSE, "umaResolutionStatus": "proposed"}
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[payload])
        )
        res = fetch_resolution_detail("test")
        assert res.outcome is None
        assert res.closed is False
        assert res.source is None

    @respx.mock
    def test_uma_resolved_fifty_fifty_is_unresolvable(self):
        payload = {**CLOSED_AMBIGUOUS_RESPONSE, "umaResolutionStatus": "resolved"}
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[payload])
        )
        res = fetch_resolution_detail("test")
        assert res.outcome is None
        assert res.closed is True

    @respx.mock
    def test_closed_unresolvable(self):
        """Closed with no signal: outcome None, closed True, source None."""
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[CLOSED_AMBIGUOUS_RESPONSE])
        )
        res = fetch_resolution_detail("test")
        assert res.outcome is None
        assert res.closed is True
        assert res.source is None

    @respx.mock
    def test_not_closed(self):
        respx.get(f"{GAMMA_BASE}/markets").mock(
            return_value=httpx.Response(200, json=[SAMPLE_API_RESPONSE])
        )
        res = fetch_resolution_detail("test")
        assert res.outcome is None
        assert res.closed is False
        assert res.source is None


@pytest.mark.live
def test_live_uma_settled_market_resolves():
    """Pins the resolution path to the wire: a market UMA-settled on
    2026-09-26 (outcomes "Cherevko Roman"/"Masko Yevhen", prices 0/1)."""
    try:
        res = fetch_resolution_detail("setkameua-cherevk-yevhen-2026-09-26")
    except MarketInfoError as exc:
        pytest.skip(f"Gamma unreachable: {exc}")
    assert res.closed is True
    assert res.source == "uma"
    assert res.outcome == 0.0
