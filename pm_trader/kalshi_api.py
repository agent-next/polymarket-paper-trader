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
    OrderRejectedError,
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

# Kalshi markets live in the engine under this prefix: the slug is
# ``kalshi:<ticker>``, the YES token is the slug, the NO token is ``<slug>:no``.
# The prefix is what routes a trade to this client instead of Polymarket.
PREFIX = "kalshi:"
_NO_SUFFIX = ":no"

# Official schedule: taker fee = M x 0.07 x C x P x (1-P); maker fee on the
# series that charge one = M x 0.0175 x C x P x (1-P) (docs/kalshi.md "Fees").
_TAKER_RATE = 0.07
_MAKER_RATE = 0.0175
_TAKER_ONLY_FEE_TYPES = frozenset({"quadratic"})
_MAKER_FEE_TYPES = frozenset(
    {"quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"}
)

# A market is settled for the engine only once Kalshi has determined it
# (`disputed` can still change) and the result names a binary side.
_DETERMINED_STATUSES = frozenset({"determined", "amended", "finalized"})

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
        """Resolve a market ticker (or ``kalshi:<ticker>``) to a Market.

        Cached 5 minutes.  A 404 is a missing market
        (:class:`MarketNotFoundError`); every other failure is an
        :class:`ApiError`.  The returned market carries no fee schedule; the
        trading path uses :meth:`get_tradable_market`.
        """
        return _parse_market(self._get_market_payload(ticker))

    def get_tradable_market(self, ticker: str) -> Market:
        """:meth:`get_market` plus the series fee schedule the engine charges.

        The fee lives on the series, reached through the market's event, so a
        trade costs two extra metadata calls (both cached 5 minutes).  A series
        whose ``fee_type`` is not a published quadratic curve gets no schedule,
        and :meth:`get_fee_rate` then refuses the trade.
        """
        payload = self._get_market_payload(ticker)
        market_type = payload.get("market_type") or "binary"
        if market_type != "binary":
            # The engine pays $1 to a YES or NO winner; a scalar payout could
            # never resolve, so such a market must not be traded at all.
            raise OrderRejectedError(
                f"Kalshi {market_type} market '{ticker}' is not tradable (binary only)"
            )
        market = _parse_market(payload)
        event_ticker = payload.get("event_ticker")
        if event_ticker:
            series = self._get_series(self._get_series_ticker(str(event_ticker)))
            market.fee_schedule = _fee_schedule(series)
        return market

    def _get_market_payload(self, ticker: str) -> dict:
        ticker = _strip_prefix(ticker)
        _check_ticker(ticker)
        cache_key = f"kalshi:market:{ticker}"
        cached = self._get_cached(cache_key)
        if isinstance(cached, dict):
            return cached

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
        return payload

    def _get_series_ticker(self, event_ticker: str) -> str:
        cache_key = f"kalshi:event:{event_ticker}"
        cached = self._get_cached(cache_key)
        if not isinstance(cached, dict):
            data = self._get(f"/events/{event_ticker}")
            cached = data.get("event") if isinstance(data, dict) else None
            if not isinstance(cached, dict) or not cached.get("series_ticker"):
                raise ApiError(f"Kalshi API returned no series for event {event_ticker}")
            self._set_cached(cache_key, cached)
        return str(cached["series_ticker"])

    def _get_series(self, series_ticker: str) -> dict:
        cache_key = f"kalshi:series:{series_ticker}"
        cached = self._get_cached(cache_key)
        if isinstance(cached, dict):
            return cached
        data = self._get(f"/series/{series_ticker}")
        series = data.get("series") if isinstance(data, dict) else None
        if not isinstance(series, dict):
            raise ApiError(f"Kalshi API returned no series {series_ticker}")
        self._set_cached(cache_key, series)
        return series

    def get_order_book(self, ticker: str, *, depth: int | None = None) -> OrderBook:
        """Fetch the live order book for a market ticker.  NEVER cached.

        The wire carries bids only, so the yes asks are derived from the no
        ladder (a no bid at ``p`` is a yes ask at ``1 - p``).  ``depth`` is
        passed through; omitted, Kalshi returns every level.
        """
        ticker = _strip_prefix(ticker)
        no_side = ticker.endswith(_NO_SUFFIX)
        if no_side:
            ticker = ticker[: -len(_NO_SUFFIX)]
        _check_ticker(ticker)
        params = {"depth": depth} if depth else None
        book = _parse_order_book(self._get(f"/markets/{ticker}/orderbook", params))
        return _no_side(book) if no_side else book

    # ------------------------------------------------------------------
    # Engine trading interface (same methods the engine calls on
    # PolymarketClient; tokens are the ``kalshi:`` tokens of a Market)
    # ------------------------------------------------------------------

    def get_midpoint(self, token_id: str) -> float:
        """Midpoint of the token's live book (one side if the other is empty).  NEVER cached."""
        book = self.get_order_book(token_id)
        prices = [levels[0].price for levels in (book.bids, book.asks) if levels]
        return round(sum(prices) / len(prices), _PRICE_DP) if prices else 0.0

    def get_fee_rate(self, token_id: str) -> int:
        """Only reached when a market has no usable fee schedule: refuse to guess."""
        raise OrderRejectedError(
            f"Kalshi market '{token_id}' has a fee type pm-trader does not model"
        )

    def get_tick_size(self, token_id: str, price: float | None = None) -> float:
        """The price grid step at ``price`` (cached with the market metadata).

        ``price_ranges`` bands are in YES terms, so a NO price ``p`` is looked
        up at ``1 - p``.  Without a price, or outside every band, the finest
        step of the market.
        """
        ticker = _strip_prefix(token_id)
        no_side = ticker.endswith(_NO_SUFFIX)
        if no_side:
            ticker = ticker[: -len(_NO_SUFFIX)]
        payload = self._get_market_payload(ticker)
        if price is not None:
            step = _band_step(payload, 1.0 - price if no_side else price)
            if step > 0.0:
                return step
        return _tick_size(payload)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _strip_prefix(ticker: str) -> str:
    return ticker[len(PREFIX):] if isinstance(ticker, str) and ticker.startswith(PREFIX) else ticker


