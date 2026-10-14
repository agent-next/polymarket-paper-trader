"""Tests for the read-only Kalshi market data client.

Every response the client parses is a recording of the live public API,
captured by ``tests/fixtures/capture_kalshi_fixtures.py`` and replayed here
through pytest-httpx.  Nothing in this file hand-writes a Kalshi payload; the
few derived payloads (``_page``, ``_with_market_fields``) start from a recorded
response and override single fields, so the wire shape stays the oracle.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from pm_trader.api import CACHE_TTL_SECONDS
from pm_trader.db import Database
from pm_trader.kalshi_api import (
    KALSHI_BASE,
    MAX_RESULTS,
    KalshiClient,
    _list_cache_key,
    _next_cursor,
    _parse_market,
    _parse_market_list,
    _parse_order_book,
    _to_float,
)
from pm_trader.models import ApiError, Market, MarketNotFoundError, OrderBook

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MARKETS_PAGE1 = "kalshi_markets_page1.json"
MARKETS_PAGE2 = "kalshi_markets_page2.json"
MARKETS_EMPTY = "kalshi_markets_empty.json"
MARKET = "kalshi_market.json"
ORDERBOOK = "kalshi_orderbook.json"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_data_dir: Path) -> Database:
    database = Database(tmp_data_dir)
    database.init_schema()
    return database


@pytest.fixture
def client(db: Database) -> KalshiClient:
    c = KalshiClient(db)
    yield c
    c.close()


def _fixture(name: str) -> Any:
    """Load a recorded live Kalshi response."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _raw_market() -> dict:
    """The recorded `/markets/{ticker}` payload (the `market` envelope)."""
    return _fixture(MARKET)["market"]


def _page(name: str, count: int | None = None, cursor: str | None = None) -> dict:
    """A recorded `/markets` page, optionally cloned to ``count`` markets.

    Cloning only rewrites ``ticker`` so the pagination tests can walk more
    markets than the two the recording holds.
    """
    page = _fixture(name)
    markets = page["markets"]
    if count is not None:
        template = markets[0]
        markets = [
            {**template, "ticker": f"{template['ticker']}-{i}"} for i in range(count)
        ]
    if cursor is not None:
        page = {**page, "cursor": cursor}
    return {**page, "markets": markets}


def _with_market_fields(**overrides: object) -> dict:
    """The recorded market payload with single fields overridden."""
    return {**_raw_market(), **overrides}


def _requests(httpx_mock) -> list[httpx.Request]:
    return list(httpx_mock.get_requests())


# ---------------------------------------------------------------------------
# list_markets
# ---------------------------------------------------------------------------


