"""Tests for the examples/kalshi_favorite.py strategy.

Every Kalshi response is a recording of the live public API
(``tests/fixtures/capture_kalshi_fixtures.py``) replayed through pytest-httpx;
the per-market order books are derived from each recording's own quote so
every fixture market prices differently. Nothing here touches the live API.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import examples.kalshi_favorite as kalshi_favorite
from pm_trader.engine import Engine
from pm_trader.kalshi_api import KALSHI_BASE

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


PAGE1 = _fixture("kalshi_markets_page1.json")
PAGE2 = _fixture("kalshi_markets_page2.json")
ALL_MARKETS = PAGE1["markets"] + PAGE2["markets"]
EVENT = _fixture("kalshi_event.json")
SERIES = _fixture("kalshi_series.json")
BOOK = _fixture("kalshi_orderbook.json")


def _url(path: str) -> re.Pattern:
    return re.compile(re.escape(f"{KALSHI_BASE}{path}") + r"(\?.*)?$")


def _list_url(cursor: str | None) -> re.Pattern:
    """The /markets list call: page 1 has no cursor, page 2 repeats page 1's."""
    base = re.escape(f"{KALSHI_BASE}/markets")
    if cursor is None:
        return re.compile(base + r"\?(?!.*\bcursor=)")
    return re.compile(base + r"\?.*\bcursor=" + re.escape(cursor))


def _mid(market: dict) -> float:
    """The market's recorded quote midpoint — the oracle for the price band."""
    return (float(market["yes_bid_dollars"]) + float(market["yes_ask_dollars"])) / 2


def _book_for(market: dict) -> dict:
    """A two-level book centered on the market's own recorded quote: best YES
    bid/ask one cent either side of its midpoint, sizes from the recorded
    ladder, so every fixture market quotes a different price."""
    mid = _mid(market)
    yes, no = BOOK["orderbook_fp"]["yes_dollars"], BOOK["orderbook_fp"]["no_dollars"]
    return {"orderbook_fp": {
        "yes_dollars": [
            [f"{mid - 0.02:.4f}", yes[0][1]],
            [f"{mid - 0.01:.4f}", yes[1][1]],
        ],
        "no_dollars": [
            [f"{round(1 - mid - 0.02, 4):.4f}", no[0][1]],
            [f"{round(1 - mid - 0.01, 4):.4f}", no[1][1]],
        ],
    }}


def _serve(httpx_mock, series: dict = SERIES) -> None:
    """Serve the recorded series walk and every market's own data."""
    httpx_mock.add_response(url=_list_url(None), json=PAGE1)
    httpx_mock.add_response(url=_list_url(PAGE1["cursor"]), json=PAGE2)
    # Page 2's recorded cursor is non-empty (the walk continued live), so the
    # client asks once more; the recorded empty page ends it.
    httpx_mock.add_response(
        url=_list_url(PAGE2["cursor"]),
        json=_fixture("kalshi_markets_empty.json"),
    )
    series_ticker = EVENT["event"]["series_ticker"]
    for market in ALL_MARKETS:
        for path, payload in (
            (f"/markets/{market['ticker']}/orderbook", _book_for(market)),
            (f"/markets/{market['ticker']}", {"market": market}),
            (f"/events/{market['event_ticker']}", EVENT),
        ):
            httpx_mock.add_response(
                url=_url(path), json=payload, is_reusable=True, is_optional=True
            )
    httpx_mock.add_response(
        url=_url(f"/series/{series_ticker}"), json=series,
        is_reusable=True, is_optional=True,
    )


@pytest.fixture
def engine(tmp_path: Path):
    eng = Engine(tmp_path / "acct")
    eng.init_account(1_000.0)
    yield eng
    eng.close()


class TestKalshiFavorite:
    def test_buys_exactly_the_in_band_favorites(self, engine, httpx_mock) -> None:
        _serve(httpx_mock)

        kalshi_favorite.run(engine)

        # Oracle: the band test on the fixtures' own recorded quotes, never
        # on the strategy's constants.
        expected = [m for m in ALL_MARKETS if 0.60 <= _mid(m) <= 0.85]
        out_of_band = [m for m in ALL_MARKETS if m not in expected]
        assert expected and out_of_band

        trades = engine.get_history()
        assert [t.market_slug for t in trades] == [
            f"kalshi:{m['ticker']}" for m in expected
        ]
        assert all(t.outcome == "yes" and t.side == "buy" for t in trades)
        for market in expected:
            pos = engine.db.get_position(market["ticker"], "yes")
            assert pos is not None and pos.shares > 0
        for market in out_of_band:
            for outcome in ("yes", "no"):
                assert engine.db.get_position(market["ticker"], outcome) is None

        # The scan really listed the one open series the config names.
        listing = httpx_mock.get_requests()[0]
        assert listing.url.params["series_ticker"] == kalshi_favorite.SERIES
        assert listing.url.params["status"] == "open"

    def test_position_cap_stops_the_scan(self, engine, httpx_mock, monkeypatch) -> None:
        _serve(httpx_mock)
        monkeypatch.setattr(kalshi_favorite, "MAX_POSITIONS", 0)

        kalshi_favorite.run(engine)

        assert engine.get_history() == []
        assert engine.db.get_open_positions() == []
        assert not any(
            "/orderbook" in r.url.path for r in httpx_mock.get_requests()
        )

    def test_rejected_buy_is_skipped(self, engine, httpx_mock) -> None:
        # A fee type the engine refuses to model: every buy raises
        # OrderRejectedError and the strategy must skip it, not crash.
        flat = {"series": {**SERIES["series"], "fee_type": "flat"}}
        _serve(httpx_mock, series=flat)

        kalshi_favorite.run(engine)

        assert engine.get_history() == []
        assert engine.db.get_open_positions() == []

    def test_already_held_market_is_skipped(self, engine, httpx_mock) -> None:
        _serve(httpx_mock)
        held = next(m for m in ALL_MARKETS if 0.60 <= _mid(m) <= 0.85)
        engine.buy(f"kalshi:{held['ticker']}", "yes", 5.0)

        kalshi_favorite.run(engine)

        # Only the pre-existing trade — the strategy did not double up.
        assert len(engine.get_history()) == 1
        # Best ask is one cent above the recorded midpoint: 5 / 0.62 shares.
        assert engine.db.get_position(
            held["ticker"], "yes"
        ).shares == pytest.approx(5.0 / 0.62)
