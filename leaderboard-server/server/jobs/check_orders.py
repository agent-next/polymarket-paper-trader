"""Background job: check pending limit orders against live order books."""
from __future__ import annotations

from server.adapters.polymarket import (
    simulate_buy_fill,
    simulate_sell_fill,
    PolymarketClient,
)
from server.db import DB


def check_orders_job(db: DB, polymarket: PolymarketClient) -> int:
    """Check all pending limit orders. Returns count of filled orders.

    1. Get all pending limit orders
    2. Group by market_condition_id (minimize API calls)
    3. For each group, fetch live order book
    4. For each order, simulate fill with limit price
    5. If fillable: execute fill, update account/position/trade
    """
    pending = db.get_pending_orders()  # all users' orders
    if not pending:
        return 0

    filled_count = 0

    # Group orders by market_condition_id to minimize API calls
    by_condition: dict[str, list[dict]] = {}
    for order in pending:
        key = order["market_condition_id"]
        by_condition.setdefault(key, []).append(order)

    for condition_id, orders in by_condition.items():
        first = orders[0]
        try:
            market = polymarket.get_market(first["market_slug"])
            token_id = market.get_token_id(first["outcome"])
            book = polymarket.get_order_book(token_id)
            fee_rate_bps = polymarket.get_fee_rate(token_id)
        except Exception:
            continue  # Skip if API fails

        for order in orders:
            try:
                outcome = order["outcome"]
                token_id = market.get_token_id(outcome)

                if order["side"] == "buy":
                    fill = simulate_buy_fill(
                        book,
                        order["amount"],
                        fee_rate_bps,
                        "fok",
                        max_price=order["limit_price"],
                    )
                    if fill.filled or fill.is_partial:
                        total_outflow = fill.total_cost + fill.fee
                        account = db.get_account(order["account_id"])
                        if total_outflow <= float(account["cash"]):
                            # Execute fill
                            db.update_cash(
                                order["account_id"],
                                float(account["cash"]) - total_outflow,
                            )
                            db.insert_trade(
                                account_id=order["account_id"],
                                market_condition_id=condition_id,
                                market_slug=order["market_slug"],
                                market_question=market.question,
                                outcome=outcome,
                                side="buy",
                                order_type="limit",
                                avg_price=fill.avg_price,
                                amount_usd=fill.total_cost,
                                shares=fill.total_shares,
                                fee_rate_bps=fee_rate_bps,
                                fee=fill.fee,
                                slippage=fill.slippage_bps,
                                levels_filled=fill.levels_filled,
                                is_partial=fill.is_partial,
                                book_snapshot_id=None,
                            )
                            # Update position
                            existing = db.get_position(
                                order["account_id"], condition_id, outcome,
                            )
                            if existing and existing["shares"] > 0:
                                total_shares = existing["shares"] + fill.total_shares
                                total_cost = existing["total_cost"] + total_outflow
                                avg_entry = total_cost / total_shares
                            else:
                                total_shares = fill.total_shares
                                total_cost = total_outflow
                                avg_entry = fill.avg_price
                            db.upsert_position(
                                account_id=order["account_id"],
                                market_condition_id=condition_id,
                                market_slug=order["market_slug"],
                                market_question=market.question,
                                outcome=outcome,
                                shares=total_shares,
                                avg_entry_price=avg_entry,
                                total_cost=total_cost,
                                realized_pnl=(
                                    existing["realized_pnl"] if existing else 0.0
                                ),
                            )
                            db.fill_order(order["id"])
                            filled_count += 1

                elif order["side"] == "sell":
                    # For sell limit orders, amount is in shares
                    fill = simulate_sell_fill(
                        book,
                        order["amount"],
                        fee_rate_bps,
                        "fok",
                        min_price=order["limit_price"],
                    )
                    if fill.filled or fill.is_partial:
                        net_proceeds = fill.total_cost - fill.fee
                        account = db.get_account(order["account_id"])
                        existing = db.get_position(
                            order["account_id"], condition_id, outcome,
                        )
                        if existing and existing["shares"] >= fill.total_shares:
                            db.update_cash(
                                order["account_id"],
                                float(account["cash"]) + net_proceeds,
                            )
                            db.insert_trade(
                                account_id=order["account_id"],
                                market_condition_id=condition_id,
                                market_slug=order["market_slug"],
                                market_question=market.question,
                                outcome=outcome,
                                side="sell",
                                order_type="limit",
                                avg_price=fill.avg_price,
                                amount_usd=fill.total_cost,
                                shares=fill.total_shares,
                                fee_rate_bps=fee_rate_bps,
                                fee=fill.fee,
                                slippage=fill.slippage_bps,
                                levels_filled=fill.levels_filled,
                                is_partial=fill.is_partial,
                                book_snapshot_id=None,
                            )
                            # Update position
                            remaining = existing["shares"] - fill.total_shares
                            cost_of_sold = (
                                existing["avg_entry_price"] * fill.total_shares
                            )
                            realized = existing["realized_pnl"] + (
                                net_proceeds - cost_of_sold
                            )
                            remaining_cost = existing["total_cost"] - cost_of_sold
                            db.upsert_position(
                                account_id=order["account_id"],
                                market_condition_id=condition_id,
                                market_slug=order["market_slug"],
                                market_question=market.question,
                                outcome=outcome,
                                shares=max(remaining, 0),
                                avg_entry_price=existing["avg_entry_price"],
                                total_cost=max(remaining_cost, 0),
                                realized_pnl=realized,
                            )
                            db.fill_order(order["id"])
                            filled_count += 1
            except Exception:
                continue

    return filled_count
