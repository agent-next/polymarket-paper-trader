"""Tests for the Polymarket HTTP client."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from pm_trader.api import (
    CACHE_TTL_SECONDS,
    CLOB_BASE,
    DATA_API_BASE,
    GAMMA_BASE,
    PolymarketClient,
    _parse_clob_market,
    _parse_market,
    _parse_order_book,
    _parse_search_results,
)
from pm_trader.db import Database
from pm_trader.models import ApiError, MarketNotFoundError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_data_dir: Path) -> Database:
    database = Database(tmp_data_dir)
    database.init_schema()
    return database


@pytest.fixture
def client(db: Database) -> PolymarketClient:
    c = PolymarketClient(db)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Gamma API response fixtures
# ---------------------------------------------------------------------------

SAMPLE_GAMMA_MARKET = {
    "condition_id": "0xabc123",
    "slug": "will-bitcoin-hit-100k",
    "question": "Will Bitcoin hit $100k by end of 2026?",
    "description": "Resolves YES if BTC >= 100k.",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["0.65", "0.35"]',
    "tokens": json.dumps([
        {"token_id": "tok_yes", "outcome": "Yes"},
        {"token_id": "tok_no", "outcome": "No"},
    ]),
    "active": True,
    "closed": False,
    "acceptingOrders": True,
    "negRisk": False,
    "volume": "5000000",
    "liquidity": "250000",
    "end_date_iso": "2026-12-31T23:59:59Z",
    "fee_rate_bps": 0,
    "minimum_tick_size": "0.01",
}

SAMPLE_BOOK_RESPONSE = {
    "bids": [
        {"price": "0.64", "size": "150"},
        {"price": "0.63", "size": "200"},
    ],
    "asks": [
        {"price": "0.66", "size": "80"},
        {"price": "0.67", "size": "120"},
    ],
}


def _clob_market(condition_id: str = "0xabc123", **overrides) -> dict:
    """Build a current (abbreviated) CLOB /clob-markets response body."""
    body = {
        "c": condition_id,
        "t": [
            {"t": "tok_yes", "o": "Yes"},
            {"t": "tok_no", "o": "No"},
        ],
        "mts": 0.01,
        "mos": 5.0,
        "mbf": 0,
        "tbf": 0,
        "fd": {"r": 0.07, "e": 1, "to": True},
    }
    body.update(overrides)
    return body


def _legacy_clob_market(condition_id: str, **overrides) -> dict:
    """Build a pre-drift (long-key) CLOB /clob-markets response body."""
    body = {
        "condition_id": condition_id,
        "market_slug": "clob-market-slug",
        "question": "CLOB market?",
        "description": "",
        "active": True,
        "closed": False,
        "minimum_tick_size": "0.01",
        "tokens": [
            {"token_id": "tok_c_yes", "outcome": "Yes"},
            {"token_id": "tok_c_no", "outcome": "No"},
        ],
    }
    body.update(overrides)
    return body


def _condition_id_lookups(httpx_mock) -> list[dict]:
    """Query params of every Gamma /markets lookup by condition_ids, in order."""
    return [
        dict(r.url.params)
        for r in httpx_mock.get_requests()
        if r.url.path == "/markets" and r.url.params.get("condition_ids")
    ]


def _search_response(markets: list[dict] | None = None, **overrides) -> dict:
    """Build a Gamma /public-search response envelope."""
    body = {
        "events": [{"title": "Bitcoin", "markets": markets or []}],
        "tags": [],
        "profiles": [],
        "pagination": {"hasMore": False, "totalResults": len(markets or [])},
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# _parse_market tests
# ---------------------------------------------------------------------------

class TestParseMarket:
    def test_basic_parsing(self):
        market = _parse_market(SAMPLE_GAMMA_MARKET)
        assert market.condition_id == "0xabc123"
        assert market.slug == "will-bitcoin-hit-100k"
        assert market.outcomes == ["Yes", "No"]
        assert market.outcome_prices == [0.65, 0.35]
        assert market.active is True
        assert market.closed is False
        assert market.accepting_orders is True
        assert market.neg_risk is False
        assert market.volume == 5_000_000.0
        assert market.liquidity == 250_000.0
        assert market.fee_rate_bps == 0
        assert market.tick_size == 0.01

    def test_tokens_parsed_from_json_string(self):
        market = _parse_market(SAMPLE_GAMMA_MARKET)
        assert len(market.tokens) == 2
        assert market.tokens[0]["token_id"] == "tok_yes"
        assert market.tokens[0]["outcome"] == "Yes"
        assert market.tokens[1]["token_id"] == "tok_no"

    def test_tokens_parsed_from_list(self):
        data = {**SAMPLE_GAMMA_MARKET, "tokens": [
            {"token_id": "t1", "outcome": "Yes"},
            {"token_id": "t2", "outcome": "No"},
        ]}
        market = _parse_market(data)
        assert market.tokens[0]["token_id"] == "t1"

    def test_outcome_prices_from_list(self):
        data = {**SAMPLE_GAMMA_MARKET, "outcomePrices": [0.7, 0.3]}
        market = _parse_market(data)
        assert market.outcome_prices == [0.7, 0.3]

    def test_missing_fields_use_defaults(self):
        data = {"condition_id": "0x1"}
        market = _parse_market(data)
        assert market.slug == ""
        assert market.volume == 0.0
        assert market.outcome_prices == [0.0, 0.0]
        assert market.tick_size == 0.0  # absent -> 0.0; placement consults CLOB /tick-size
        assert market.accepting_orders is True
        assert market.neg_risk is False

    def test_null_volume_liquidity(self):
        data = {**SAMPLE_GAMMA_MARKET, "volume": None, "liquidity": None}
        market = _parse_market(data)
        assert market.volume == 0.0
        assert market.liquidity == 0.0

    def test_string_bool_flags_are_parsed(self):
        """Gamma sends booleans as strings — 'false' must not parse as True."""
        data = {
            **SAMPLE_GAMMA_MARKET,
            "active": "TRUE",
            "closed": "false",
            "acceptingOrders": "true",
            "negRisk": "FALSE",
        }
        market = _parse_market(data)
        assert market.active is True
        assert market.closed is False
        assert market.accepting_orders is True
        assert market.neg_risk is False

    def test_fee_schedule_present(self):
        data = {
            **SAMPLE_GAMMA_MARKET,
            "feeSchedule": {
                "rate": 0.07,
                "exponent": 1,
                "takerOnly": True,
                "rebateRate": 0.25,
            },
            "feesEnabled": True,
        }
        market = _parse_market(data)
        assert market.fee_schedule == {
            "rate": 0.07,
            "exponent": 1,
            "takerOnly": True,
            "rebateRate": 0.25,
        }
        # rate is a coefficient, not bps — fee_rate_bps stays untouched
        assert market.fee_rate_bps == 0

    def test_fee_schedule_absent_is_none(self):
        market = _parse_market(SAMPLE_GAMMA_MARKET)
        assert market.fee_schedule is None

    def test_fee_schedule_non_dict_is_none(self):
        data = {**SAMPLE_GAMMA_MARKET, "feeSchedule": "0.07"}
        assert _parse_market(data).fee_schedule is None

    def test_fee_schedule_missing_subfields_default(self):
        data = {**SAMPLE_GAMMA_MARKET, "feeSchedule": {"rate": None}}
        assert _parse_market(data).fee_schedule == {
            "rate": 0.0,
            "exponent": 0,
            "takerOnly": False,
            "rebateRate": 0.0,
        }

    def test_maker_taker_base_fees_parsed(self):
        """Gamma carries makerBaseFee/takerBaseFee in bps."""
        data = {**SAMPLE_GAMMA_MARKET, "makerBaseFee": 15, "takerBaseFee": 25}
        market = _parse_market(data)
        assert market.maker_base_fee_bps == 15
        assert market.taker_base_fee_bps == 25

    def test_maker_taker_base_fees_default_to_zero(self):
        market = _parse_market(SAMPLE_GAMMA_MARKET)
        assert market.maker_base_fee_bps == 0
        assert market.taker_base_fee_bps == 0

    def test_maker_taker_base_fees_snake_case_and_null(self):
        """Snake_case (cached/test) keys work; nulls fall back to 0."""
        data = {
            **SAMPLE_GAMMA_MARKET,
            "maker_base_fee_bps": 5,
            "takerBaseFee": None,
        }
        market = _parse_market(data)
        assert market.maker_base_fee_bps == 5
        assert market.taker_base_fee_bps == 0


# ---------------------------------------------------------------------------
# _parse_clob_market tests
# ---------------------------------------------------------------------------


class TestParseClobMarket:
    def test_abbreviated_keys(self):
        """Current CLOB payload: c / t[{t,o}] / mts / mos / mbf / tbf / fd."""
        market = _parse_clob_market(
            _clob_market("0xcond", mts=0.0025, mos=15.0, mbf=10, tbf=20)
        )
        assert market.condition_id == "0xcond"
        assert market.tokens == [
            {"token_id": "tok_yes", "outcome": "Yes"},
            {"token_id": "tok_no", "outcome": "No"},
        ]
        assert market.outcomes == ["Yes", "No"]
        assert market.tick_size == 0.0025
        assert market.min_order_size == 15.0
        assert market.maker_base_fee_bps == 10
        assert market.taker_base_fee_bps == 20
        # Abbreviated payloads carry no slug/question/description
        assert market.slug == ""
        assert market.question == ""

    def test_abbreviated_missing_optional_fields_default(self):
        market = _parse_clob_market({"c": "0x1", "t": [{"t": "tok", "o": "Yes"}]})
        assert market.tick_size == 0.0  # absent -> 0.0; placement consults CLOB /tick-size
        assert market.min_order_size == 0.0
        assert market.maker_base_fee_bps == 0
        assert market.taker_base_fee_bps == 0
        # No `fd` block -> no fee schedule, rather than a zeroed one
        assert market.fee_schedule is None

    def test_fee_schedule_from_fd_block(self):
        """`fd` (r/e/to) maps onto the Gamma feeSchedule shape."""
        market = _parse_clob_market(
            _clob_market("0xfd1", fd={"r": 0.07, "e": 1, "to": True})
        )
        assert market.fee_schedule == {
            "rate": 0.07,
            "exponent": 1,
            "takerOnly": True,
            "rebateRate": 0.0,
        }

    def test_fee_schedule_from_fd_block_with_rebate(self):
        """A rebate rate inside `fd` is surfaced as rebateRate."""
        market = _parse_clob_market(
            _clob_market("0xfd2", fd={"r": 0.04, "e": 2, "to": False, "rr": 0.25})
        )
        assert market.fee_schedule == {
            "rate": 0.04,
            "exponent": 2,
            "takerOnly": False,
            "rebateRate": 0.25,
        }

    def test_fee_schedule_fd_missing_subfields_default(self):
        market = _parse_clob_market(_clob_market("0xfd3", fd={"r": None}))
        assert market.fee_schedule == {
            "rate": 0.0,
            "exponent": 0,
            "takerOnly": False,
            "rebateRate": 0.0,
        }

    def test_fee_schedule_fd_non_dict_is_none(self):
        market = _parse_clob_market(_clob_market("0xfd4", fd="0.07"))
        assert market.fee_schedule is None

    def test_legacy_keys_still_parse(self):
        """Mixed generations: the pre-drift long-key shape keeps working."""
        market = _parse_clob_market(
            _legacy_clob_market("0xold", minimum_tick_size="0.005")
        )
        assert market.condition_id == "0xold"
        assert market.slug == "clob-market-slug"
        assert market.question == "CLOB market?"
        assert market.tokens[0]["token_id"] == "tok_c_yes"
        assert market.tick_size == 0.005
        assert market.min_order_size == 0.0
        assert market.fee_schedule is None

    def test_tokens_as_json_string(self):
        market = _parse_clob_market({
            "c": "0x1",
            "t": json.dumps([{"t": "tok_a", "o": "Yes"}]),
        })
        assert market.tokens == [{"token_id": "tok_a", "outcome": "Yes"}]

    def test_no_tokens_falls_back_to_yes_no(self):
        market = _parse_clob_market({"c": "0x1"})
        assert market.outcomes == ["Yes", "No"]
        assert market.active is True
        assert market.closed is False

    def test_string_bools_coerced(self):
        market = _parse_clob_market(
            _legacy_clob_market("0x1", active="True", closed="False")
        )
        assert market.active is True
        assert market.closed is False


# ---------------------------------------------------------------------------
# _parse_search_results tests
# ---------------------------------------------------------------------------


class TestParseSearchResults:
    def test_flattens_nested_event_markets(self):
        data = _search_response([SAMPLE_GAMMA_MARKET])
        results = _parse_search_results(data)
        assert len(results) == 1
        assert results[0].slug == "will-bitcoin-hit-100k"

    def test_multiple_events_flattened(self):
        second = {**SAMPLE_GAMMA_MARKET, "slug": "eth-etf", "condition_id": "0xeth"}
        data = {
            "events": [
                {"markets": [SAMPLE_GAMMA_MARKET]},
                {"markets": [second]},
            ]
        }
        results = _parse_search_results(data)
        assert [m.slug for m in results] == ["will-bitcoin-hit-100k", "eth-etf"]

    def test_event_without_markets(self):
        data = {"events": [{"title": "no markets"}, {"markets": None}]}
        assert _parse_search_results(data) == []

    def test_markets_without_condition_id_filtered(self):
        data = _search_response([{"slug": "no-condition-id"}])
        assert _parse_search_results(data) == []

    def test_non_dict_response(self):
        assert _parse_search_results([SAMPLE_GAMMA_MARKET]) == []
        assert _parse_search_results(None) == []

    def test_missing_or_non_list_events(self):
        assert _parse_search_results({}) == []
        assert _parse_search_results({"events": None}) == []
        assert _parse_search_results({"events": "nope"}) == []


# ---------------------------------------------------------------------------
# _parse_order_book tests
# ---------------------------------------------------------------------------

class TestParseOrderBook:
    def test_basic_parsing(self):
        book = _parse_order_book(SAMPLE_BOOK_RESPONSE)
        assert len(book.bids) == 2
        assert len(book.asks) == 2
        assert book.bids[0].price == 0.64
        assert book.bids[0].size == 150.0
        assert book.asks[0].price == 0.66
        assert book.asks[0].size == 80.0

    def test_empty_book(self):
        book = _parse_order_book({})
        assert book.bids == []
        assert book.asks == []

    def test_one_sided_book(self):
        book = _parse_order_book({"bids": [{"price": "0.5", "size": "100"}]})
        assert len(book.bids) == 1
        assert book.asks == []


# ---------------------------------------------------------------------------
# PolymarketClient.get_market tests (with httpx mock)
# ---------------------------------------------------------------------------

class TestGetMarket:
    def test_get_market_by_slug(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "will-bitcoin-hit-100k"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        market = client.get_market("will-bitcoin-hit-100k")
        assert market.condition_id == "0xabc123"
        assert market.slug == "will-bitcoin-hit-100k"
        # The open lookup hit, so no closed=true retry was needed
        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert dict(requests[0].url.params) == {"slug": "will-bitcoin-hit-100k"}

    def test_get_market_by_condition_id(self, client: PolymarketClient, httpx_mock):
        # First request (slug lookup) returns empty
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xabc123"}),
            json=[],
        )
        # Gamma defaults closed=false, so a closed=true retry follows
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xabc123", "closed": "true"},
            ),
            json=[],
        )
        # Then the CLOB abbreviated endpoint for the condition id
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xabc123"),
            json=_clob_market("0xabc123"),
        )
        # Abbreviated CLOB payload has no slug -> enrichment by condition_ids
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"condition_ids": "0xabc123"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        market = client.get_market("0xabc123")
        assert market.condition_id == "0xabc123"

    def test_get_market_closed_market_found_on_retry(
        self, client: PolymarketClient, httpx_mock
    ):
        """A closed market is missed by the default slug lookup but found
        by the closed=true retry."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "resolved-market"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "resolved-market", "closed": "true"},
            ),
            json=[{**SAMPLE_GAMMA_MARKET, "closed": True, "active": False}],
        )
        market = client.get_market("resolved-market")
        assert market.slug == "will-bitcoin-hit-100k"
        assert market.closed is True

    def test_market_not_found(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "nonexistent"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "nonexistent", "closed": "true"},
            ),
            json=[],
        )
        # "nonexistent" doesn't start with "0x" so no CLOB lookup
        with pytest.raises(MarketNotFoundError):
            client.get_market("nonexistent")

    def test_market_not_found_by_condition_id(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xdead"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xdead", "closed": "true"},
            ),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xdead"),
            status_code=404,
            text="Not Found",
        )
        with pytest.raises(MarketNotFoundError):
            client.get_market("0xdead")

    def test_clob_body_without_condition_id_not_found(
        self, client: PolymarketClient, httpx_mock
    ):
        """A 200 CLOB body with no condition id is not a usable market."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xnocond"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xnocond", "closed": "true"},
            ),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xnocond"),
            json={"error": "no condition id"},
        )
        with pytest.raises(MarketNotFoundError):
            client.get_market("0xnocond")

    def test_market_cached_on_second_call(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "btc"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        m1 = client.get_market("btc")
        m2 = client.get_market("btc")  # Should use cache, no second HTTP call
        assert m1.condition_id == m2.condition_id
        assert len(httpx_mock.get_requests()) == 1

    def test_cached_clob_payload_replays_abbreviated_keys(
        self, client: PolymarketClient, httpx_mock
    ):
        """A bare abbreviated CLOB payload cached by the fallback path must
        still parse on a cache hit (no HTTP call)."""
        client._set_cached("market:0xcached", _clob_market("0xcached"))
        market = client.get_market("0xcached")
        assert market.condition_id == "0xcached"
        assert market.tokens[0] == {"token_id": "tok_yes", "outcome": "Yes"}
        assert httpx_mock.get_requests() == []

    def test_api_http_error(self, client: PolymarketClient, httpx_mock):
        # Gamma 5xx propagates — the closed=true retry never runs
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "err"}),
            status_code=500,
            text="Internal Server Error",
        )
        with pytest.raises(ApiError) as exc_info:
            client.get_market("err")
        assert exc_info.value.status_code == 500
        assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# PolymarketClient.list_markets tests
# ---------------------------------------------------------------------------

class TestListMarkets:
    def test_list_markets(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        markets = client.list_markets(limit=5)
        assert len(markets) == 1
        assert markets[0].slug == "will-bitcoin-hit-100k"
        req = httpx_mock.get_requests()[0]
        assert req.url.path == "/markets/keyset"
        assert dict(req.url.params) == {
            "active": "true",
            "closed": "false",
            "order": "volumeNum",
            "ascending": "false",
            "limit": "5",
        }

    def test_list_markets_empty(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(json={"markets": []})
        markets = client.list_markets()
        assert markets == []


class TestListMarketsKeyset:
    def test_paginates_with_after_cursor_and_dedups(
        self, client: PolymarketClient, httpx_mock
    ):
        """Page 2 carries after_cursor; a duplicated condition_id is merged."""
        m1 = {**SAMPLE_GAMMA_MARKET, "condition_id": "0xm1", "slug": "m1"}
        m2 = {**SAMPLE_GAMMA_MARKET, "condition_id": "0xm2", "slug": "m2"}
        m3 = {**SAMPLE_GAMMA_MARKET, "condition_id": "0xm3", "slug": "m3"}
        httpx_mock.add_response(
            json={"markets": [m1, m2], "next_cursor": "c2"}
        )
        httpx_mock.add_response(json={"markets": [m2, m3], "next_cursor": "c3"})
        markets = client.list_markets(limit=3)
        assert [m.condition_id for m in markets] == ["0xm1", "0xm2", "0xm3"]
        requests = httpx_mock.get_requests()
        assert len(requests) == 2
        assert "after_cursor" not in requests[0].url.params
        assert requests[1].url.params["after_cursor"] == "c2"
        # Only 1 market was still needed, so page 2 asks for limit=1
        assert requests[1].url.params["limit"] == "1"

    def test_missing_next_cursor_stops_after_page_one(
        self, client: PolymarketClient, httpx_mock
    ):
        """No next_cursor on the final page ends paging."""
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        markets = client.list_markets(limit=20)
        assert len(markets) == 1
        assert len(httpx_mock.get_requests()) == 1

    def test_page_limit_capped_at_100(
        self, client: PolymarketClient, httpx_mock
    ):
        """Per-request limit never exceeds 100; the remainder is requested."""
        page1 = [
            {**SAMPLE_GAMMA_MARKET, "condition_id": f"0x{i:03x}", "slug": f"m{i}"}
            for i in range(100)
        ]
        httpx_mock.add_response(json={"markets": page1, "next_cursor": "c2"})
        httpx_mock.add_response(json={"markets": []})
        markets = client.list_markets(limit=150)
        assert len(markets) == 100
        requests = httpx_mock.get_requests()
        assert len(requests) == 2
        assert requests[0].url.params["limit"] == "100"
        assert requests[1].url.params["limit"] == "50"
        assert requests[1].url.params["after_cursor"] == "c2"

    @pytest.mark.parametrize("bad_cursor", [123, {}, "", None])
    def test_malformed_next_cursor_stops(
        self, client: PolymarketClient, httpx_mock, bad_cursor
    ):
        """Non-string or empty next_cursor values are treated as end-of-list."""
        httpx_mock.add_response(
            json={"markets": [SAMPLE_GAMMA_MARKET], "next_cursor": bad_cursor}
        )
        markets = client.list_markets(limit=20)
        assert len(markets) == 1
        assert len(httpx_mock.get_requests()) == 1

    def test_repeated_cursor_not_followed(
        self, client: PolymarketClient, httpx_mock
    ):
        """An already-seen next_cursor stops paging (upstream replay bug)."""
        m2 = {**SAMPLE_GAMMA_MARKET, "condition_id": "0xm2", "slug": "m2"}
        httpx_mock.add_response(
            json={"markets": [SAMPLE_GAMMA_MARKET], "next_cursor": "c2"}
        )
        httpx_mock.add_response(json={"markets": [m2], "next_cursor": "c2"})
        markets = client.list_markets(limit=20)
        assert [m.condition_id for m in markets] == ["0xabc123", "0xm2"]
        assert len(httpx_mock.get_requests()) == 2

    def test_bare_list_response_still_parses(
        self, client: PolymarketClient, httpx_mock
    ):
        """The legacy bare-list shape is tolerated (no cursor to follow)."""
        httpx_mock.add_response(json=[SAMPLE_GAMMA_MARKET])
        markets = client.list_markets()
        assert len(markets) == 1
        assert len(httpx_mock.get_requests()) == 1

    def test_data_key_fallback(self, client: PolymarketClient, httpx_mock):
        """Envelopes carrying items under `data` (no `markets`) still parse."""
        httpx_mock.add_response(json={"data": [SAMPLE_GAMMA_MARKET]})
        markets = client.list_markets()
        assert len(markets) == 1

    def test_limit_zero_makes_no_request(
        self, client: PolymarketClient, httpx_mock
    ):
        assert client.list_markets(limit=0) == []
        assert httpx_mock.get_requests() == []

    def test_unknown_sort_by_sends_no_order(
        self, client: PolymarketClient, httpx_mock
    ):
        httpx_mock.add_response(json={"markets": []})
        client.list_markets(sort_by="date")
        assert "order" not in httpx_mock.get_requests()[0].url.params


# ---------------------------------------------------------------------------
# PolymarketClient.search_markets tests
# ---------------------------------------------------------------------------

class TestSearchMarkets:
    def test_search(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/public-search",
                params={"q": "bitcoin", "limit_per_type": 10},
            ),
            json=_search_response([SAMPLE_GAMMA_MARKET]),
        )
        results = client.search_markets("bitcoin")
        assert len(results) == 1
        assert "bitcoin" in results[0].slug
        req = httpx_mock.get_requests()[0]
        assert req.url.params["q"] == "bitcoin"
        assert req.url.params["limit_per_type"] == "10"

    def test_search_passes_limit_and_encodes_query(
        self, client: PolymarketClient, httpx_mock
    ):
        httpx_mock.add_response(json=_search_response([SAMPLE_GAMMA_MARKET]))
        results = client.search_markets("café 政治", limit=3)
        assert len(results) == 1
        req = httpx_mock.get_requests()[0]
        assert req.url.params["q"] == "café 政治"
        assert req.url.params["limit_per_type"] == "3"

    def test_search_flattens_multiple_events(
        self, client: PolymarketClient, httpx_mock
    ):
        second = {**SAMPLE_GAMMA_MARKET, "slug": "eth-etf", "condition_id": "0xeth"}
        httpx_mock.add_response(
            json={
                "events": [
                    {"markets": [SAMPLE_GAMMA_MARKET]},
                    {"markets": [second]},
                ],
            }
        )
        results = client.search_markets("crypto")
        assert [m.slug for m in results] == ["will-bitcoin-hit-100k", "eth-etf"]


# ---------------------------------------------------------------------------
# CLOB API tests
# ---------------------------------------------------------------------------

class TestClobEndpoints:
    def test_get_order_book(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "tok_yes"}),
            json=SAMPLE_BOOK_RESPONSE,
        )
        book = client.get_order_book("tok_yes")
        assert len(book.bids) == 2
        assert len(book.asks) == 2

    def test_get_midpoint(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/midpoint", params={"token_id": "tok_yes"}),
            json={"mid": "0.65"},
        )
        mid = client.get_midpoint("tok_yes")
        assert mid == 0.65

    def test_get_fee_rate(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"base_fee": 200},
        )
        fee = client.get_fee_rate("tok_yes")
        assert fee == 200

    def test_get_fee_rate_legacy_key(self, client: PolymarketClient, httpx_mock):
        """Pre-drift bodies used fee_rate_bps — still accepted."""
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"fee_rate_bps": 250},
        )
        assert client.get_fee_rate("tok_yes") == 250

    def test_fee_rate_cached(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"base_fee": 175},
        )
        f1 = client.get_fee_rate("tok_yes")
        f2 = client.get_fee_rate("tok_yes")
        assert f1 == f2 == 175
        assert len(httpx_mock.get_requests()) == 1

    def test_fee_rate_cache_replays_legacy_row(
        self, client: PolymarketClient, httpx_mock
    ):
        """A cache row written before the drift must still parse (no HTTP)."""
        client._set_cached("fee_rate:tok_old", {"fee_rate_bps": 100})
        assert client.get_fee_rate("tok_old") == 100
        assert httpx_mock.get_requests() == []

    def test_get_tick_size(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/tick-size", params={"token_id": "tok_yes"}),
            json={"minimum_tick_size": 0.001},
        )
        tick = client.get_tick_size("tok_yes")
        assert tick == 0.001

    def test_clob_api_error(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "bad"}),
            status_code=404,
            text="Not Found",
        )
        with pytest.raises(ApiError) as exc_info:
            client.get_order_book("bad")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# get_price_history tests (Data API v2)
# ---------------------------------------------------------------------------

def _history_page(rows, next_cursor=None):
    """Build a /v2/prices-history response envelope."""
    page = {"data": rows, "pagination": {"limit": len(rows), "offset": 0,
                                         "has_more": next_cursor is not None}}
    if next_cursor is not None:
        page["pagination"]["next_cursor"] = next_cursor
    return page


class TestGetPriceHistory:
    PH_URL = DATA_API_BASE + "/v2/prices-history"

    def test_single_page(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "interval": "1d", "limit": 5000}),
            json=_history_page([
                {"timestamp": 1790000000, "price": "0.55", "resolution_seconds": 60},
                {"timestamp": 1790000060, "price": 0.56, "resolution_seconds": 60},
            ]),
        )
        points = client.get_price_history("tok_yes", interval="1d")
        assert points == [
            {"timestamp": 1790000000, "price": 0.55, "resolution_seconds": 60},
            {"timestamp": 1790000060, "price": 0.56, "resolution_seconds": 60},
        ]
        # String prices are coerced to float
        assert isinstance(points[0]["price"], float)
        assert len(httpx_mock.get_requests()) == 1

    def test_follows_cursor_across_pages(self, client: PolymarketClient,
                                         httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 5000}),
            json=_history_page(
                [{"timestamp": 1, "price": 0.4, "resolution_seconds": 60}],
                next_cursor="cur1",
            ),
        )
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 4999, "cursor": "cur1"}),
            json=_history_page(
                [{"timestamp": 2, "price": 0.6, "resolution_seconds": 60}],
            ),
        )
        points = client.get_price_history("tok_yes")
        assert [p["timestamp"] for p in points] == [1, 2]
        assert len(httpx_mock.get_requests()) == 2

    def test_data_null_is_empty(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 5000}),
            json={"data": None, "pagination": {"has_more": False}},
        )
        assert client.get_price_history("tok_yes") == []

    def test_missing_pagination_stops(self, client: PolymarketClient,
                                      httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 5000}),
            json={"data": [{"timestamp": 1, "price": 0.5,
                            "resolution_seconds": 60}]},
        )
        points = client.get_price_history("tok_yes")
        assert len(points) == 1
        assert len(httpx_mock.get_requests()) == 1

    def test_limit_stops_paging(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 2}),
            json=_history_page(
                [{"timestamp": 1, "price": 0.5, "resolution_seconds": 60},
                 {"timestamp": 2, "price": 0.5, "resolution_seconds": 60}],
                next_cursor="cur1",
            ),
        )
        points = client.get_price_history("tok_yes", limit=2)
        assert len(points) == 2
        # Limit reached: next_cursor is not followed
        assert len(httpx_mock.get_requests()) == 1

    def test_limit_over_large_page_truncates(self, client: PolymarketClient,
                                             httpx_mock):
        # Defensive: a page that returns MORE rows than asked is truncated
        rows = [{"timestamp": i, "price": 0.5, "resolution_seconds": 60}
                for i in range(5)]
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 3}),
            json=_history_page(rows),
        )
        assert len(client.get_price_history("tok_yes", limit=3)) == 3

    def test_non_positive_limit_no_request(self, client: PolymarketClient):
        assert client.get_price_history("tok_yes", limit=0) == []

    def test_replayed_cursor_not_refollowed(self, client: PolymarketClient,
                                            httpx_mock):
        # Page 2 replays page 1's cursor forever — the loop must stop.
        replay = _history_page(
            [{"timestamp": 1, "price": 0.5, "resolution_seconds": 60}],
            next_cursor="same",
        )
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 5000}),
            json=replay,
        )
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "limit": 4999, "cursor": "same"}),
            json=replay,
        )
        points = client.get_price_history("tok_yes")
        assert len(points) == 2
        assert len(httpx_mock.get_requests()) == 2

    def test_window_params_passed_through(self, client: PolymarketClient,
                                          httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={
                "token_id": "tok_yes", "start": 1000, "end": 2000,
                "bucket_seconds": 300, "limit": 5000,
            }),
            json=_history_page([]),
        )
        assert client.get_price_history("tok_yes", start=1000, end=2000,
                                        bucket_seconds=300) == []

    def test_as_of_point_in_time(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "tok_yes",
                                               "as_of": 1790000000,
                                               "limit": 5000}),
            json=_history_page(
                [{"timestamp": 1790000000, "price": 0.61,
                  "resolution_seconds": 0}],
            ),
        )
        points = client.get_price_history("tok_yes", as_of=1790000000)
        assert points == [{"timestamp": 1790000000, "price": 0.61,
                           "resolution_seconds": 0}]

    def test_data_api_error(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(self.PH_URL, params={"token_id": "bad",
                                               "limit": 5000}),
            status_code=400,
            json={"error": "interval must be one of max, all, 1m, 1w, 1d, 6h, 1h",
                  "code": "invalid_request"},
        )
        with pytest.raises(ApiError) as exc_info:
            client.get_price_history("bad")
        assert exc_info.value.status_code == 400
        assert "Data API error" in str(exc_info.value)


    def test_data_api_request_error(self, client: PolymarketClient):
        with patch.object(
            client._http, "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(ApiError, match="Data API request failed"):
                client.get_price_history("tok_yes")


class TestSplitPriceHistoryPage:
    def test_non_dict_data(self):
        from pm_trader.api import _split_price_history_page
        assert _split_price_history_page([1, 2]) == ([], None)

    def test_data_non_list(self):
        from pm_trader.api import _split_price_history_page
        assert _split_price_history_page({"data": "oops"}) == ([], None)

    def test_pagination_non_dict(self):
        from pm_trader.api import _split_price_history_page
        rows, cursor = _split_price_history_page(
            {"data": [], "pagination": "oops"}
        )
        assert rows == []
        assert cursor is None

    def test_next_cursor_non_string(self):
        from pm_trader.api import _split_price_history_page
        _, cursor = _split_price_history_page(
            {"data": [], "pagination": {"next_cursor": 123}}
        )
        assert cursor is None


# ---------------------------------------------------------------------------
# get_trade_context tests
# ---------------------------------------------------------------------------

class TestGetTradeContext:
    def test_returns_market_book_fee(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "btc"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "tok_yes"}),
            json=SAMPLE_BOOK_RESPONSE,
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_yes"}),
            json={"base_fee": 0},
        )
        market, book, fee = client.get_trade_context("btc", "yes")
        assert market.condition_id == "0xabc123"
        assert len(book.bids) == 2
        assert fee == 0

    def test_no_outcome(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "btc"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/book", params={"token_id": "tok_no"}),
            json=SAMPLE_BOOK_RESPONSE,
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/fee-rate", params={"token_id": "tok_no"}),
            json={"base_fee": 175},
        )
        market, book, fee = client.get_trade_context("btc", "no")
        assert fee == 175


# ===========================================================================
# Coverage tests for the 14 uncovered lines
# ===========================================================================


# ---------------------------------------------------------------------------
# Line 61: _get_cached returns None when cache entry is stale (TTL expired)
# ---------------------------------------------------------------------------

class TestCacheTtlExpiry:
    def test_stale_cache_returns_none(self, client: PolymarketClient):
        """When the cached entry is older than CACHE_TTL_SECONDS, _get_cached
        must return None (line 61)."""
        # Insert a cache entry with a fetched_at timestamp well in the past.
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS + 60)
        client.db.conn.execute(
            "INSERT OR REPLACE INTO market_cache (cache_key, data, fetched_at) "
            "VALUES (?, ?, ?)",
            ("stale_key", json.dumps({"x": 1}), stale_time.isoformat()),
        )
        client.db.conn.commit()

        result = client._get_cached("stale_key")
        assert result is None

    def test_fresh_cache_returns_data(self, client: PolymarketClient):
        """Sanity: a fresh cache entry should be returned normally."""
        client._set_cached("fresh_key", {"y": 2})
        result = client._get_cached("fresh_key")
        assert result == {"y": 2}


# ---------------------------------------------------------------------------
# Lines 83-84: httpx.RequestError in _gamma_get
# ---------------------------------------------------------------------------

class TestGammaRequestError:
    def test_request_error_raises_api_error(self, client: PolymarketClient):
        """A network-level error (DNS failure, timeout, connection refused)
        should be caught and re-raised as ApiError (lines 83-84)."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(ApiError, match="Gamma API request failed"):
                client._gamma_get("/markets")

    def test_timeout_error_raises_api_error(self, client: PolymarketClient):
        """httpx.TimeoutException (subclass of RequestError) should also
        be caught and re-raised as ApiError."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ReadTimeout("Read timed out"),
        ):
            with pytest.raises(ApiError, match="Gamma API request failed"):
                client._gamma_get("/markets")


# ---------------------------------------------------------------------------
# Lines 98-99: httpx.RequestError in _clob_get
# ---------------------------------------------------------------------------

class TestClobRequestError:
    def test_request_error_raises_api_error(self, client: PolymarketClient):
        """A network-level error in _clob_get should be caught and
        re-raised as ApiError (lines 98-99)."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(ApiError, match="CLOB API request failed"):
                client._clob_get("/book")

    def test_timeout_error_raises_api_error(self, client: PolymarketClient):
        """httpx.TimeoutException (subclass of RequestError) should also
        be caught and re-raised as ApiError."""
        with patch.object(
            client._http, "get",
            side_effect=httpx.ReadTimeout("Read timed out"),
        ):
            with pytest.raises(ApiError, match="CLOB API request failed"):
                client._clob_get("/midpoint")


