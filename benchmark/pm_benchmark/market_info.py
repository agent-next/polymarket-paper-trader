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


def fetch_market_info(
    slug: str,
    *,
    http_client: httpx.Client | None = None,
) -> MarketInfo:
    """Fetch market info from Polymarket Gamma API."""
    client = http_client or httpx.Client(timeout=_TIMEOUT)
    owns_client = http_client is None
    try:
        resp = client.get(f"{GAMMA_BASE}/markets", params={"slug": slug})
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, list):
            if not data:
                raise MarketInfoError(f"Market not found: {slug}")
            data = data[0]

        return _parse_market(data)
    except httpx.HTTPStatusError as e:
        raise MarketInfoError(
            f"API error {e.response.status_code}: {e.response.text[:200]}"
        ) from e
    except httpx.RequestError as e:
        raise MarketInfoError(f"Request failed: {e}") from e
    finally:
        if owns_client:
            client.close()


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
    info = fetch_market_info(slug, http_client=http_client)
    if not info.closed:
        return None
    if not info.outcome_prices:
        return None
    yes_price = info.outcome_prices[0]
    if yes_price >= 0.99:
        return 1.0
    if yes_price <= 0.01:
        return 0.0
    return None


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
        active=bool(data.get("active", False)),
        closed=bool(data.get("closed", False)),
    )


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