def _fee_schedule(series: dict) -> dict | None:
    """Map a series' ``fee_type`` / ``fee_multiplier`` onto the engine's
    ``feeSchedule`` shape (``rate x C x p x (1-p)``); None if not modelled."""
    fee_type = series.get("fee_type")
    multiplier = _to_float(series.get("fee_multiplier"), 1.0)
    if fee_type in _TAKER_ONLY_FEE_TYPES:
        return {"rate": _TAKER_RATE * multiplier, "exponent": 1, "takerOnly": True}
    if fee_type in _MAKER_FEE_TYPES:
        return {
            "rate": _TAKER_RATE * multiplier,
            "exponent": 1,
            "takerOnly": False,
            "makerRate": _MAKER_RATE * multiplier,
        }
    return None


def _no_side(book: OrderBook) -> OrderBook:
    """The NO book from the YES book: a YES ask at p is a NO bid at 1 - p."""
    return OrderBook(
        bids=[OrderBookLevel(round(1.0 - l.price, _PRICE_DP), l.size) for l in book.asks],
        asks=[OrderBookLevel(round(1.0 - l.price, _PRICE_DP), l.size) for l in book.bids],
    )


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


def _band_step(data: dict, price: float) -> float:
    """Step of the ``price_ranges`` band containing ``price`` (0.0 if none)."""
    for band in data.get("price_ranges") or []:
        if not isinstance(band, dict):
            continue
        start, end = _to_float(band.get("start")), _to_float(band.get("end"))
        if start <= price <= end:
            return _to_float(band.get("step"))
    return 0.0


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

    result = str(data.get("result") or "")
    resolved = status in _DETERMINED_STATUSES and result in ("yes", "no")

    return Market(
        condition_id=ticker,
        slug=f"{PREFIX}{ticker}",
        question=str(data.get("title") or ""),
        description=str(data.get("rules_primary") or ""),
        outcomes=["Yes", "No"],
        outcome_prices=[yes_price, round(1.0 - yes_price, _PRICE_DP)],
        tokens=[
            {"token_id": f"{PREFIX}{ticker}", "outcome": "Yes"},
            {"token_id": f"{PREFIX}{ticker}{_NO_SUFFIX}", "outcome": "No"},
        ],
        active=status == "active",
        closed=status in _CLOSED_STATUSES,
        accepting_orders=status == "active",
        volume=_to_float(data.get("volume_fp")),
        end_date=str(data.get("close_time") or ""),
        tick_size=_tick_size(data),
        uma_resolution_status="resolved" if resolved else "unresolved",
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