# ---------------------------------------------------------------------------
# Lines 122-124: get_market — Gamma returns a dict (not a list) with a
# condition_id.  This is the "slug starts with 0x" CLOB fallback path
# when Gamma returns a dict instead of a list.
# ---------------------------------------------------------------------------

class TestGetMarketGammaDictFallback:
    def test_gamma_returns_dict_with_condition_id(
        self, client: PolymarketClient, httpx_mock
    ):
        """When _gamma_get returns a *dict* (not a list) that has a
        condition_id, get_market should use it directly (lines 122-124)."""
        market_dict = {
            "condition_id": "0xdict1",
            "conditionId": "0xdict1",
            "slug": "dict-market",
            "question": "Dict market?",
            "description": "",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.50","0.50"]',
            "tokens": json.dumps([
                {"token_id": "tok_d_yes", "outcome": "Yes"},
                {"token_id": "tok_d_no", "outcome": "No"},
            ]),
            "active": True,
            "closed": False,
        }
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "dict-market"}),
            json=market_dict,
        )
        market = client.get_market("dict-market")
        assert market.condition_id == "0xdict1"
        assert market.slug == "dict-market"


# ---------------------------------------------------------------------------
# Lines 123-124 (and 127-146): get_market CLOB fallback path when slug
# starts with 0x and CLOB returns valid data.
# ---------------------------------------------------------------------------

