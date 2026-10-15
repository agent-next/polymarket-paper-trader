"""Tests for paper trading Kalshi markets through the engine.

Every Kalshi response is a recording of the live public API (see
``tests/fixtures/capture_kalshi_fixtures.py``) replayed through pytest-httpx;
the few derived payloads override single fields of a recording.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from pm_trader.db import Database
from pm_trader.engine import Engine, _maker_fee, _VenueRouter
from pm_trader.kalshi_api import KALSHI_BASE, KalshiClient, _fee_schedule
from pm_trader.models import (
    ApiError,
    Market,
    OrderBook,
    OrderBookLevel,
    OrderRejectedError,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


MARKET = _fixture("kalshi_market.json")["market"]
TICKER = MARKET["ticker"]
SLUG = f"kalshi:{TICKER}"
EVENT = _fixture("kalshi_event.json")
SERIES = _fixture("kalshi_series.json")
BOOK = _fixture("kalshi_orderbook.json")
SETTLED = _fixture("kalshi_market_settled.json")["market"]


def _url(path: str) -> re.Pattern:
    return re.compile(re.escape(f"{KALSHI_BASE}{path}") + r"(\?.*)?$")


def _serve(httpx_mock, market: dict = MARKET, series: dict = SERIES) -> None:
    """Serve one market, its event, its series and its book, reusably."""
    ticker = market["ticker"]
    for path, payload in (
        (f"/markets/{ticker}/orderbook", BOOK),
        (f"/markets/{ticker}", {"market": market}),
        (f"/events/{market['event_ticker']}", EVENT),
        (f"/series/{EVENT['event']['series_ticker']}", series),
    ):
        httpx_mock.add_response(
            url=_url(path), json=payload, is_reusable=True, is_optional=True
        )


@pytest.fixture
def engine(tmp_path: Path):
    eng = Engine(tmp_path / "acct")
    eng.init_account(1_000.0)
    yield eng
    eng.close()


@pytest.fixture
def client(tmp_path: Path):
    db = Database(tmp_path / "client")
    db.init_schema()
    c = KalshiClient(db)
    yield c
    c.close()
    db.close()


# Recorded ladders (ascending on the wire):
#   yes bids [[0.57, 482], [0.58, 21]]   no bids [[0.38, 470.52], [0.40, 521]]
# YES asks = 1 - no bids -> 0.60 x 521, 0.62 x 470.52
# NO asks  = 1 - yes bids -> 0.42 x 21, 0.43 x 482


# ---------------------------------------------------------------------------
# Client: fees, sides, resolution
# ---------------------------------------------------------------------------


class TestFeeSchedule:
    def test_recorded_series_charges_takers_and_makers(self) -> None:
        assert SERIES["series"]["fee_type"] == "quadratic_with_maker_fees"
        assert _fee_schedule(SERIES["series"]) == {
            "rate": pytest.approx(0.07),
            "exponent": 1,
            "takerOnly": False,
            "makerRate": pytest.approx(0.0175),
        }

    def test_quadratic_is_taker_only_and_scales_by_multiplier(self) -> None:
        series = {**SERIES["series"], "fee_type": "quadratic", "fee_multiplier": 0.5}
        assert _fee_schedule(series) == {
            "rate": pytest.approx(0.035), "exponent": 1, "takerOnly": True,
        }

    def test_unmodelled_fee_type_has_no_schedule(self) -> None:
        assert _fee_schedule({**SERIES["series"], "fee_type": "flat"}) is None

    def test_tradable_market_carries_the_series_schedule(self, client, httpx_mock) -> None:
        _serve(httpx_mock)

        market = client.get_tradable_market(SLUG)

        assert market.slug == SLUG
        assert market.fee_schedule["rate"] == pytest.approx(0.07)
        assert market.fee_schedule["makerRate"] == pytest.approx(0.0175)

    def test_event_and_series_are_cached(self, client, httpx_mock) -> None:
        _serve(httpx_mock)

        client.get_tradable_market(SLUG)
        client.get_tradable_market(SLUG)

        paths = [r.url.path for r in httpx_mock.get_requests()]
        assert paths.count(f"/trade-api/v2/events/{MARKET['event_ticker']}") == 1
        assert paths.count("/trade-api/v2/series/KXNBAGAME") == 1

    def test_market_without_event_has_no_schedule(self, client, httpx_mock) -> None:
        httpx_mock.add_response(json={"market": {**MARKET, "event_ticker": ""}})

        assert client.get_tradable_market(TICKER).fee_schedule is None

    def test_event_without_series_is_an_api_error(self, client, httpx_mock) -> None:
        httpx_mock.add_response(url=_url(f"/markets/{TICKER}"), json={"market": MARKET})
        httpx_mock.add_response(
            url=_url(f"/events/{MARKET['event_ticker']}"),
            json={"event": {**EVENT["event"], "series_ticker": ""}},
        )

        with pytest.raises(ApiError, match="no series for event"):
            client.get_tradable_market(TICKER)

    def test_missing_series_payload_is_an_api_error(self, client, httpx_mock) -> None:
        _serve(httpx_mock, series={"error": "gone"})

        with pytest.raises(ApiError, match="no series KXNBAGAME"):
            client.get_tradable_market(TICKER)

    def test_fee_rate_refuses_unmodelled_fee_types(self, client) -> None:
        with pytest.raises(OrderRejectedError, match="fee type"):
            client.get_fee_rate(SLUG)


class TestSides:
    def test_no_book_is_the_inverted_yes_book(self, client, httpx_mock) -> None:
        httpx_mock.add_response(json=BOOK)

        book = client.get_order_book(f"{SLUG}:no")

        assert [(l.price, l.size) for l in book.asks] == [(0.42, 21.0), (0.43, 482.0)]
        assert [(l.price, l.size) for l in book.bids] == [(0.40, 521.0), (0.38, 470.52)]
        assert httpx_mock.get_requests()[0].url.path.endswith(f"/markets/{TICKER}/orderbook")

    def test_midpoint_of_each_side(self, client, httpx_mock) -> None:
        httpx_mock.add_response(json=BOOK, is_reusable=True)

        assert client.get_midpoint(SLUG) == pytest.approx((0.58 + 0.60) / 2)
        assert client.get_midpoint(f"{SLUG}:no") == pytest.approx((0.40 + 0.42) / 2)

    def test_midpoint_with_one_or_no_side(self, client, httpx_mock) -> None:
        one_sided = {"orderbook_fp": {**BOOK["orderbook_fp"], "no_dollars": []}}
        httpx_mock.add_response(json=one_sided)
        httpx_mock.add_response(json={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})

        assert client.get_midpoint(SLUG) == pytest.approx(0.58)
        assert client.get_midpoint(SLUG) == 0.0

    def test_tick_size_for_either_token(self, client, httpx_mock) -> None:
        httpx_mock.add_response(json={"market": MARKET})

        assert client.get_tick_size(SLUG) == pytest.approx(0.01)
        assert client.get_tick_size(f"{SLUG}:no") == pytest.approx(0.01)  # cached


class TestResolutionStatus:
    def test_recorded_settled_market_is_resolved(self, client, httpx_mock) -> None:
        httpx_mock.add_response(json={"market": SETTLED})

        market = client.get_market(SETTLED["ticker"])

        assert SETTLED["status"] == "finalized" and SETTLED["result"] == "yes"
        assert market.uma_resolution_status == "resolved"
        assert market.outcome_prices == [1.0, 0.0]

    def test_active_and_disputed_markets_are_unresolved(self, client, httpx_mock) -> None:
        httpx_mock.add_response(json={"market": MARKET})
        httpx_mock.add_response(json={"market": SETTLED | {"status": "disputed"}})

        assert client.get_market(MARKET["ticker"]).uma_resolution_status == "unresolved"
        assert client.get_market(SETTLED["ticker"]).uma_resolution_status == "unresolved"


# ---------------------------------------------------------------------------
# Engine: end-to-end paper trades
# ---------------------------------------------------------------------------


class TestEngineTrading:
    def test_buy_yes_walks_the_derived_asks_and_charges_the_kalshi_fee(
        self, engine, httpx_mock,
    ) -> None:
        _serve(httpx_mock)

        result = engine.buy(SLUG, "yes", 30.0)

        shares = 30.0 / 0.60
        assert result.trade.avg_price == pytest.approx(0.60)
        assert result.trade.shares == pytest.approx(shares)
        assert result.trade.fee == pytest.approx(shares * 0.07 * 0.60 * 0.40, abs=1e-5)
        assert result.trade.market_slug == SLUG
        assert engine.get_account().cash == pytest.approx(1_000.0 - 30.0 - result.trade.fee)

    def test_buy_no_walks_the_inverted_yes_bids(self, engine, httpx_mock) -> None:
        _serve(httpx_mock)

        result = engine.buy(SLUG, "no", 20.0, order_type="fak")

        # 21 contracts at 0.42 = $8.82, the rest at 0.43
        expected_shares = 21.0 + (20.0 - 21.0 * 0.42) / 0.43
        assert result.trade.shares == pytest.approx(expected_shares)
        assert result.trade.outcome == "no"

    def test_sell_and_portfolio_use_the_kalshi_book(self, engine, httpx_mock) -> None:
        _serve(httpx_mock)
        bought = engine.buy(SLUG, "yes", 6.0)

        portfolio = engine.get_portfolio()
        sold = engine.sell(SLUG, "yes", bought.trade.shares)

        assert portfolio[0]["live_price"] == pytest.approx((0.58 + 0.60) / 2)
        assert sold.trade.avg_price == pytest.approx(0.58)
        assert engine.get_portfolio() == []

    def test_resolve_pays_the_determined_side(self, engine, httpx_mock) -> None:
        _serve(httpx_mock)
        bought = engine.buy(SLUG, "yes", 6.0)
        cash_after_buy = engine.get_account().cash
        engine.db.conn.execute("DELETE FROM market_cache")
        httpx_mock.add_response(
            url=_url(f"/markets/{TICKER}"),
            json={"market": MARKET | {
                "status": "finalized", "result": "yes",
                "settlement_value_dollars": "1.0000",
            }},
        )

        results = engine.resolve_market(SLUG)

        assert results[0].payout == pytest.approx(bought.trade.shares)
        assert engine.get_account().cash == pytest.approx(cash_after_buy + bought.trade.shares)

    def test_unmodelled_fee_type_refuses_the_trade(self, engine, httpx_mock) -> None:
        _serve(httpx_mock, series={"series": {**SERIES["series"], "fee_type": "flat"}})

        with pytest.raises(OrderRejectedError, match="fee type"):
            engine.buy(SLUG, "yes", 6.0)


# ---------------------------------------------------------------------------
# Engine helpers
# ---------------------------------------------------------------------------


def _market(schedule: dict | None) -> Market:
    return Market(
        condition_id="c", slug="s", question="q", description="", outcomes=["Yes", "No"],
        outcome_prices=[0.5, 0.5], tokens=[], active=True, closed=False,
        fee_schedule=schedule,
    )


class TestMakerFee:
    def test_no_schedule_pays_the_taker_fee(self) -> None:
        assert _maker_fee(_market(None), 0.8) == 0.8

    def test_taker_only_pays_nothing(self) -> None:
        assert _maker_fee(_market({"rate": 0.07, "takerOnly": True}), 0.8) == 0.0

    def test_maker_rate_scales_the_fee(self) -> None:
        schedule = {"rate": 0.07, "takerOnly": False, "makerRate": 0.0175}
        assert _maker_fee(_market(schedule), 0.8) == pytest.approx(0.2)

    def test_schedule_without_maker_rate_pays_the_taker_fee(self) -> None:
        assert _maker_fee(_market({"rate": 0.07}), 0.8) == 0.8
        assert _maker_fee(_market({"rate": 0, "makerRate": 0.01}), 0.8) == 0.8


class TestVenueRouter:
    def test_polymarket_calls_and_other_attributes_pass_through(self, tmp_path) -> None:
        polymarket = MagicMock()
        router = _VenueRouter(polymarket, MagicMock())

        router.get_market("will-it-rain")
        router.get_order_book("123")
        router.get_midpoint("123")
        router.get_fee_rate("123")
        router.get_tick_size("123")
        router.search_markets("rain")
        router.close()

        polymarket.get_market.assert_called_once_with("will-it-rain")
        polymarket.search_markets.assert_called_once_with("rain")
        polymarket.close.assert_called_once_with()
        assert router._kalshi is None

    def test_kalshi_client_is_created_once_and_closed(self, tmp_path, httpx_mock) -> None:
        db = Database(tmp_path / "r")
        db.init_schema()
        router = _VenueRouter(MagicMock(), db)
        httpx_mock.add_response(json=BOOK, is_reusable=True)

        router.get_order_book(SLUG)
        first = router._kalshi
        router.get_midpoint(SLUG)

        assert router._kalshi is first
        router.close()
        db.close()
        assert first._http.is_closed