class TestListMarkets:
    def test_maps_a_recorded_page(self, client: KalshiClient, httpx_mock) -> None:
        page = _fixture(MARKETS_PAGE1)
        httpx_mock.add_response(json=page)

        markets = client.list_markets(limit=2, series_ticker="KXNBAGAME")

        raw = page["markets"][0]
        assert [m.condition_id for m in markets] == [m["ticker"] for m in page["markets"]]
        assert markets[0].active is True
        assert markets[0].closed is False
        assert markets[0].accepting_orders is True
        assert markets[0].question == raw["title"]
        assert markets[0].description == raw["rules_primary"]
        assert markets[0].outcomes == ["Yes", "No"]
        assert markets[0].yes_price == pytest.approx(float(raw["last_price_dollars"]))
        assert markets[0].no_price == pytest.approx(1.0 - float(raw["last_price_dollars"]))
        assert markets[0].yes_token_id == raw["ticker"]
        assert markets[0].no_token_id == raw["ticker"]
        assert markets[0].volume == pytest.approx(float(raw["volume_fp"]))
        assert markets[0].end_date == raw["close_time"]
        assert markets[0].tick_size == pytest.approx(0.01)
        # Nothing on the wire maps onto these.
        assert markets[0].liquidity == 0.0
        assert markets[0].fee_rate_bps == 0
        assert markets[0].fee_schedule is None

    def test_sends_filters_and_page_size(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json=_page(MARKETS_PAGE1, cursor=""))
        recorded = _fixture(MARKETS_PAGE1)["markets"]
        tickers = ",".join(m["ticker"] for m in recorded)

        client.list_markets(
            limit=7,
            status=None,
            event_ticker="KXNBAGAME-26OCT16DENLAL",
            tickers=tickers,
        )

        params = _requests(httpx_mock)[0].url.params
        assert "status" not in params
        assert params["event_ticker"] == "KXNBAGAME-26OCT16DENLAL"
        assert params["tickers"] == tickers
        assert params["limit"] == "7"

    def test_follows_cursors(self, client: KalshiClient, httpx_mock) -> None:
        page1 = _fixture(MARKETS_PAGE1)
        page2 = _fixture(MARKETS_PAGE2)
        httpx_mock.add_response(json=page1)
        httpx_mock.add_response(json=page2)

        markets = client.list_markets(limit=4, series_ticker="KXNBAGAME")

        assert [m.condition_id for m in markets] == [
            m["ticker"] for m in page1["markets"] + page2["markets"]
        ]
        requests = _requests(httpx_mock)
        assert len(requests) == 2
        assert "cursor" not in requests[0].url.params
        assert requests[1].url.params["cursor"] == page1["cursor"]

    def test_stops_on_a_repeated_cursor(self, client: KalshiClient, httpx_mock) -> None:
        page = _fixture(MARKETS_PAGE1)
        httpx_mock.add_response(json=page)
        httpx_mock.add_response(json=page)

        markets = client.list_markets(limit=10, series_ticker="KXNBAGAME")

        # The second page replays the first: deduped, and the walk stops.
        assert len(markets) == 2
        assert len(_requests(httpx_mock)) == 2

    def test_caps_at_max_results(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json=_page(MARKETS_PAGE1, count=50, cursor="page-2"))
        httpx_mock.add_response(json=_page(MARKETS_PAGE2, count=50, cursor=""))

        markets = client.list_markets(limit=1_000, series_ticker="KXNBAGAME")

        assert len(markets) == MAX_RESULTS
        limits = [r.url.params["limit"] for r in _requests(httpx_mock)]
        assert limits == ["100", "50"]

    def test_zero_limit_never_calls_the_api(self, client: KalshiClient, httpx_mock) -> None:
        assert client.list_markets(limit=0) == []
        assert _requests(httpx_mock) == []

    def test_empty_result_is_not_cached(self, client: KalshiClient, db, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture(MARKETS_EMPTY))
        httpx_mock.add_response(json=_page(MARKETS_PAGE1, cursor=""))

        assert client.list_markets(event_ticker="KXNOTAREALEVENT") == []
        assert db.get_cache(_list_cache_key({"event_ticker": "KXNOTAREALEVENT", "limit": 20})) is None

        # The second call goes back to the wire instead of replaying the miss.
        assert len(client.list_markets(event_ticker="KXNOTAREALEVENT")) == 2
        assert len(_requests(httpx_mock)) == 2

    def test_caches_metadata(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json=_page(MARKETS_PAGE1, cursor=""))

        first = client.list_markets(series_ticker="KXNBAGAME")
        second = client.list_markets(series_ticker="KXNBAGAME")

        assert [m.condition_id for m in first] == [m.condition_id for m in second]
        assert len(_requests(httpx_mock)) == 1

    def test_expired_cache_is_refetched(self, client: KalshiClient, db, httpx_mock) -> None:
        httpx_mock.add_response(json=_page(MARKETS_PAGE1, cursor=""))
        key = _list_cache_key({"series_ticker": "KXNBAGAME", "status": "open", "limit": 20})
        stale = (datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS + 60)).isoformat()
        db.conn.execute(
            "INSERT INTO market_cache (cache_key, data, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps([_raw_market()]), stale),
        )
        db.conn.commit()

        assert len(client.list_markets(series_ticker="KXNBAGAME")) == 2
        assert len(_requests(httpx_mock)) == 1

    def test_malformed_envelope_yields_nothing(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json=[_raw_market()])

        assert client.list_markets() == []
        assert len(_requests(httpx_mock)) == 1

    def test_bad_status_raises_api_error(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"code": "bad_request", "message": "bad request"}},
        )

        with pytest.raises(ApiError) as exc:
            client.list_markets(status="bogus")

        assert exc.value.status_code == 400
        assert "Kalshi API error: 400" in exc.value.message

    def test_market_without_a_ticker_raises(self, client: KalshiClient, httpx_mock) -> None:
        broken = {k: v for k, v in _raw_market().items() if k != "ticker"}
        httpx_mock.add_response(json={"cursor": "", "markets": [broken]})

        with pytest.raises(ApiError, match="without a ticker"):
            client.list_markets()


# ---------------------------------------------------------------------------
# get_market
# ---------------------------------------------------------------------------