class TestGetMarketClobFallbackPath:
    def test_clob_returns_data_gamma_enrichment_succeeds(
        self, client: PolymarketClient, httpx_mock
    ):
        """Gamma returns empty list for 0x slug, CLOB returns abbreviated
        market data, and Gamma enrichment by condition_ids succeeds."""
        # Gamma slug lookups return empty (open, then closed=true retry)
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xclob1"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xclob1", "closed": "true"},
            ),
            json=[],
        )
        # CLOB abbreviated payload (no slug)
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xclob1"),
            json=_clob_market("0xclob1"),
        )
        # Gamma enrichment lookup succeeds
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"condition_ids": "0xclob1"}),
            json=[SAMPLE_GAMMA_MARKET],
        )
        market = client.get_market("0xclob1")
        # Should return the Gamma-enriched data
        assert market.condition_id == "0xabc123"  # from SAMPLE_GAMMA_MARKET
        assert market.slug == "will-bitcoin-hit-100k"
        # The first enrichment lookup hit, so no closed=true retry was issued
        assert _condition_id_lookups(httpx_mock) == [{"condition_ids": "0xclob1"}]

    def test_clob_enrichment_retries_with_closed_for_closed_market(
        self, client: PolymarketClient, httpx_mock
    ):
        """A closed market is invisible to the default condition_ids lookup
        (Gamma defaults `closed` to false) and found by the closed=true retry,
        so the CLOB stub no longer misreports it as open."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xclosed1"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xclosed1", "closed": "true"},
            ),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xclosed1"),
            json=_clob_market("0xclosed1"),
        )
        # Open lookup: Gamma hides the closed market
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets", params={"condition_ids": "0xclosed1"}
            ),
            json=[],
        )
        # closed=true lookup finds it
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"condition_ids": "0xclosed1", "closed": "true"},
            ),
            json=[{**SAMPLE_GAMMA_MARKET, "closed": True, "active": False}],
        )
        market = client.get_market("0xclosed1")
        # Real slug/question come back from the retry...
        assert market.slug == "will-bitcoin-hit-100k"
        assert market.question == "Will Bitcoin hit $100k by end of 2026?"
        # ...and the market is reported closed, not as the CLOB stub's open
        assert market.closed is True
        assert market.active is False
        assert _condition_id_lookups(httpx_mock) == [
            {"condition_ids": "0xclosed1"},
            {"condition_ids": "0xclosed1", "closed": "true"},
        ]

    def test_clob_enrichment_ignores_unusable_gamma_payload(
        self, client: PolymarketClient, httpx_mock
    ):
        """A condition_ids response with no usable market (a bare dict, then a
        list without a condition id) keeps the CLOB stub after both attempts."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xunusable"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xunusable", "closed": "true"},
            ),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xunusable"),
            json=_clob_market("0xunusable", mts=0.005),
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets", params={"condition_ids": "0xunusable"}
            ),
            json={"error": "unexpected format"},
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"condition_ids": "0xunusable", "closed": "true"},
            ),
            json=[{"slug": "no-condition-id"}],
        )
        market = client.get_market("0xunusable")
        assert market.condition_id == "0xunusable"
        assert market.tick_size == 0.005
        assert _condition_id_lookups(httpx_mock) == [
            {"condition_ids": "0xunusable"},
            {"condition_ids": "0xunusable", "closed": "true"},
        ]

    def test_clob_returns_data_gamma_enrichment_fails_falls_back_to_clob(
        self, client: PolymarketClient, httpx_mock
    ):
        """Gamma enrichment raises an exception -> falls back to CLOB-only
        data."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xclob2"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xclob2", "closed": "true"},
            ),
            json=[],
        )
        # CLOB abbreviated payload with a 0.0025 tick and min order size
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xclob2"),
            json=_clob_market("0xclob2", mts=0.005, mos=10.0),
        )
        # Gamma enrichment lookup fails
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"condition_ids": "0xclob2"}),
            status_code=500,
            text="Internal Server Error",
        )
        market = client.get_market("0xclob2")
        # Should return the CLOB-only parsed market
        assert market.condition_id == "0xclob2"
        assert market.tick_size == 0.005
        assert market.min_order_size == 10.0

    def test_clob_returns_data_no_slug_falls_back_to_clob(
        self, client: PolymarketClient, httpx_mock
    ):
        """Abbreviated CLOB data has no slug and the condition_ids enrichment
        finds nothing (open, then closed=true); falls back to CLOB-only data."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xnoslugs"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xnoslugs", "closed": "true"},
            ),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xnoslugs"),
            json=_clob_market("0xnoslugs"),
        )
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"condition_ids": "0xnoslugs"}),
            json=[],
        )
        # closed=true retry also finds nothing
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"condition_ids": "0xnoslugs", "closed": "true"},
            ),
            json=[],
        )
        market = client.get_market("0xnoslugs")
        assert market.condition_id == "0xnoslugs"
        assert market.slug == ""

    def test_clob_returns_data_gamma_enrichment_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """A CLOB payload carrying a legacy slug is enriched by slug, and an
        empty enrichment result falls back to CLOB-only data."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "0xclob3"}),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(
                GAMMA_BASE + "/markets",
                params={"slug": "0xclob3", "closed": "true"},
            ),
            json=[],
        )
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/clob-markets/0xclob3"),
            json=_clob_market("0xclob3", market_slug="gamma-empty-result"),
        )
        # Gamma enrichment lookup returns empty list (no match)
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/markets", params={"slug": "gamma-empty-result"}),
            json=[],
        )
        market = client.get_market("0xclob3")
        # Should fall back to CLOB-only parsed market
        assert market.condition_id == "0xclob3"
        assert market.slug == "gamma-empty-result"


# ---------------------------------------------------------------------------
# Line 170: list_markets returns non-list -> returns []
# ---------------------------------------------------------------------------

class TestListMarketsNonList:
    def test_non_list_response_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """When _gamma_get returns a dict instead of a list,
        list_markets should return [] (line 170)."""
        httpx_mock.add_response(json={"error": "unexpected format"})
        result = client.list_markets()
        assert result == []

    def test_string_response_returns_empty(
        self, client: PolymarketClient
    ):
        """When _gamma_get returns a non-list (string), return []."""
        with patch.object(client, "_gamma_get", return_value="not a list"):
            result = client.list_markets()
            assert result == []

    def test_parse_market_list_non_list(self, client: PolymarketClient):
        """_parse_market_list itself still rejects non-list input."""
        assert client._parse_market_list("not a list") == []
        assert client._parse_market_list({"markets": []}) == []


# ---------------------------------------------------------------------------
# search_markets with an unexpected envelope -> []
# ---------------------------------------------------------------------------

class TestSearchMarketsNonList:
    def test_response_without_events_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """An envelope with no usable events list returns []."""
        httpx_mock.add_response(json={"error": "unexpected format"})
        result = client.search_markets("bitcoin")
        assert result == []

    def test_none_response_returns_empty(
        self, client: PolymarketClient
    ):
        """When _gamma_get returns None, search_markets should return []."""
        with patch.object(client, "_gamma_get", return_value=None):
            result = client.search_markets("bitcoin")
            assert result == []

    def test_list_response_returns_empty(
        self, client: PolymarketClient
    ):
        """A bare list (the old /markets shape) is no longer an envelope."""
        with patch.object(client, "_gamma_get", return_value=[SAMPLE_GAMMA_MARKET]):
            result = client.search_markets("bitcoin")
            assert result == []


# ---------------------------------------------------------------------------
# Line 213: get_tick_size cache hit path
# ---------------------------------------------------------------------------

class TestGetTickSizeCacheHit:
    def test_tick_size_served_from_cache(
        self, client: PolymarketClient, httpx_mock
    ):
        """After fetching tick_size once, the second call should be served
        from cache without another HTTP request (line 213)."""
        httpx_mock.add_response(
            url=httpx.URL(CLOB_BASE + "/tick-size", params={"token_id": "tok_cache"}),
            json={"minimum_tick_size": 0.005},
        )
        t1 = client.get_tick_size("tok_cache")
        t2 = client.get_tick_size("tok_cache")
        assert t1 == 0.005
        assert t2 == 0.005
        # Only one HTTP request should have been made
        assert len(httpx_mock.get_requests()) == 1

    def test_tick_size_cache_returns_correct_value(
        self, client: PolymarketClient
    ):
        """Directly seed the cache and verify get_tick_size reads from it
        without any HTTP call (line 213)."""
        client._set_cached("tick_size:tok_direct", {"minimum_tick_size": 0.002})
        # No httpx_mock needed — should not make any HTTP call
        result = client.get_tick_size("tok_direct")
        assert result == 0.002


# ---------------------------------------------------------------------------
# list_markets sort_by="liquidity" branch (lines 164-166)
# ---------------------------------------------------------------------------


class TestListMarketsLiquidity:
    def test_sort_by_liquidity(
        self, client: PolymarketClient, httpx_mock
    ):
        """list_markets with sort_by='liquidity' sets order=liquidityNum."""
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        markets = client.list_markets(sort_by="liquidity")
        assert len(markets) == 1
        req = httpx_mock.get_requests()[0]
        assert "order=liquidityNum" in str(req.url)
        assert "ascending=false" in str(req.url)


# ---------------------------------------------------------------------------
# _parse_market clobTokenIds as JSON string (lines 308-312)
# ---------------------------------------------------------------------------


class TestParseMarketClobTokenIds:
    def test_clob_token_ids_string(self):
        """When clobTokenIds is a JSON string, parse and map to outcomes."""
        data = {
            "condition_id": "0xcondition",
            "slug": "test-clob-ids",
            "question": "Test?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.60", "0.40"]',
            "clobTokenIds": '["tok_yes_clob", "tok_no_clob"]',
            "active": True,
            "closed": False,
        }
        market = _parse_market(data)
        assert len(market.tokens) == 2
        assert market.tokens[0]["token_id"] == "tok_yes_clob"
        assert market.tokens[0]["outcome"] == "Yes"
        assert market.tokens[1]["token_id"] == "tok_no_clob"
        assert market.tokens[1]["outcome"] == "No"

    def test_clob_token_ids_list(self):
        """When clobTokenIds is already a list, use directly."""
        data = {
            "condition_id": "0xcondition2",
            "slug": "test-clob-ids-list",
            "question": "Test?",
            "outcomes": '["A", "B", "C"]',
            "outcomePrices": '["0.33", "0.33", "0.34"]',
            "clobTokenIds": ["tok_a", "tok_b", "tok_c"],
            "active": True,
            "closed": False,
        }
        market = _parse_market(data)
        assert len(market.tokens) == 3
        assert market.tokens[2]["outcome"] == "C"

    def test_clob_token_ids_more_ids_than_outcomes(self):
        """Extra token IDs beyond outcomes get 'OutcomeN' names."""
        data = {
            "condition_id": "0xcondition3",
            "slug": "test-extra-ids",
            "question": "Test?",
            "outcomes": '["Yes"]',
            "outcomePrices": '["0.60"]',
            "clobTokenIds": '["tok1", "tok2"]',
            "active": True,
            "closed": False,
        }
        market = _parse_market(data)
        assert market.tokens[0]["outcome"] == "Yes"
        assert market.tokens[1]["outcome"] == "Outcome1"


# ---------------------------------------------------------------------------
# get_tags tests
# ---------------------------------------------------------------------------


class TestGetTags:
    def test_get_tags(self, client: PolymarketClient, httpx_mock):
        tags = [
            {"id": "1", "label": "Politics", "slug": "politics"},
            {"id": "2", "label": "Crypto", "slug": "crypto"},
        ]
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags"),
            json=tags,
        )
        result = client.get_tags()
        assert len(result) == 2
        assert result[0]["slug"] == "politics"

    def test_get_tags_cached(self, client: PolymarketClient, httpx_mock):
        tags = [{"id": "1", "label": "Sports", "slug": "sports"}]
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags"),
            json=tags,
        )
        r1 = client.get_tags()
        r2 = client.get_tags()
        assert r1 == r2
        assert len(httpx_mock.get_requests()) == 1

    def test_get_tags_non_list(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags"),
            json={"error": "unexpected"},
        )
        result = client.get_tags()
        assert result == []


# ---------------------------------------------------------------------------
# get_markets_by_tag tests
# ---------------------------------------------------------------------------


class TestGetMarketsByTag:
    def test_get_markets_by_tag(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/politics"),
            json={"id": "745", "slug": "politics", "label": "Politics"},
        )
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        markets = client.get_markets_by_tag("politics", limit=5)
        assert len(markets) == 1
        assert markets[0].slug == "will-bitcoin-hit-100k"
        req = httpx_mock.get_requests()[1]
        assert req.url.params["tag_id"] == "745"
        assert req.url.params["limit"] == "5"

    @pytest.mark.parametrize(
        "tag_body, expected_id",
        [
            ({"id": "745"}, "745"),
            ({"id": 745}, "745"),
            ({"tagID": 42}, "42"),
            ({"tag_id": "77"}, "77"),
            ({"id": None, "tag_id": "9"}, "9"),
        ],
    )
    def test_tag_id_resolution_variants(
        self, client: PolymarketClient, httpx_mock, tag_body, expected_id
    ):
        """The tag id is accepted as int or digit-string, under `id`,
        `tagID`, or `tag_id` keys."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/nba"), json=tag_body
        )
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        markets = client.get_markets_by_tag("nba")
        assert len(markets) == 1
        req = httpx_mock.get_requests()[1]
        assert req.url.params["tag_id"] == expected_id

    def test_get_markets_by_tag_unknown_slug_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """A 404 on /tags/slug maps to [] (legacy bogus-slug behavior)."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/nonexistent"),
            status_code=404,
            text="Not Found",
        )
        markets = client.get_markets_by_tag("nonexistent")
        assert markets == []
        assert len(httpx_mock.get_requests()) == 1

    def test_get_markets_by_tag_unusable_id_returns_empty(
        self, client: PolymarketClient, httpx_mock
    ):
        """A tag payload without a usable numeric id yields []."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/weird"),
            json={"id": "not-numeric", "slug": "weird"},
        )
        assert client.get_markets_by_tag("weird") == []
        assert len(httpx_mock.get_requests()) == 1

    def test_get_markets_by_tag_non_dict_tag_response(
        self, client: PolymarketClient, httpx_mock
    ):
        """A non-dict /tags/slug body yields [] (no usable id)."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/bad"), json="huh"
        )
        assert client.get_markets_by_tag("bad") == []

    def test_get_markets_by_tag_api_error_propagates(
        self, client: PolymarketClient, httpx_mock
    ):
        """Non-404 errors on the tag lookup are re-raised."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/err"),
            status_code=500,
            text="Internal Server Error",
        )
        with pytest.raises(ApiError) as exc_info:
            client.get_markets_by_tag("err")
        assert exc_info.value.status_code == 500

    def test_tag_lookup_cached_on_second_call(
        self, client: PolymarketClient, httpx_mock
    ):
        """The slug→id resolution is cached; only the listing refetches."""
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/nba"),
            json={"id": "745", "slug": "nba", "label": "NBA"},
        )
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        client.get_markets_by_tag("nba")
        client.get_markets_by_tag("nba")
        requests = httpx_mock.get_requests()
        tag_requests = [r for r in requests if r.url.path == "/tags/slug/nba"]
        list_requests = [r for r in requests if r.url.path == "/markets/keyset"]
        assert len(tag_requests) == 1
        assert len(list_requests) == 2

    def test_get_markets_by_tag_closed(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/tags/slug/politics"),
            json={"id": "745", "slug": "politics", "label": "Politics"},
        )
        httpx_mock.add_response(json={"markets": [SAMPLE_GAMMA_MARKET]})
        markets = client.get_markets_by_tag("politics", closed=True)
        assert len(markets) == 1
        req = httpx_mock.get_requests()[1]
        assert "closed=true" in str(req.url)
        assert "active=false" in str(req.url)


# ---------------------------------------------------------------------------
# get_event tests
# ---------------------------------------------------------------------------


class TestGetEvent:
    def test_get_event(self, client: PolymarketClient, httpx_mock):
        event_data = {
            "title": "US Elections 2028",
            "slug": "us-elections-2028",
            "markets": [{"slug": "who-wins-2028"}],
        }
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/events/slug/us-elections-2028"),
            json=event_data,
        )
        result = client.get_event("us-elections-2028")
        assert result["title"] == "US Elections 2028"
        assert len(result["markets"]) == 1

    def test_get_event_cached(self, client: PolymarketClient, httpx_mock):
        event_data = {"title": "Event", "slug": "evt"}
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/events/slug/evt"),
            json=event_data,
        )
        r1 = client.get_event("evt")
        r2 = client.get_event("evt")
        assert r1 == r2
        assert len(httpx_mock.get_requests()) == 1

    def test_get_event_non_dict(self, client: PolymarketClient, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(GAMMA_BASE + "/events/slug/bad"),
            json=[],
        )
        result = client.get_event("bad")
        assert result == {}
