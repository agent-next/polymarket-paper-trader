"""Kalshi HTTP client for the public read-only market data API.

Read-only and unauthenticated: list markets, fetch one market, fetch its order
book.  ``docs/kalshi.md`` holds the probed contract this follows, the fee and
settlement facts a later trading change will need, and what deliberately does
not map onto the engine's models.

Same conventions as ``api.py``: market metadata is cached in SQLite for
``CACHE_TTL_SECONDS``, order books are NEVER cached, empty responses are never
cached, and every HTTP/transport failure raises a typed ``SimError``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import httpx

from pm_trader.api import CACHE_TTL_SECONDS
from pm_trader.db import Database
from pm_trader.models import (
    ApiError,
    Market,
    MarketNotFoundError,
    OrderBook,
    OrderBookLevel,
)

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

MAX_RESULTS = 100  # cap on list_markets, same bound the market-listing tools use
_PAGE_SIZE = 100  # GET /markets accepts up to 1000; 100 is its documented default
_PRICE_DP = 4  # finest grid any Kalshi market quotes (0.0001)

_TIMEOUT = httpx.Timeout(10.0)

# Kalshi tickers are upper-case letters, digits, '-', '.' and '_'. Anything else is
# refused before it is pasted into a URL path, so a caller-supplied "ticker"
# such as "../series/X" can never reach another endpoint.
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,127}")

# Wire statuses past trading; `initialized`/`inactive` are pre-open, not closed.
_CLOSED_STATUSES = frozenset(
    {"closed", "determined", "disputed", "amended", "finalized"}
)


class KalshiClient:
    """Read-only HTTP client for the public Kalshi REST API."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._http = httpx.Client(timeout=_TIMEOUT)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Cache helpers (mirror api.py; the table is shared, the keys are not)
    # ------------------------------------------------------------------

    def _get_cached(self, key: str) -> object | None:
        """Return a cached value if it exists and is within TTL."""
        row = self.db.conn.execute(
            "SELECT data, fetched_at FROM market_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if not fetched_at.tzinfo:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        if age > CACHE_TTL_SECONDS:
            return None
        return json.loads(row["data"])

    def _set_cached(self, key: str, data: dict | list) -> None:
        self.db.set_cache(key, data)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> object:
        """GET a public Kalshi path, raising ApiError on any failure."""
        try:
            resp = self._http.get(f"{KALSHI_BASE}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise ApiError(
                f"Kalshi API error: {e.response.status_code} {e.response.text[:200]}",
                status_code=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise ApiError(f"Kalshi API request failed: {e}") from e

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def list_markets(
        self,
        *,
        limit: int = 20,
        status: str | None = "open",
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        tickers: str | None = None,
    ) -> list[Market]:
        """List markets, following cursors until ``limit`` markets are collected.

        ``limit`` is capped at :data:`MAX_RESULTS` and pages hold 100 markets.
        The collected markets are metadata, so they are cached for 5 minutes
        and a repeated identical call is served from there; an empty result is
        never cached.
        """
        if limit <= 0:
            return []
        cap = min(limit, MAX_RESULTS)

        params: dict = {}
        if status is not None:
            params["status"] = status
        if series_ticker is not None:
            params["series_ticker"] = series_ticker
        if event_ticker is not None:
            params["event_ticker"] = event_ticker
        if tickers is not None:
            params["tickers"] = tickers
        cache_key = _list_cache_key({**params, "limit": cap})

        cached = self._get_cached(cache_key)
        if isinstance(cached, list):
            return _parse_market_list([e for e in cached if isinstance(e, dict)])[:cap]

        collected: dict[str, dict] = {}
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while len(collected) < cap:
            page_params: dict = {
                **params,
                "limit": min(cap - len(collected), _PAGE_SIZE),
            }
            if cursor is not None:
                page_params["cursor"] = cursor
            data = self._get("/markets", page_params)
            entries = data.get("markets") if isinstance(data, dict) else None
            if not isinstance(entries, list) or not entries:
                break
            for entry in entries:
                if isinstance(entry, dict):
                    collected.setdefault(str(entry.get("ticker")), entry)
            cursor = _next_cursor(data)
            # A cursor already followed is never re-followed: the wire answers
            # an unusable cursor with page 1, which would loop forever.
            if cursor is None or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)

        if collected:
            self._set_cached(cache_key, list(collected.values()))
        return _parse_market_list(list(collected.values()))[:cap]

    def get_market(self, ticker: str) -> Market:
        """Resolve a market ticker to a Market.  Cached 5 minutes.

        A 404 is a missing market (:class:`MarketNotFoundError`); every other
        failure is an :class:`ApiError`.
        """
        _check_ticker(ticker)
        cache_key = f"kalshi:market:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return _parse_market(cached)

        try:
            data = self._get(f"/markets/{ticker}")
        except ApiError as e:
            if e.status_code == 404:
                raise MarketNotFoundError(ticker) from e
            raise
        payload = data.get("market") if isinstance(data, dict) else None
        if not isinstance(payload, dict):
            raise ApiError(f"Kalshi API returned no market for {ticker}")
        self._set_cached(cache_key, payload)
        return _parse_market(payload)

    def get_order_book(self, ticker: str, *, depth: int | None = None) -> OrderBook:
        """Fetch the live order book for a market ticker.  NEVER cached.

        The wire carries bids only, so the yes asks are derived from the no
        ladder (a no bid at ``p`` is a yes ask at ``1 - p``).  ``depth`` is
        passed through; omitted, Kalshi returns every level.
        """
        _check_ticker(ticker)
        params = {"depth": depth} if depth else None
        return _parse_order_book(self._get(f"/markets/{ticker}/orderbook", params))


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _check_ticker(ticker: str) -> None:
    """Refuse anything that is not a Kalshi ticker (see `_TICKER`)."""
    if not isinstance(ticker, str) or not _TICKER.fullmatch(ticker):
        raise MarketNotFoundError(str(ticker))


def _to_float(value: object, default: float = 0.0) -> float:
    """Coerce a fixed-point wire value ("0.5800", "304.24") to a float."""
    if value is None or value == "":
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _next_cursor(data: object) -> str | None:
    """Cursor for the next page; Kalshi ends the walk with ``""`` (docs: null)."""
    if not isinstance(data, dict):
        return None
    cursor = data.get("cursor")
    if isinstance(cursor, str) and cursor:
        return cursor
    return None


def _list_cache_key(params: dict) -> str:
    """Cache key for a market-list query: its filters and capped limit, no cursor."""
    return "kalshi:markets:" + json.dumps(params, sort_keys=True)


def _tick_size(data: dict) -> float:
    """Finest step across a market's price_ranges.

    ``price_ranges`` is the source of truth for valid prices; this collapses a
    multi-band grid to the single number ``Market.tick_size`` can hold.  No
    ranges means no grid constraint, which the engine already reads as 0.0.
    """
    steps = [
        _to_float(band.get("step"))
        for band in data.get("price_ranges") or []
        if isinstance(band, dict)
    ]
    steps = [step for step in steps if step > 0.0]
    return min(steps) if steps else 0.0


def _parse_market_list(data: list) -> list[Market]:
    """Parse a `/markets` list into Markets."""
    return [_parse_market(entry) for entry in data]


def _parse_market(data: object) -> Market:
    """Parse a Kalshi market payload into a Market.

    A Kalshi market is one binary contract keyed by its ticker: there are no
    per-outcome token ids, and the yes ask lives in the no ladder (see
    ``docs/kalshi.md`` for the full field mapping and its gaps).
    """
    if not isinstance(data, dict):
        raise ApiError("Kalshi API returned a malformed market payload")
    ticker = data.get("ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ApiError("Kalshi API returned a market payload without a ticker")

    status = str(data.get("status") or "")
    # Determined markets carry the settlement value of the YES side; before
    # that the last traded price is the only yes price the wire publishes.
    settled = data.get("settlement_value_dollars")
    yes_price = _to_float(settled) if settled is not None else _to_float(
        data.get("last_price_dollars")
    )

    return Market(
        condition_id=ticker,
        slug=ticker,
        question=str(data.get("title") or ""),
        description=str(data.get("rules_primary") or ""),
        outcomes=["Yes", "No"],
        outcome_prices=[yes_price, round(1.0 - yes_price, _PRICE_DP)],
        tokens=[
            {"token_id": ticker, "outcome": "Yes"},
            {"token_id": ticker, "outcome": "No"},
        ],
        active=status == "active",
        closed=status in _CLOSED_STATUSES,
        accepting_orders=status == "active",
        volume=_to_float(data.get("volume_fp")),
        end_date=str(data.get("close_time") or ""),
        tick_size=_tick_size(data),
    )


def _levels(book: dict, key: str) -> list[tuple[object, object]]:
    """Validate one ladder: a list of [price_dollars, contracts] pairs."""
    levels = book.get(key)
    if not isinstance(levels, list):
        raise ApiError(f"Kalshi API order book has no {key} ladder")
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) != 2:
            raise ApiError(f"Kalshi API order book has a malformed {key} level")
    return [(level[0], level[1]) for level in levels]


def _parse_order_book(data: object) -> OrderBook:
    """Parse an `orderbook_fp` response into an OrderBook.

    Bids are the yes ladder, best first.  Asks are the no ladder read the
    other way round: a no bid at ``p`` is a yes ask at ``1 - p``, so reversing
    the ascending no ladder yields asks in ascending order.
    """
    book = data.get("orderbook_fp") if isinstance(data, dict) else None
    if not isinstance(book, dict):
        raise ApiError("Kalshi API returned a payload without orderbook_fp")

    bids = [
        OrderBookLevel(price=_to_float(price), size=_to_float(size))
        for price, size in reversed(_levels(book, "yes_dollars"))
    ]
    asks = [
        OrderBookLevel(
            price=round(1.0 - _to_float(price), _PRICE_DP), size=_to_float(size)
        )
        for price, size in reversed(_levels(book, "no_dollars"))
    ]
    return OrderBook(bids=bids, asks=asks)