class TestGetMarket:
    def test_maps_a_recorded_market(self, client: KalshiClient, httpx_mock) -> None:
        raw = _raw_market()
        httpx_mock.add_response(json=_fixture(MARKET))

        market = client.get_market(raw["ticker"])

        assert market.condition_id == raw["ticker"]
        assert market.slug == raw["ticker"]
        assert market.tick_size == pytest.approx(
            float(raw["price_ranges"][0]["step"])
        )
        assert _requests(httpx_mock)[0].url.path == (
            f"/trade-api/v2/markets/{raw['ticker']}"
        )

    def test_caches_metadata(self, client: KalshiClient, httpx_mock) -> None:
        raw = _raw_market()
        httpx_mock.add_response(json=_fixture(MARKET))

        first = client.get_market(raw["ticker"])
        second = client.get_market(raw["ticker"])

        assert first == second
        assert len(_requests(httpx_mock)) == 1

    def test_expired_cache_is_refetched(self, client: KalshiClient, db, httpx_mock) -> None:
        raw = _raw_market()
        key = f"kalshi:market:{raw['ticker']}"
        stale = (datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_SECONDS + 60)).isoformat()
        db.conn.execute(
            "INSERT INTO market_cache (cache_key, data, fetched_at) VALUES (?, ?, ?)",
            (key, json.dumps(_with_market_fields(title="stale copy")), stale),
        )
        db.conn.commit()
        httpx_mock.add_response(json=_fixture(MARKET))

        market = client.get_market(raw["ticker"])

        assert market.question == raw["title"]
        assert len(_requests(httpx_mock)) == 1

    def test_settled_market_uses_the_settlement_value(
        self, client: KalshiClient, httpx_mock
    ) -> None:
        raw = _raw_market()
        httpx_mock.add_response(
            json={
                "market": _with_market_fields(
                    status="finalized", result="no", settlement_value_dollars="0.0000"
                )
            }
        )

        market = client.get_market(raw["ticker"])

        assert market.outcome_prices == [0.0, 1.0]
        assert market.closed is True
        assert market.active is False

    @pytest.mark.parametrize(
        "status,active,closed",
        [
            ("initialized", False, False),
            ("inactive", False, False),
            ("active", True, False),
            ("closed", False, True),
            ("determined", False, True),
            ("disputed", False, True),
            ("amended", False, True),
            ("finalized", False, True),
        ],
    )
    def test_status_mapping(
        self, client: KalshiClient, httpx_mock, status: str, active: bool, closed: bool
    ) -> None:
        raw = _raw_market()
        httpx_mock.add_response(json={"market": _with_market_fields(status=status)})

        market = client.get_market(raw["ticker"])

        assert market.active is active
        assert market.closed is closed
        assert market.accepting_orders is active

    def test_unknown_ticker_raises_market_not_found(
        self, client: KalshiClient, httpx_mock
    ) -> None:
        httpx_mock.add_response(
            status_code=404, json={"error": {"code": "not_found", "message": "not found"}}
        )

        with pytest.raises(MarketNotFoundError) as exc:
            client.get_market("KXNOTAREALTICKER")

        assert exc.value.identifier == "KXNOTAREALTICKER"
        assert exc.value.code == "MARKET_NOT_FOUND"

    @pytest.mark.parametrize(
        "ticker", ["../series/KXNBAGAME", "KX?status=open", "kx-lower", "", "KX/A"]
    )
    def test_non_ticker_never_reaches_the_network(
        self, client: KalshiClient, httpx_mock, ticker: str
    ) -> None:
        with pytest.raises(MarketNotFoundError):
            client.get_market(ticker)
        with pytest.raises(MarketNotFoundError):
            client.get_order_book(ticker)
        assert httpx_mock.get_requests() == []

    def test_http_error_keeps_the_status(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(status_code=500, text="upstream boom")

        with pytest.raises(ApiError) as exc:
            client.get_market("KXNBAGAME-26OCT16DENLAL-LAL")

        assert exc.value.status_code == 500
        assert "upstream boom" in exc.value.message
        assert exc.value.code == "API_ERROR"

    def test_transport_error_raises_api_error(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("no route to host"))

        with pytest.raises(ApiError, match="Kalshi API request failed"):
            client.get_market("KXNBAGAME-26OCT16DENLAL-LAL")

    def test_missing_market_payload_raises(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json={})

        with pytest.raises(ApiError, match="returned no market"):
            client.get_market("KXNBAGAME-26OCT16DENLAL-LAL")

    def test_market_payload_without_a_ticker_raises(
        self, client: KalshiClient, httpx_mock
    ) -> None:
        broken = {k: v for k, v in _raw_market().items() if k != "ticker"}
        httpx_mock.add_response(json={"market": broken})

        with pytest.raises(ApiError, match="without a ticker"):
            client.get_market("KXNBAGAME-26OCT16DENLAL-LAL")


# ---------------------------------------------------------------------------
# get_order_book
# ---------------------------------------------------------------------------


class TestGetOrderBook:
    def test_maps_yes_bids_and_derived_asks(
        self, client: KalshiClient, httpx_mock
    ) -> None:
        raw = _fixture(ORDERBOOK)["orderbook_fp"]
        httpx_mock.add_response(json=_fixture(ORDERBOOK))

        book = client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL", depth=2)

        # yes ladder arrives ascending, best level last
        assert [(lvl.price, lvl.size) for lvl in book.bids] == [
            (0.58, 21.00), (0.57, 482.00)
        ]
        # a no bid at 0.40 is a yes ask at 0.60
        assert [(lvl.price, lvl.size) for lvl in book.asks] == [
            (0.60, 521.00), (0.62, 470.52)
        ]
        assert book.asks[0].price < book.asks[1].price
        assert book.bids[0].price > book.bids[1].price
        assert raw["yes_dollars"][-1][0] == "0.5800"
        assert _requests(httpx_mock)[0].url.params["depth"] == "2"

    def test_full_book_omits_depth(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture(ORDERBOOK))

        client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

        assert "depth" not in _requests(httpx_mock)[0].url.params

    def test_ask_prices_are_complement_rounded_to_the_grid(
        self, client: KalshiClient, httpx_mock
    ) -> None:
        recorded = _fixture(ORDERBOOK)
        book_json = {
            "orderbook_fp": {
                "yes_dollars": [["0.6100", "1.00"]],
                "no_dollars": [["0.3900", "2.00"]],
            }
        }
        assert recorded["orderbook_fp"]["yes_dollars"]  # the recording is the base
        httpx_mock.add_response(json=book_json)

        book = client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

        assert book.asks[0].price == 0.61  # not 0.6100000000000001
        assert book.bids[0].price == 0.61

    def test_empty_ladders(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(
            json={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
        )

        book = client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

        assert book == OrderBook(bids=[], asks=[])

    def test_never_cached(self, client: KalshiClient, db, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture(ORDERBOOK))
        httpx_mock.add_response(json=_fixture(ORDERBOOK))

        client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")
        client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

        assert len(_requests(httpx_mock)) == 2
        assert db.get_cache("kalshi:market:KXNBAGAME-26OCT16DENLAL-LAL") is None
        assert _requests(httpx_mock)[0].url.host == KALSHI_BASE.split("/")[2]

    def test_http_error_keeps_the_status(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(
            status_code=404, json={"error": {"code": "not_found", "message": "not found"}}
        )

        with pytest.raises(ApiError) as exc:
            client.get_order_book("KXNOTAREALTICKER")

        assert exc.value.status_code == 404

    def test_payload_without_orderbook_fp_raises(
        self, client: KalshiClient, httpx_mock
    ) -> None:
        httpx_mock.add_response(json={"markets": []})

        with pytest.raises(ApiError, match="without orderbook_fp"):
            client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

    def test_non_dict_payload_raises(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json=[_fixture(ORDERBOOK)])

        with pytest.raises(ApiError, match="without orderbook_fp"):
            client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

    def test_missing_ladder_raises(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(json={"orderbook_fp": {"yes_dollars": []}})

        with pytest.raises(ApiError, match="no no_dollars ladder"):
            client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

    def test_malformed_level_raises(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(
            json={
                "orderbook_fp": {
                    "yes_dollars": [["0.5800"]],
                    "no_dollars": [["0.4000", "1.00"]],
                }
            }
        )

        with pytest.raises(ApiError, match="malformed yes_dollars level"):
            client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

    def test_non_pair_level_raises(self, client: KalshiClient, httpx_mock) -> None:
        httpx_mock.add_response(
            json={
                "orderbook_fp": {
                    "yes_dollars": [{"price": "0.58", "size": "1"}],
                    "no_dollars": [["0.4000", "1.00"]],
                }
            }
        )

        with pytest.raises(ApiError, match="malformed yes_dollars level"):
            client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")


# ---------------------------------------------------------------------------
# Unit conversion and price grids
# ---------------------------------------------------------------------------


class TestUnitConversion:
    def test_dollar_strings_and_fixed_point_counts(self, client, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture(ORDERBOOK))

        book = client.get_order_book("KXNBAGAME-26OCT16DENLAL-LAL")

        # [price_dollars, contracts] — the second element is a quantity.
        assert isinstance(book.bids[0].price, float)
        assert book.bids[0].size == 21.00

    def test_to_float_defaults(self) -> None:
        assert _to_float("0.5800") == pytest.approx(0.58)
        assert _to_float("304.24") == pytest.approx(304.24)
        assert _to_float(None) == 0.0
        assert _to_float("") == 0.0
        assert _to_float("not-a-number") == 0.0
        assert _to_float(None, default=1.5) == 1.5
        assert _to_float("0.5000", default=1.5) == pytest.approx(0.5)

    def test_tick_size_is_the_finest_price_range_step(self) -> None:
        subcent = {
            **(_raw_market()),
            "price_level_structure": "center_deci_edge_centi_cent",
            "price_ranges": [
                {"start": "0.0000", "end": "0.0100", "step": "0.0001"},
                {"start": "0.0100", "end": "0.9900", "step": "0.0010"},
                {"start": "0.9900", "end": "1.0000", "step": "0.0001"},
            ],
        }
        market = _parse_market(subcent)
        assert market.tick_size == pytest.approx(0.0001)

    def test_missing_or_broken_price_ranges_leave_no_grid(self) -> None:
        assert _parse_market({**_raw_market(), "price_ranges": None}).tick_size == 0.0
        assert _parse_market({**_raw_market(), "price_ranges": []}).tick_size == 0.0
        assert _parse_market({**_raw_market(), "price_ranges": ["x"]}).tick_size == 0.0
        assert (
            _parse_market({**_raw_market(), "price_ranges": [{"step": "0.00"}]}).tick_size
            == 0.0
        )

    def test_market_without_optional_fields_uses_defaults(self) -> None:
        market = _parse_market({"ticker": "KXBARE-1"})

        assert isinstance(market, Market)
        assert market.question == ""
        assert market.description == ""
        assert market.end_date == ""
        assert market.outcome_prices == [0.0, 1.0]
        assert market.active is False
        assert market.volume == 0.0

    def test_parse_market_list_and_cursor(self) -> None:
        markets = _parse_market_list([_raw_market(), _raw_market()])
        assert len(markets) == 2
        assert all(isinstance(m, Market) for m in markets)

        assert _next_cursor({"cursor": "abc"}) == "abc"
        assert _next_cursor({"cursor": ""}) is None
        assert _next_cursor({"cursor": None}) is None
        assert _next_cursor({"cursor": 7}) is None
        assert _next_cursor({}) is None
        assert _next_cursor([]) is None

    def test_parse_market_rejects_non_dicts(self) -> None:
        with pytest.raises(ApiError, match="malformed market payload"):
            _parse_market([_raw_market()])
        with pytest.raises(ApiError, match="without a ticker"):
            _parse_market({"ticker": 7, "status": "active"})

    def test_parse_order_book_rejects_non_dicts(self) -> None:
        with pytest.raises(ApiError, match="without orderbook_fp"):
            _parse_order_book({"orderbook_fp": []})


# ---------------------------------------------------------------------------
# Live (excluded from `make check`, like the Polymarket live tests)
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestKalshiLive:
    def test_live_market_and_book(self, client: KalshiClient) -> None:
        markets = _live(
            lambda: client.list_markets(limit=3, series_ticker="KXNBAGAME")
        )
        if not markets:
            pytest.skip("no open markets in the live Kalshi API")
        market = markets[0]
        book = _live(lambda: client.get_order_book(market.condition_id, depth=2))

        assert market.condition_id.startswith("KX")
        assert market.outcomes == ["Yes", "No"]
        assert 0.0 <= market.yes_price <= 1.0
        assert market.no_price == pytest.approx(1.0 - market.yes_price)
        assert market.tick_size in (0.01, 0.005, 0.002, 0.001, 0.0001)
        assert book.bids == sorted(book.bids, key=lambda l: l.price, reverse=True)
        assert book.asks == sorted(book.asks, key=lambda l: l.price)
        assert all(0.0 < lvl.price < 1.0 for lvl in book.bids + book.asks)
        assert all(lvl.size > 0 for lvl in book.bids + book.asks)


def _live(call: Callable[[], Any]) -> Any:
    """Run a live call, skipping the test when the API is unreachable."""
    try:
        return call()
    except (ApiError, MarketNotFoundError) as e:
        pytest.skip(f"Kalshi API unavailable: {e}")
