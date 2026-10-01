"""Performance analytics for pm-trader paper trading.

Pure functions that compute metrics from trade history and account data.
No side effects, no API calls, no database writes.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from pm_trader.models import Account, Position, Trade

_EPSILON = 1e-9


@dataclass(frozen=True)
class Resolution:
    """A resolved position: a closing event that writes no trade row."""

    market_condition_id: str
    outcome: str
    shares: float
    total_cost: float
    payout: float
    resolved_at: str


def build_resolutions(
    resolved_positions: list[Position],
    trades: list[Trade],
) -> list[Resolution]:
    """Reconstruct resolution events from resolved positions and trades.

    The positions table keeps no payout or resolved share count, so both are
    rebuilt: the shares held at resolution come from the net trade shares of
    the position, and the payout from its final ``realized_pnl`` minus the
    realized P&L earned before resolution (average-cost replay of its
    trades, mirroring the engine's accounting).
    """
    by_key: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in _sort_trades_chronological(trades):
        by_key[(t.market_condition_id, t.outcome.lower().strip())].append(t)

    resolutions: list[Resolution] = []
    for pos in resolved_positions:
        held = cost = realized = 0.0
        for t in by_key.get((pos.market_condition_id, pos.outcome.lower().strip()), []):
            if t.side == "buy":
                held += t.shares
                cost += t.amount_usd + t.fee
            elif t.side == "sell":
                cost_sold = cost / held * t.shares if held > _EPSILON else 0.0
                realized += (t.amount_usd - t.fee) - cost_sold
                held -= t.shares
                cost -= cost_sold
                if held <= _EPSILON:
                    held = cost = 0.0
        if held <= _EPSILON:
            continue
        payout = pos.realized_pnl - realized + pos.total_cost
        resolutions.append(Resolution(
            market_condition_id=pos.market_condition_id,
            outcome=pos.outcome.lower().strip(),
            shares=held,
            total_cost=pos.total_cost,
            payout=payout if payout > _EPSILON else 0.0,
            resolved_at=pos.resolved_at or "",
        ))
    return sorted(resolutions, key=lambda r: _parse_trade_datetime(r.resolved_at))


def compute_stats(
    trades: list[Trade],
    account: Account,
    positions_value: float = 0.0,
    resolutions: list[Resolution] | None = None,
) -> dict:
    """Compute all analytics metrics from trade history.

    Args:
        trades: All trades (newest first from DB).
        account: Current account state.
        positions_value: Sum of current_value for open positions.
        resolutions: Resolved positions (resolve_market writes no trade
            row); each counts as a closing event in win rate and the
            equity curve at its resolution time.

    Returns:
        Dict with all metrics.
    """
    total_value = account.cash + positions_value
    pnl = total_value - account.starting_balance
    roi_pct = (pnl / account.starting_balance * 100) if account.starting_balance else 0.0

    chronological = _sort_trades_chronological(trades)

    return {
        "starting_balance": account.starting_balance,
        "cash": account.cash,
        "positions_value": positions_value,
        "total_value": total_value,
        "pnl": pnl,
        "roi_pct": roi_pct,
        "total_trades": len(trades),
        "buy_count": sum(1 for t in trades if t.side == "buy"),
        "sell_count": sum(1 for t in trades if t.side == "sell"),
        "win_rate": win_rate(trades, resolutions),
        "sharpe_ratio": sharpe_ratio(
            chronological, account.starting_balance, resolutions=resolutions,
        ),
        "max_drawdown": max_drawdown(
            chronological, account.starting_balance, resolutions=resolutions,
        ),
        "total_fees": sum(t.fee for t in trades),
        "avg_trade_size": _avg_trade_size(trades),
    }


def win_rate(
    trades: list[Trade],
    resolutions: list[Resolution] | None = None,
) -> float:
    """Fraction of closing events (sells and resolutions) with positive P&L.

    Uses FIFO lots per (market_condition_id, outcome) for entry cost
    accounting. Each buy lot carries a fee-inclusive cost_per_share of
    (amount_usd + fee) / shares; a sell consumes the oldest open lots first.
    A sell is "winning" if net proceeds exceed realized FIFO entry cost; a
    resolution is winning if its payout exceeds the cost basis resolved.
    """
    chronological = _sort_trades_chronological(trades)
    sells = [t for t in chronological if t.side == "sell"]
    resolutions = resolutions or []
    closings = len(sells) + len(resolutions)
    if not closings:
        return 0.0

    # FIFO lots: [[remaining_shares, cost_per_share], ...]
    lots: dict[tuple[str, str], list[list[float]]] = defaultdict(list)
    wins = sum(1 for r in resolutions if r.payout > r.total_cost + _EPSILON)

    for t in chronological:
        key = (t.market_condition_id, t.outcome.lower().strip())
        if t.side == "buy":
            if t.shares <= _EPSILON:
                continue
            cost_per_share = (t.amount_usd + t.fee) / t.shares
            lots[key].append([t.shares, cost_per_share])
            continue
        if t.side != "sell":
            continue

        remaining = max(0.0, t.shares)
        entry_cost = 0.0

        while remaining > _EPSILON and lots[key]:
            lot = lots[key][0]
            take = min(lot[0], remaining)
            entry_cost += take * lot[1]
            lot[0] -= take
            remaining -= take
            if lot[0] <= _EPSILON:
                lots[key].pop(0)

        if remaining > _EPSILON:
            # Uncovered shares: value at the sell's own price (neutral).
            entry_cost += remaining * t.avg_price

        proceeds = t.amount_usd - t.fee
        if proceeds > entry_cost + _EPSILON:
            wins += 1

    return wins / closings


def sharpe_ratio(
    trades_chronological: list[Trade],
    starting_balance: float,
    annualize_days: int = 365,
    resolutions: list[Resolution] | None = None,
) -> float:
    """Annualized Sharpe ratio from daily equity returns (risk-free=0)."""
    daily_returns = _daily_returns(
        trades_chronological, starting_balance, resolutions,
    )
    if len(daily_returns) < 2:
        return 0.0

    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_ret = math.sqrt(variance)

    if std_ret == 0:
        return 0.0

    return (mean_ret / std_ret) * math.sqrt(annualize_days)


def max_drawdown(
    trades_chronological: list[Trade],
    starting_balance: float,
    resolutions: list[Resolution] | None = None,
) -> float:
    """Maximum drawdown from the daily equity curve (0.0 to 1.0)."""
    if not trades_chronological:
        return 0.0

    equity_curve = _daily_equity_curve(
        trades_chronological, starting_balance, resolutions,
    )
    peak = equity_curve[0]
    max_dd = 0.0

    for equity in equity_curve:
        if equity > peak:
            peak = equity

        if peak > 0:
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)

    return max_dd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _daily_pnl(trades_chronological: list[Trade]) -> list[float]:
    """Daily net cashflow series from first trade date to last, zero-filled."""
    return _daily_cashflows(trades_chronological)


def _avg_trade_size(trades: list[Trade]) -> float:
    """Average trade size in USD."""
    if not trades:
        return 0.0
    return sum(t.amount_usd for t in trades) / len(trades)


def _sort_trades_chronological(trades: list[Trade]) -> list[Trade]:
    """Sort trades oldest-first by timestamp, then id."""
    return sorted(trades, key=lambda t: (_parse_trade_datetime(t.created_at), t.id))


def _parse_trade_datetime(created_at: str) -> datetime:
    """Parse a trade timestamp to a naive UTC datetime (min on failure)."""
    text = created_at.strip()
    if not text:
        return datetime.min
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        pass
    if dt is None and " " in text and "T" not in text:
        try:
            dt = datetime.fromisoformat(text.replace(" ", "T", 1))
        except ValueError:
            pass
    if dt is None:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            dt = datetime.min
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _daily_cashflows(trades_chronological: list[Trade]) -> list[float]:
    if not trades_chronological:
        return []

    by_day: dict[date, float] = defaultdict(float)
    for t in trades_chronological:
        day = _parse_trade_datetime(t.created_at).date()
        if t.side == "buy":
            by_day[day] -= (t.amount_usd + t.fee)
        elif t.side == "sell":
            by_day[day] += (t.amount_usd - t.fee)

    if not by_day:
        return []

    days: list[float] = []
    current = min(by_day.keys())
    end = max(by_day.keys())
    while current <= end:
        days.append(by_day.get(current, 0.0))
        current += timedelta(days=1)
    return days


def _daily_equity_curve(
    trades_chronological: list[Trade],
    starting_balance: float,
    resolutions: list[Resolution] | None = None,
) -> list[float]:
    """Daily mark-to-market equity: cash plus long positions at last trade price.

    A resolution closes its position at its resolution day: the payout is
    credited to cash and the marked shares are dropped.
    """
    if not trades_chronological:
        return [starting_balance]

    by_day: dict[date, list[Trade]] = defaultdict(list)
    for t in trades_chronological:
        by_day[_parse_trade_datetime(t.created_at).date()].append(t)

    resolved_by_day: dict[date, list[Resolution]] = defaultdict(list)
    for r in resolutions or []:
        resolved_by_day[_parse_trade_datetime(r.resolved_at).date()].append(r)

    cash = starting_balance
    shares: dict[tuple[str, str], float] = defaultdict(float)
    mark: dict[tuple[str, str], float] = {}
    curve = [starting_balance]

    current = min(by_day.keys())
    end = max([*by_day.keys(), *resolved_by_day.keys()])
    while current <= end:
        for t in by_day.get(current, []):
            key = (t.market_condition_id, t.outcome.lower().strip())
            if t.side == "buy":
                cash -= t.amount_usd + t.fee
                shares[key] += t.shares
                mark[key] = t.avg_price
            elif t.side == "sell":
                cash += t.amount_usd - t.fee
                shares[key] -= t.shares
                mark[key] = t.avg_price
        for r in resolved_by_day.get(current, []):
            cash += r.payout
            shares[(r.market_condition_id, r.outcome)] = 0.0
        equity = cash + sum(max(0.0, s) * mark[k] for k, s in shares.items())
        curve.append(equity)
        current += timedelta(days=1)
    return curve


def _daily_returns(
    trades_chronological: list[Trade],
    starting_balance: float,
    resolutions: list[Resolution] | None = None,
) -> list[float]:
    equity_curve = _daily_equity_curve(
        trades_chronological, starting_balance, resolutions,
    )
    if len(equity_curve) < 2:
        return []
    returns: list[float] = []
    for i in range(1, len(equity_curve)):
        previous = equity_curve[i - 1]
        if previous > 0:
            returns.append((equity_curve[i] - previous) / previous)
        else:
            returns.append(0.0)
    return returns
