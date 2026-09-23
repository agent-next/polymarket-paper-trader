"""Polymarket HTTP client for Gamma and CLOB APIs.

Fetches market data, order books, prices, fees, and tick sizes from
the public Polymarket APIs.  Market metadata is cached in SQLite;
prices and order books are NEVER cached (always live).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx

from pm_trader.db import Database
from pm_trader.models import (
    ApiError,
    Market,
    MarketNotFoundError,
    OrderBook,
    OrderBookLevel,
)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

CACHE_TTL_SECONDS = 300  # 5 minutes for market metadata

_TIMEOUT = httpx.Timeout(10.0)


class PolymarketClient:
    """HTTP client for Polymarket public APIs."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._http = httpx.Client(timeout=_TIMEOUT)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _get_cached(self, key: str) -> dict | list | None:
        """Return cached value if it exists and is within TTL."""
        row = self.db.conn.execute(
            "SELECT data, fetched_at FROM market_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if not fetched_at.tzinfo:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = (now - fetched_at).total_seconds()
        if age > CACHE_TTL_SECONDS:
            return None
        return json.loads(row["data"])

    def _set_cached(self, key: str, data: dict | list) -> None:
        self.db.set_cache(key, data)

    # ------------------------------------------------------------------
    # Gamma API — market discovery
    # ------------------------------------------------------------------

    def _gamma_get(self, path: str, params: dict | None = None) -> list | dict:
        """Make a GET request to the Gamma API."""
        url = f"{GAMMA_BASE}{path}"
        try:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise ApiError(
                f"Gamma API error: {e.response.status_code} {e.response.text[:200]}",
                status_code=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise ApiError(f"Gamma API request failed: {e}") from e

    def _clob_get(self, path: str, params: dict | None = None) -> dict | list:
        """Make a GET request to the CLOB API."""
        url = f"{CLOB_BASE}{path}"
        try:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise ApiError(
                f"CLOB API error: {e.response.status_code} {e.response.text[:200]}",
                status_code=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise ApiError(f"CLOB API request failed: {e}") from e

    # ------------------------------------------------------------------
    # Market resolution (slug or condition_id → Market)
    # ------------------------------------------------------------------

    def get_market(self, slug_or_id: str) -> Market:
        """Resolve a slug or condition_id to a full Market object.

        Market metadata is cached for 5 minutes.  Tries slug first
        (Gamma API), then condition_id (CLOB API).
        """
        cache_key = f"market:{slug_or_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            # A bare CLOB payload may have been cached by the fallback below.
            if (
                isinstance(cached, dict)
                and _clob_condition_id(cached)
                and not _has_condition_id(cached)
            ):
                return _parse_clob_market(cached)
            return _parse_market(cached)

        # Try by slug first (Gamma API).  Gamma defaults `closed` to false,
        # so retry once including closed markets before falling back.
        for params in (
            {"slug": slug_or_id},
            {"slug": slug_or_id, "closed": "true"},
        ):
            data = self._gamma_get("/markets", params=params)
            if isinstance(data, list) and len(data) > 0:
                market_data = data[0]
                self._set_cached(cache_key, market_data)
                return _parse_market(market_data)
            if isinstance(data, dict) and _has_condition_id(data):
                self._set_cached(cache_key, data)
                return _parse_market(data)

        # Try by condition_id via CLOB API (reliable exact match)
        if slug_or_id.startswith("0x"):
            try:
                clob_data = self._clob_get(f"/clob-markets/{slug_or_id}")
                if isinstance(clob_data, dict) and _clob_condition_id(clob_data):
                    # CLOB returns tokens with outcome/token_id — enrich with
                    # Gamma data.  Abbreviated CLOB payloads carry no slug, so
                    # look the market up by condition id in that case.  Gamma
                    # defaults `closed` to false, so a closed market needs the
                    # closed=true retry, exactly as on the slug path above.
                    market = _parse_clob_market(clob_data)
                    enrich_attempts: tuple[dict, ...] = (
                        ({"slug": market.slug},)
                        if market.slug
                        else (
                            {"condition_ids": market.condition_id},
                            {"condition_ids": market.condition_id, "closed": "true"},
                        )
                    )
                    try:
                        for enrich_params in enrich_attempts:
                            gamma_market = _first_gamma_market(
                                self._gamma_get("/markets", params=enrich_params)
                            )
                            if gamma_market is not None:
                                self._set_cached(cache_key, gamma_market)
                                return _parse_market(gamma_market)
                    except Exception:
                        pass
                    # Fall back to CLOB-only data
                    self._set_cached(cache_key, clob_data)
                    return market
            except ApiError:
                pass

        raise MarketNotFoundError(slug_or_id)

    @staticmethod
    def _parse_market_list(data: object) -> list[Market]:
        """Parse a Gamma API response into a list of Markets."""
        if not isinstance(data, list):
            return []
        return [_parse_market(m) for m in data if _has_condition_id(m)]

    def list_markets(
        self, *, limit: int = 20, sort_by: str = "volume"
    ) -> list[Market]:
        """List active markets sorted by volume or liquidity."""
        params: dict = {
            "limit": limit,
            "active": "true",
            "closed": "false",
        }
        if sort_by == "volume":
            params["order"] = "volume"
            params["ascending"] = "false"
        elif sort_by == "liquidity":
            params["order"] = "liquidity"
            params["ascending"] = "false"

        return self._parse_market_list(self._gamma_get("/markets", params=params))

    def search_markets(self, query: str, *, limit: int = 10) -> list[Market]:
        """Search markets by text query (Gamma /public-search)."""
        return _parse_search_results(
            self._gamma_get(
                "/public-search",
                params={"q": query, "limit_per_type": limit},
            )
        )

    def get_tags(self) -> list[dict]:
        """Fetch all market tags/categories from Gamma API.  Cached 5 min."""
        cache_key = "tags:all"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        data = self._gamma_get("/tags")
        if not isinstance(data, list) or len(data) == 0:
            return []
        self._set_cached(cache_key, data)
        return data

    def get_markets_by_tag(
        self, tag_slug: str, *, limit: int = 20, closed: bool = False,
    ) -> list[Market]:
        """Fetch markets filtered by tag slug."""
        params: dict = {
            "tag_slug": tag_slug,
            "limit": limit,
            "closed": str(closed).lower(),
            "active": str(not closed).lower(),
        }
        return self._parse_market_list(self._gamma_get("/markets", params=params))

    def get_event(self, slug: str) -> dict:
        """Fetch event details (group of related markets).  Cached 5 min."""
        cache_key = f"event:{slug}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        data = self._gamma_get(f"/events/slug/{slug}")
        if isinstance(data, dict):
            self._set_cached(cache_key, data)
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # CLOB API — prices, order book, fees, tick size
    # ------------------------------------------------------------------

    def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch the live order book for a token.  NEVER cached."""
        data = self._clob_get("/book", params={"token_id": token_id})
        return _parse_order_book(data)

    def get_midpoint(self, token_id: str) -> float:
        """Fetch the live midpoint price for a token.  NEVER cached."""
        data = self._clob_get("/midpoint", params={"token_id": token_id})
        return float(data.get("mid", 0.0))

    def get_fee_rate(self, token_id: str) -> int:
        """Fetch the fee rate in bps for a token.  Cached 5 min."""
        cache_key = f"fee_rate:{token_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return int(cached.get("base_fee", cached.get("fee_rate_bps", 0)))

        data = self._clob_get("/fee-rate", params={"token_id": token_id})
        fee_bps = int(data.get("base_fee", data.get("fee_rate_bps", 0)))
        self._set_cached(cache_key, {"base_fee": fee_bps})
        return fee_bps

    def get_tick_size(self, token_id: str) -> float:
        """Fetch the tick size for a token.  Cached 5 min."""
        cache_key = f"tick_size:{token_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return float(cached.get("minimum_tick_size", 0.01))

        data = self._clob_get("/tick-size", params={"token_id": token_id})
        tick = float(data.get("minimum_tick_size", 0.01))
        self._set_cached(cache_key, {"minimum_tick_size": tick})
        return tick

    # ------------------------------------------------------------------
    # Convenience: get everything needed for a trade
    # ------------------------------------------------------------------

    def get_trade_context(
        self, slug_or_id: str, outcome: str
    ) -> tuple[Market, OrderBook, int]:
        """Return (Market, OrderBook, fee_rate_bps) for a trade.

        - Market metadata is cached.
        - Order book is always live.
        - Fee rate is cached.
        """
        market = self.get_market(slug_or_id)
        token_id = market.get_token_id(outcome)
        book = self.get_order_book(token_id)
        fee_rate = self.get_fee_rate(token_id)
        return market, book, fee_rate


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _has_condition_id(data: dict) -> bool:
    """Check if a response dict has a condition ID (camelCase or snake_case)."""
    return bool(data.get("conditionId") or data.get("condition_id"))


def _clob_condition_id(data: dict) -> str:
    """Condition id from a CLOB market payload (abbreviated or legacy keys)."""
    return data.get("c") or data.get("condition_id") or ""


def _first_gamma_market(data: object) -> dict | None:
    """First usable market in a Gamma /markets response.

    An empty list, or a list whose entries carry no condition id, holds no
    market data — callers retry (with ``closed=true``) or fall back.
    """
    if not isinstance(data, list):
        return None
    for entry in data:
        if isinstance(entry, dict) and _has_condition_id(entry):
            return entry
    return None


def _clob_fee_schedule(fd: object) -> dict | None:
    """Fee schedule from a CLOB abbreviated ``fd`` block.

    ``fd`` carries the same metadata as Gamma's ``feeSchedule`` in short keys:
    ``r`` (rate), ``e`` (exponent), ``to`` (takerOnly) and, when present,
    ``rr`` (rebate rate).  ``rate`` is a coefficient, not bps.
    """
    if not isinstance(fd, dict):
        return None
    return {
        "rate": float(fd.get("r", 0) or 0),
        "exponent": int(fd.get("e", 0) or 0),
        "takerOnly": bool(fd.get("to", False)),
        "rebateRate": float(fd.get("rr", fd.get("rebateRate", 0)) or 0),
    }


def _parse_clob_market(data: dict) -> Market:
    """Parse a CLOB /clob-markets/{condition_id} response into a Market.

    Handles the current abbreviated payload (``c``, ``t``/``o`` tokens,
    ``mts``/``mos``/``mbf``/``tbf``, ``fd``) as well as the legacy long-key
    shape.
    """
    tokens_raw = data.get("t", data.get("tokens", []))
    if isinstance(tokens_raw, str):
        tokens_raw = json.loads(tokens_raw)

    tokens = []
    for t in tokens_raw:
        tokens.append({
            "token_id": t.get("t") or t.get("token_id", ""),
            "outcome": t.get("o") or t.get("outcome", ""),
        })

    def _to_bool(val) -> bool:
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    return Market(
        condition_id=_clob_condition_id(data),
        slug=data.get("market_slug", ""),
        question=data.get("question", ""),
        description=data.get("description", ""),
        outcomes=[t.get("outcome", "") for t in tokens] or ["Yes", "No"],
        outcome_prices=[0.0, 0.0],  # CLOB doesn't return prices here
        tokens=tokens,
        active=_to_bool(data.get("active", True)),
        closed=_to_bool(data.get("closed", False)),
        accepting_orders=_to_bool(data.get("accepting_orders", True)),
        neg_risk=_to_bool(data.get("neg_risk", False)),
        end_date=data.get("end_date_iso", ""),
        tick_size=float(data.get("mts", data.get("minimum_tick_size", 0.01)) or 0.01),
        min_order_size=float(data.get("mos", 0) or 0),
        maker_base_fee_bps=int(data.get("mbf", 0) or 0),
        taker_base_fee_bps=int(data.get("tbf", 0) or 0),
        fee_schedule=_clob_fee_schedule(data.get("fd")),
    )


def _parse_search_results(data: object) -> list[Market]:
    """Parse a Gamma /public-search response into a list of Markets.

    The envelope is a dict whose ``events`` list carries nested ``markets``.
    """
    if not isinstance(data, dict):
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []

    markets: list[Market] = []
    for event in events:
        for m in event.get("markets") or []:
            if _has_condition_id(m):
                markets.append(_parse_market(m))
    return markets


def _parse_market(data: dict) -> Market:
    """Parse a Gamma API market response into a Market dataclass.

    Handles both camelCase (live API) and snake_case (cached/test) field names.
    """
    # Parse outcomes — can be JSON string or list
    outcomes_raw = data.get("outcomes", [])
    if isinstance(outcomes_raw, str):
        outcomes_raw = json.loads(outcomes_raw)
    outcomes = outcomes_raw if outcomes_raw else ["Yes", "No"]

    # Parse outcome prices — can be JSON string or list
    outcome_prices_raw = data.get("outcomePrices", data.get("outcome_prices", []))
    if isinstance(outcome_prices_raw, str):
        outcome_prices_raw = json.loads(outcome_prices_raw)
    outcome_prices = [float(p) for p in outcome_prices_raw] if outcome_prices_raw else [0.0, 0.0]

    # Parse tokens — Gamma API uses clobTokenIds (JSON string of IDs matching outcomes order)
    # Also support the tokens list format used in tests/cache
    tokens = []
    clob_token_ids_raw = data.get("clobTokenIds")
    tokens_raw = data.get("tokens")

    if clob_token_ids_raw:
        # Real Gamma API format: clobTokenIds is a JSON string like '["id1", "id2"]'
        if isinstance(clob_token_ids_raw, str):
            clob_token_ids_raw = json.loads(clob_token_ids_raw)
        for i, token_id in enumerate(clob_token_ids_raw):
            outcome_name = outcomes[i] if i < len(outcomes) else f"Outcome{i}"
            tokens.append({
                "token_id": str(token_id),
                "outcome": outcome_name,
            })
    elif tokens_raw:
        # Test/cached format: list of {"token_id": ..., "outcome": ...}
        if isinstance(tokens_raw, str):
            tokens_raw = json.loads(tokens_raw)
        for t in tokens_raw:
            tokens.append({
                "token_id": t.get("token_id", ""),
                "outcome": t.get("outcome", ""),
            })

    # condition_id: Gamma uses conditionId (camelCase)
    condition_id = data.get("conditionId", data.get("condition_id", ""))

    def _to_bool(val, default: bool) -> bool:
        if val is None:
            return default
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    # tick size: Gamma uses orderPriceMinTickSize
    tick_size_raw = data.get("orderPriceMinTickSize",
                             data.get("minimum_tick_size", 0.01))
    tick_size = float(tick_size_raw) if tick_size_raw else 0.01

    # feeSchedule: nested fee metadata (rate is a coefficient, not bps)
    fs_raw = data.get("feeSchedule")
    fee_schedule = None if not isinstance(fs_raw, dict) else {
        "rate": float(fs_raw.get("rate", 0) or 0),
        "exponent": int(fs_raw.get("exponent", 0) or 0),
        "takerOnly": bool(fs_raw.get("takerOnly", False)),
        "rebateRate": float(fs_raw.get("rebateRate", 0) or 0),
    }

    return Market(
        condition_id=condition_id,
        slug=data.get("slug", ""),
        question=data.get("question", ""),
        description=data.get("description", ""),
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        tokens=tokens,
        active=_to_bool(data.get("active"), False),
        closed=_to_bool(data.get("closed"), False),
        accepting_orders=_to_bool(
            data.get("acceptingOrders", data.get("accepting_orders")), True
        ),
        neg_risk=_to_bool(data.get("negRisk", data.get("neg_risk")), False),
        volume=float(data.get("volume", 0) or 0),
        liquidity=float(data.get("liquidity", 0) or 0),
        end_date=data.get("endDateIso", data.get("end_date_iso", data.get("end_date", ""))),
        fee_rate_bps=int(data.get("fee_rate_bps", 0) or 0),
        tick_size=tick_size,
        fee_schedule=fee_schedule,
        maker_base_fee_bps=int(
            data.get("makerBaseFee", data.get("maker_base_fee_bps", 0)) or 0
        ),
        taker_base_fee_bps=int(
            data.get("takerBaseFee", data.get("taker_base_fee_bps", 0)) or 0
        ),
    )


def _parse_order_book(data: dict) -> OrderBook:
    """Parse a CLOB /book response into an OrderBook dataclass."""
    bids = []
    for entry in data.get("bids", []):
        bids.append(OrderBookLevel(
            price=float(entry.get("price", 0)),
            size=float(entry.get("size", 0)),
        ))

    asks = []
    for entry in data.get("asks", []):
        asks.append(OrderBookLevel(
            price=float(entry.get("price", 0)),
            size=float(entry.get("size", 0)),
        ))

    return OrderBook(bids=bids, asks=asks)
