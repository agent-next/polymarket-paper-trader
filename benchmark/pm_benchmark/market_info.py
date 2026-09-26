"""Lightweight read-only Polymarket Gamma API client."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
_TIMEOUT = httpx.Timeout(10.0)


class MarketInfoError(Exception):
    """Raised on API or parsing failures."""


@dataclass(frozen=True)
class MarketInfo:
    """Essential market data for benchmark prompts."""

    slug: str
    question: str
    description: str
    outcomes: list[str]
    outcome_prices: list[float]
    volume: float
    liquidity: float
    end_date: str
    active: bool
    closed: bool
    event_id: str | None = None


@dataclass(frozen=True)
class Resolution:
    """Resolution state for a market.

    ``outcome`` is 1.0 when Yes won, 0.0 when No won, None when the outcome
    cannot be determined. ``closed`` is the market's Gamma closed flag, so a
    closed market with ``outcome=None`` is detectable as unresolvable.
    ``source`` records how the outcome was read: ``"uma"`` (UMA-settled,
    ``umaResolutionStatus == "resolved"``) or ``"price"`` (no oracle status on
    the payload; 0.99/0.01 price rule only).
    """

    outcome: float | None
    closed: bool
    source: str | None


def _fetch_market_raw(
    slug: str,
    *,
    http_client: httpx.Client | None = None,
) -> dict:
    """GET the raw Gamma ``/markets`` payload for a slug.

    Gamma defaults ``closed`` to false, so a closed market needs a
    ``closed=true`` retry before it can be considered missing.
    """
    client = http_client or httpx.Client(timeout=_TIMEOUT)
    owns_client = http_client is None
    try:
        for params in ({"slug": slug}, {"slug": slug, "closed": "true"}):
            resp = client.get(f"{GAMMA_BASE}/markets", params=params)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                if data:
                    return data[0]
            elif isinstance(data, dict):
                return data
        raise MarketInfoError(f"Market not found: {slug}")
    except httpx.HTTPStatusError as e:
        raise MarketInfoError(
            f"API error {e.response.status_code}: {e.response.text[:200]}"
        ) from e
    except httpx.RequestError as e:
        raise MarketInfoError(f"Request failed: {e}") from e
    finally:
        if owns_client:
            client.close()


def fetch_market_info(
    slug: str,
    *,
    http_client: httpx.Client | None = None,
) -> MarketInfo:
    """Fetch market info from Polymarket Gamma API."""
    return _parse_market(_fetch_market_raw(slug, http_client=http_client))


def list_markets(
    *,
    limit: int = 100,
    offset: int = 0,
    active: bool = True,
    closed: bool = False,
    order: str = "volumeNum",
    ascending: bool = False,
    http_client: httpx.Client | None = None,
) -> list[MarketInfo]:
    """List markets from Gamma ``/markets`` ordered by volume or liquidity.

    Ordering uses the camelCase ``volumeNum``/``liquidityNum`` values the
    wire accepts (snake_case variants are rejected upstream). A page is at
    most ~100 markets; ``offset`` fetches further pages.
    """
    params = {
        "limit": limit,
        "offset": offset,
        "active": "true" if active else "false",
        "closed": "true" if closed else "false",
        "order": order,
        "ascending": "true" if ascending else "false",
    }
    client = http_client or httpx.Client(timeout=_TIMEOUT)
    owns_client = http_client is None
    try:
        resp = client.get(f"{GAMMA_BASE}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise MarketInfoError(
            f"API error {e.response.status_code}: {e.response.text[:200]}"
        ) from e
    except httpx.RequestError as e:
        raise MarketInfoError(f"Request failed: {e}") from e
    finally:
        if owns_client:
            client.close()

    if isinstance(data, dict):
        data = data.get("markets", data.get("data", []))
    if not isinstance(data, list):
        return []
    markets = []
    for m in data:
        # One malformed record must not sink the whole page.
        try:
            markets.append(_parse_market(m))
        except (AttributeError, TypeError, ValueError):
            continue
    return markets


def fetch_prices(
    slug: str,
    *,
    http_client: httpx.Client | None = None,
) -> dict[str, float]:
    """Fetch current outcome prices for a market.

    Returns dict like {"Yes": 0.65, "No": 0.35}.
    """
    info = fetch_market_info(slug, http_client=http_client)
    return dict(zip(info.outcomes, info.outcome_prices))


def fetch_resolution(
    slug: str,
    *,
    http_client: httpx.Client | None = None,
) -> float | None:
    """Fetch resolution outcome for a market.

    Returns:
        1.0 if Yes won, 0.0 if No won, None if unresolved.
        Resolution is inferred from a closed market whose Yes price
        is near 1.0 (≥0.99) or near 0.0 (≤0.01).
    """
    return fetch_resolution_detail(slug, http_client=http_client).outcome


def fetch_resolution_detail(
    slug: str,
    *,
    http_client: httpx.Client | None = None,
) -> Resolution:
    """Fetch resolution state for a market.

    The YES side is mapped by outcome label (not by list position). Gamma
    carries no explicit winner field; a UMA-settled market has
    ``umaResolutionStatus: "resolved"`` and final prices of exactly 0/1
    (live-probed 2026-09-26). A closed market whose oracle status is present
    but not yet ``resolved`` stays pending (a closing price is not a
    settlement). When the status is resolved, or absent, the outcome comes
    from the 0.99/0.01 rule on the YES price; a closed market whose outcome
    cannot be determined (e.g. a 50-50 settlement) returns
    ``outcome=None, closed=True`` (unresolvable).
    """
    data = _fetch_market_raw(slug, http_client=http_client)
    info = _parse_market(data)
    if not info.closed:
        return Resolution(outcome=None, closed=False, source=None)

    uma_status = data.get("umaResolutionStatus")
    if isinstance(uma_status, str) and uma_status and uma_status != "resolved":
        return Resolution(outcome=None, closed=False, source=None)
    source = "uma" if uma_status == "resolved" else "price"

    yes_idx = _yes_index(info.outcomes)
    if yes_idx < len(info.outcome_prices):
        yes_price = info.outcome_prices[yes_idx]
        if yes_price >= 0.99:
            return Resolution(outcome=1.0, closed=True, source=source)
        if yes_price <= 0.01:
            return Resolution(outcome=0.0, closed=True, source=source)
    return Resolution(outcome=None, closed=True, source=None)


def _yes_index(outcomes: list[str]) -> int:
    """Index of the Yes outcome by label; defaults to 0."""
    for i, outcome in enumerate(outcomes):
        if outcome.strip().lower() == "yes":
            return i
    return 0


def _parse_market(data: dict) -> MarketInfo:
    """Parse raw Gamma API response into MarketInfo."""
    outcomes = _parse_list(data.get("outcomes", "[]"))
    prices = _parse_list(data.get("outcomePrices", "[]"))
    outcome_prices = [float(p) for p in prices]

    return MarketInfo(
        slug=data.get("slug", ""),
        question=data.get("question", ""),
        description=data.get("description", ""),
        outcomes=outcomes,
        outcome_prices=outcome_prices,
        volume=float(data.get("volume", 0)),
        liquidity=float(data.get("liquidity", 0)),
        end_date=data.get("endDate", ""),
        active=_to_bool(data.get("active", False)),
        closed=_to_bool(data.get("closed", False)),
        event_id=_parse_event_id(data),
    )


def _parse_event_id(data: dict) -> str | None:
    """Gamma event id for cluster grouping: ``eventId`` or ``events[0].id``."""
    for key in ("eventId", "event_id"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    events = data.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        value = events[0].get("id")
        if value not in (None, ""):
            return str(value)
    return None


def _to_bool(value: object) -> bool:
    """String-aware bool parse — Gamma sends booleans as strings."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _parse_list(raw: str | list) -> list[str]:
    """Parse a JSON string list or pass through an actual list."""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    import json
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
