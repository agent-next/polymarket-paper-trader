"""Record the public Kalshi responses replayed by ``tests/test_kalshi_api.py``.

Run from the repo root:

    python3 tests/fixtures/capture_kalshi_fixtures.py
    python3 tests/fixtures/capture_kalshi_fixtures.py --trading-only  # event/series/settled only

Every JSON file next to this script is a verbatim response from the public,
unauthenticated Kalshi REST API.  The tests replay those files through
pytest-httpx, so the suite never needs the network and never invents a payload.

The base URL is the host ``docs.kalshi.com`` recommends for external API
traders (https://docs.kalshi.com/getting_started/api_environments).  Re-record
before touching the client: a stale fixture proves the parser against yesterday's
wire, not today's.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"

HERE = Path(__file__).resolve().parent
TIMEOUT = httpx.Timeout(20.0)

PAGE_SIZE = 2
SERIES_TICKER = "KXNBAGAME"
EMPTY_EVENT_TICKER = "KXNOTAREALEVENT"
ORDERBOOK_DEPTH = 2


def _get(client: httpx.Client, path: str, params: dict | None = None) -> Any:
    """GET a public Kalshi path and return the decoded body."""
    resp = client.get(f"{BASE}{path}", params=params)
    resp.raise_for_status()
    return resp.json()


def _write(name: str, payload: Any) -> Any:
    """Write a recorded response next to this script, pretty-printed."""
    path = HERE / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {name} ({path.stat().st_size} bytes)")
    return payload


def _record_trading(client: httpx.Client, market: dict) -> None:
    """Record what paper trading needs for ``market``: its event (for the
    series ticker), that series (fee type and multiplier), and one settled
    market of the same series (resolution)."""
    event = _write("kalshi_event.json", _get(client, f"/events/{market['event_ticker']}"))
    series_ticker = event["event"]["series_ticker"]
    _write("kalshi_series.json", _get(client, f"/series/{series_ticker}"))
    settled = _get(
        client, "/markets", {"limit": 1, "status": "settled", "series_ticker": series_ticker}
    )
    _write(
        "kalshi_market_settled.json",
        _get(client, f"/markets/{settled['markets'][0]['ticker']}"),
    )


def main() -> int:
    if "--trading-only" in sys.argv:
        # Re-record only the trading fixtures for the already-recorded market.
        market = json.loads((HERE / "kalshi_market.json").read_text(encoding="utf-8"))
        with httpx.Client(timeout=TIMEOUT) as client:
            _record_trading(client, market["market"])
        return 0

    with httpx.Client(timeout=TIMEOUT) as client:
        page1 = _get(
            client,
            "/markets",
            {"limit": PAGE_SIZE, "status": "open", "series_ticker": SERIES_TICKER},
        )
        if not page1.get("markets"):
            print(
                f"no open markets in series {SERIES_TICKER} right now; "
                "pick another series_ticker and re-record"
            )
            return 1
        _write("kalshi_markets_page1.json", page1)
        _write(
            "kalshi_markets_page2.json",
            _get(
                client,
                "/markets",
                {
                    "limit": PAGE_SIZE,
                    "status": "open",
                    "series_ticker": SERIES_TICKER,
                    "cursor": page1["cursor"],
                },
            ),
        )
        _write(
            "kalshi_markets_empty.json",
            _get(
                client,
                "/markets",
                {"limit": PAGE_SIZE, "status": "open", "event_ticker": EMPTY_EVENT_TICKER},
            ),
        )

        ticker = page1["markets"][0]["ticker"]
        _write("kalshi_market.json", _get(client, f"/markets/{ticker}"))
        _write(
            "kalshi_orderbook.json",
            _get(client, f"/markets/{ticker}/orderbook", {"depth": ORDERBOOK_DEPTH}),
        )
        _record_trading(client, _get(client, f"/markets/{ticker}")["market"])
    print(f"ticker: {ticker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
