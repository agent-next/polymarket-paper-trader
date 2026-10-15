"""Tests for the read-only Kalshi CLI commands and MCP tools.

Responses are the recorded live Kalshi payloads in ``tests/fixtures``,
replayed through pytest-httpx, so the wire shape stays the oracle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import click.testing
import pytest

from pm_trader import mcp_server
from pm_trader.cli import main
from pm_trader.mcp_server import kalshi_book, kalshi_market, kalshi_markets

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TICKER = "KXNBAGAME-26OCT16DENLAL-LAL"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _cli(tmp_path: Path, args: list[str]) -> tuple[int, dict]:
    result = click.testing.CliRunner().invoke(
        main, ["--data-dir", str(tmp_path / "cli")] + args
    )
    return result.exit_code, json.loads(result.output)


@pytest.fixture(autouse=True)
def fresh_engine(tmp_path: Path):
    mcp_server._engine = None
    with patch.object(Path, "home", return_value=tmp_path):
        yield
    if mcp_server._engine is not None:
        mcp_server._engine.close()
        mcp_server._engine = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_markets_passes_filters_and_returns_markets(self, tmp_path, httpx_mock) -> None:
        page = _fixture("kalshi_markets_page1.json")
        httpx_mock.add_response(json={**page, "cursor": ""})

        code, data = _cli(tmp_path, [
            "kalshi", "markets", "--limit", "2", "--status", "settled",
            "--series", "KXNBAGAME", "--event", "KXNBAGAME-26OCT16DENLAL",
        ])

        assert code == 0
        assert [m["slug"] for m in data["data"]] == [m["ticker"] for m in page["markets"]]
        params = httpx_mock.get_requests()[0].url.params
        assert params["status"] == "settled"
        assert params["series_ticker"] == "KXNBAGAME"
        assert params["event_ticker"] == "KXNBAGAME-26OCT16DENLAL"
        assert params["limit"] == "2"

    def test_markets_limit_above_cap_is_rejected(self, tmp_path) -> None:
        result = click.testing.CliRunner().invoke(
            main, ["--data-dir", str(tmp_path), "kalshi", "markets", "--limit", "101"]
        )
        assert result.exit_code == 2

    def test_market(self, tmp_path, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture("kalshi_market.json"))

        code, data = _cli(tmp_path, ["kalshi", "market", TICKER])

        assert code == 0
        assert data["data"]["slug"] == TICKER
        assert data["data"]["question"] == "Los Angeles L wins"
        assert data["data"]["outcome_prices"] == pytest.approx([0.59, 0.41])

    def test_market_not_found(self, tmp_path, httpx_mock) -> None:
        httpx_mock.add_response(status_code=404, json={"error": "not found"})

        code, data = _cli(tmp_path, ["kalshi", "market", "KXNOPE"])

        assert code == 1
        assert data["ok"] is False
        assert data["code"] == "MARKET_NOT_FOUND"

    def test_market_rejects_a_non_ticker_without_a_request(self, tmp_path, httpx_mock) -> None:
        code, data = _cli(tmp_path, ["kalshi", "market", "../series/X"])

        assert code == 1
        assert data["code"] == "MARKET_NOT_FOUND"
        assert httpx_mock.get_requests() == []

    def test_book_is_best_first_and_trimmed(self, tmp_path, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture("kalshi_orderbook.json"))

        code, data = _cli(tmp_path, ["kalshi", "book", TICKER, "--depth", "1"])

        assert code == 0
        assert data["data"]["ticker"] == TICKER
        # yes ladder [[0.57, 482], [0.58, 21]] -> best bid 0.58
        assert data["data"]["bids"] == [{"price": pytest.approx(0.58), "size": 21.0}]
        # no ladder [[0.38, 470.52], [0.40, 521]] -> best yes ask 1 - 0.40
        assert data["data"]["asks"] == [{"price": pytest.approx(0.60), "size": 521.0}]

    def test_book_full_depth(self, tmp_path, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture("kalshi_orderbook.json"))

        code, data = _cli(tmp_path, ["kalshi", "book", TICKER])

        assert code == 0
        assert [b["price"] for b in data["data"]["bids"]] == pytest.approx([0.58, 0.57])
        assert [a["price"] for a in data["data"]["asks"]] == pytest.approx([0.60, 0.62])

    def test_markets_api_error(self, tmp_path, httpx_mock) -> None:
        httpx_mock.add_response(status_code=500)

        code, data = _cli(tmp_path, ["kalshi", "markets"])

        assert code == 1
        assert data["code"] == "API_ERROR"

    def test_book_api_error(self, tmp_path, httpx_mock) -> None:
        httpx_mock.add_response(status_code=500)

        code, data = _cli(tmp_path, ["kalshi", "book", TICKER])

        assert code == 1
        assert data["code"] == "API_ERROR"


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


class TestMcpTools:
    def test_markets(self, httpx_mock) -> None:
        page = _fixture("kalshi_markets_page1.json")
        httpx_mock.add_response(json={**page, "cursor": ""})

        data = json.loads(kalshi_markets(limit=500, series_ticker="KXNBAGAME"))

        assert data["ok"] is True
        assert [m["slug"] for m in data["data"]] == [m["ticker"] for m in page["markets"]]
        params = httpx_mock.get_requests()[0].url.params
        assert params["status"] == "open"
        assert params["series_ticker"] == "KXNBAGAME"
        assert params["limit"] == "100"  # limit clamped to MAX_RESULTS

    def test_markets_bad_status_makes_no_request(self, httpx_mock) -> None:
        data = json.loads(kalshi_markets(status="bogus"))

        assert data == {
            "ok": False,
            "error": "status must be one of open, closed, settled",
            "code": "invalid_status",
        }
        assert httpx_mock.get_requests() == []

    def test_markets_api_error(self, httpx_mock) -> None:
        httpx_mock.add_response(status_code=500)

        data = json.loads(kalshi_markets())

        assert data["ok"] is False
        assert data["code"] == "API_ERROR"

    def test_market(self, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture("kalshi_market.json"))

        data = json.loads(kalshi_market(TICKER))

        assert data["ok"] is True
        assert data["data"]["slug"] == TICKER
        assert data["data"]["active"] is True

    def test_market_not_found(self, httpx_mock) -> None:
        httpx_mock.add_response(status_code=404, json={"error": "not found"})

        data = json.loads(kalshi_market("KXNOPE"))

        assert data["ok"] is False
        assert data["code"] == "market_not_found"

    def test_book(self, httpx_mock) -> None:
        httpx_mock.add_response(json=_fixture("kalshi_orderbook.json"))

        data = json.loads(kalshi_book(TICKER, depth=1))

        assert data["ok"] is True
        assert data["data"]["bids"] == [{"price": pytest.approx(0.58), "size": 21.0}]
        assert data["data"]["asks"] == [{"price": pytest.approx(0.60), "size": 521.0}]

    def test_book_error(self, httpx_mock) -> None:
        httpx_mock.add_response(status_code=500)

        data = json.loads(kalshi_book(TICKER))

        assert data["ok"] is False
        assert data["code"] == "order_book_error"

    def test_tools_are_read_only_live(self) -> None:
        import anyio

        tools = {t.name: t for t in anyio.run(mcp_server.mcp.list_tools)}
        for name in ("kalshi_markets", "kalshi_market", "kalshi_book"):
            ann = tools[name].annotations
            assert ann.read_only_hint is True
            assert ann.destructive_hint is False
            assert ann.open_world_hint is True
