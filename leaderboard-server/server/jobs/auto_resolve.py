"""Background job: auto-resolve closed markets."""
from __future__ import annotations

from server.adapters.polymarket import PolymarketClient
from server.db import DB


def auto_resolve_job(db: DB, polymarket: PolymarketClient) -> int:
    """Resolve closed markets for all accounts simultaneously. Returns resolved count."""
    open_positions = db.get_all_open_positions()
    if not open_positions:
        return 0

    # Get distinct market slugs
    slugs = set(p["market_slug"] for p in open_positions)
    resolved_count = 0

    for slug in slugs:
        try:
            market = polymarket.get_market(slug)
        except Exception:
            continue

        if not market.closed:
            continue

        # Check if any outcome price >= 0.99 (market resolved)
        winning_outcome = None
        for i, price in enumerate(market.outcome_prices):
            if price >= 0.99:
                winning_outcome = market.outcomes[i].lower()
                break

        if winning_outcome is None:
            continue

        # Resolve ALL positions for this market across ALL accounts
        for pos in open_positions:
            if pos["market_slug"] != slug:
                continue
            if pos["is_resolved"]:
                continue

            outcome = pos["outcome"]
            shares = float(pos["shares"])

            if outcome == winning_outcome:
                payout = shares * 1.0  # $1 per share
            else:
                payout = 0.0

            # Add payout to account cash
            account = db.get_account(pos["account_id"])
            new_cash = float(account["cash"]) + payout
            db.update_cash(pos["account_id"], new_cash)

            # Mark position as resolved
            realized_pnl = payout - float(pos["total_cost"])
            db.resolve_position(pos["id"], realized_pnl)
            resolved_count += 1

    return resolved_count
