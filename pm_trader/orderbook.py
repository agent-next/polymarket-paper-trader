"""Order book fill simulation engine.

Walks a real Polymarket order book level-by-level to compute exact execution
prices, slippage, and fees.  This is the core of pm-trader's 1:1 faithful
trade simulation.
"""

from __future__ import annotations

import logging

from pm_trader.models import Fill, FillResult, OrderBook

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fee calculation — exact Polymarket formula
# ---------------------------------------------------------------------------

def calculate_fee(fee_rate_bps: int, price: float, size: float) -> float:
    """Return the trading fee using the legacy bps Polymarket formula.

    Formula: (fee_rate_bps / 10_000) * min(price, 1 - price) * size

    This is the pre-``feeSchedule`` model, kept for markets that carry no
    ``feeSchedule`` (and for cached payloads written before it existed).
    Preferred for live markets: :func:`calculate_fee_schedule`.

    The fee is proportional to how close the price is to 0.50.  A minimum
    fee of 0.0001 is enforced when fee_rate_bps > 0 and the computed fee is
    positive.
    """
    if fee_rate_bps == 0:
        return 0.0

    fee = (fee_rate_bps / 10_000) * min(price, 1.0 - price) * size

    if fee > 0.0:
        fee = max(fee, 0.0001)

    return fee


def calculate_fee_schedule(
    rate: float,
    exponent: float,
    price: float,
    shares: float,
) -> float:
    """Return the taker fee using the official ``feeSchedule`` curve.

    Formula (https://docs.polymarket.com/trading/fees, verbatim):

        fee = C × feeRate × p × (1 - p)

    where ``C`` is the number of shares traded and ``p`` the price of the
    shares.  The result is a USDC amount charged to the taker at match time —
    never a share count — so buys and sells are charged identically for the
    same ``C`` and ``p``, and the fee is symmetric around ``p = 0.50``.

    ``exponent`` is ``feeSchedule.exponent`` ("Exponent applied to the price
    component of the fee curve").  The published curve is the identity
    (``exponent = 1``); a non-identity exponent is not published anywhere, so
    it is applied to the ``p * (1 - p)`` price component and logged.

    Per the "Fee Precision" rule, fees are rounded to 5 decimal places: the
    smallest fee charged is 0.00001 USDC and anything smaller rounds to zero,
    so very small trades near the extremes may incur no fee at all.
    """
    if rate <= 0.0 or shares <= 0.0 or price <= 0.0 or price >= 1.0:
        return 0.0

    if exponent != 1:
        logger.warning(
            "feeSchedule exponent %s is not the published identity (1); "
            "applying it to the p*(1-p) price component",
            exponent,
        )

    return round(shares * rate * (price * (1.0 - price)) ** exponent, 5)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fill_fee(
    *,
    avg_price: float,
    total_shares: float,
    legacy_size: float,
    fee_rate: float | None,
    fee_rate_bps: int,
) -> float:
    """Fee for one simulated fill.

    When ``fee_rate`` (the ``feeSchedule`` coefficient) is set it takes
    precedence and the fee is charged on the share count ``C`` — the official
    curve — on both the buy and the sell path.  Otherwise the legacy bps
    model is used unchanged, with its per-side ``legacy_size`` (the USD
    notional on a buy, the share count on a sell).
    """
    if fee_rate is not None:
        return calculate_fee_schedule(fee_rate, 1, avg_price, total_shares)
    return calculate_fee(fee_rate_bps, avg_price, legacy_size)


def _midpoint(book: OrderBook) -> float | None:
    """Return (best_bid + best_ask) / 2, or None if either side is empty."""
    if not book.bids or not book.asks:
        return None

    best_bid = max(level.price for level in book.bids)
    best_ask = min(level.price for level in book.asks)
    return (best_bid + best_ask) / 2.0


def _empty_fill_result() -> FillResult:
    """Return a FillResult representing no execution."""
    return FillResult(
        filled=False,
        avg_price=0.0,
        total_cost=0.0,
        total_shares=0.0,
        fee=0.0,
        slippage_bps=0.0,
        levels_filled=0,
        is_partial=False,
        fills=[],
    )


# ---------------------------------------------------------------------------
# Buy simulation — walk the ASK side
# ---------------------------------------------------------------------------

def simulate_buy_fill(
    book: OrderBook,
    amount_usd: float,
    fee_rate_bps: int,
    order_type: str = "fok",
    max_price: float | None = None,
    fee_rate: float | None = None,
) -> FillResult:
    """Simulate buying shares by spending *amount_usd*.

    Walks the ASK side of the order book from lowest price upward, consuming
    liquidity level-by-level.

    Parameters
    ----------
    book:
        The current order book snapshot.
    amount_usd:
        Total USD to spend on shares (before fees).
    fee_rate_bps:
        Legacy market fee rate in basis points — used only when ``fee_rate``
        is not given.
    fee_rate:
        ``feeSchedule.rate`` coefficient.  When set, the official curve is
        used and the fee is charged on the share count (not the notional).
    order_type:
        ``"fok"`` (fill-or-kill: all or nothing) or
        ``"fak"`` (fill-and-kill: partial fills allowed).
    max_price:
        If set, skip ask levels priced above this limit.

    Returns
    -------
    FillResult
        Detailed execution result including per-level fills.
    """
    if not book.asks:
        if order_type == "fok":
            return _empty_fill_result()
        return _empty_fill_result()

    sorted_asks = sorted(book.asks, key=lambda lvl: lvl.price)

    remaining_usd = amount_usd
    fills: list[Fill] = []

    for level_idx, level in enumerate(sorted_asks):
        if remaining_usd <= 0:
            break

        # Limit order: skip levels above max_price
        if max_price is not None and level.price > max_price:
            break

        max_cost_at_level = level.size * level.price

        if max_cost_at_level <= remaining_usd:
            # Consume the entire level
            fills.append(Fill(
                price=level.price,
                shares=level.size,
                cost=max_cost_at_level,
                level=level_idx + 1,
            ))
            remaining_usd -= max_cost_at_level
        else:
            # Partial level fill — buy as many shares as remaining USD allows
            shares = remaining_usd / level.price
            fills.append(Fill(
                price=level.price,
                shares=shares,
                cost=remaining_usd,
                level=level_idx + 1,
            ))
            remaining_usd = 0.0
            break

    if not fills:
        return _empty_fill_result()

    total_cost = sum(f.cost for f in fills)
    total_shares = sum(f.shares for f in fills)

    # FOK: reject if the book could not absorb the full amount
    is_partial = remaining_usd > 0
    if order_type == "fok" and is_partial:
        return _empty_fill_result()

    avg_price = total_cost / total_shares if total_shares > 0 else 0.0
    fee = _fill_fee(
        avg_price=avg_price,
        total_shares=total_shares,
        legacy_size=total_cost,
        fee_rate=fee_rate,
        fee_rate_bps=fee_rate_bps,
    )

    midpoint = _midpoint(book)
    if midpoint and midpoint > 0:
        slippage_bps = (avg_price - midpoint) / midpoint * 10_000
    else:
        slippage_bps = 0.0

    return FillResult(
        filled=not is_partial,
        avg_price=avg_price,
        total_cost=total_cost,
        total_shares=total_shares,
        fee=fee,
        slippage_bps=slippage_bps,
        levels_filled=len(fills),
        is_partial=is_partial,
        fills=fills,
    )


# ---------------------------------------------------------------------------
# Sell simulation — walk the BID side
# ---------------------------------------------------------------------------

def simulate_sell_fill(
    book: OrderBook,
    shares: float,
    fee_rate_bps: int,
    order_type: str = "fok",
    min_price: float | None = None,
    fee_rate: float | None = None,
) -> FillResult:
    """Simulate selling *shares* into the order book.

    Walks the BID side of the order book from highest price downward,
    consuming liquidity level-by-level.

    Parameters
    ----------
    book:
        The current order book snapshot.
    shares:
        Number of shares to sell.
    fee_rate_bps:
        Legacy market fee rate in basis points — used only when ``fee_rate``
        is not given.
    fee_rate:
        ``feeSchedule.rate`` coefficient.  When set, the official curve is
        used and the fee is charged on the share count.
    order_type:
        ``"fok"`` (fill-or-kill) or ``"fak"`` (fill-and-kill).
    min_price:
        If set, skip bid levels priced below this limit.

    Returns
    -------
    FillResult
        Detailed execution result including per-level fills.
    """
    if not book.bids:
        if order_type == "fok":
            return _empty_fill_result()
        return _empty_fill_result()

    sorted_bids = sorted(book.bids, key=lambda lvl: lvl.price, reverse=True)

    remaining_shares = shares
    fills: list[Fill] = []

    for level_idx, level in enumerate(sorted_bids):
        if remaining_shares <= 0:
            break

        # Limit order: skip levels below min_price
        if min_price is not None and level.price < min_price:
            break

        if level.size <= remaining_shares:
            # Consume entire level
            cost = level.size * level.price
            fills.append(Fill(
                price=level.price,
                shares=level.size,
                cost=cost,
                level=level_idx + 1,
            ))
            remaining_shares -= level.size
        else:
            # Partial level fill — sell only the remaining shares
            cost = remaining_shares * level.price
            fills.append(Fill(
                price=level.price,
                shares=remaining_shares,
                cost=cost,
                level=level_idx + 1,
            ))
            remaining_shares = 0.0
            break

    if not fills:
        return _empty_fill_result()

    total_cost = sum(f.cost for f in fills)
    total_shares = sum(f.shares for f in fills)

    # FOK: reject if the book could not absorb all shares
    is_partial = remaining_shares > 0
    if order_type == "fok" and is_partial:
        return _empty_fill_result()

    avg_price = total_cost / total_shares if total_shares > 0 else 0.0
    fee = _fill_fee(
        avg_price=avg_price,
        total_shares=total_shares,
        legacy_size=total_shares,
        fee_rate=fee_rate,
        fee_rate_bps=fee_rate_bps,
    )

    midpoint = _midpoint(book)
    if midpoint and midpoint > 0:
        # Selling below midpoint means negative slippage
        slippage_bps = (avg_price - midpoint) / midpoint * 10_000
    else:
        slippage_bps = 0.0

    return FillResult(
        filled=not is_partial,
        avg_price=avg_price,
        total_cost=total_cost,
        total_shares=total_shares,
        fee=fee,
        slippage_bps=slippage_bps,
        levels_filled=len(fills),
        is_partial=is_partial,
        fills=fills,
    )
