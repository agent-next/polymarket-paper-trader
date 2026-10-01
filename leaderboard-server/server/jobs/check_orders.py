"""Background job: check pending limit orders against live order books."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from server.adapters.polymarket import (
    simulate_buy_fill,
    simulate_sell_fill,
    PolymarketClient,
)
from server.db import DB


class _SkipOrder(Exception):
    """Abort one order's transaction; the order stays as it was."""


def check_orders_job(db: DB, polymarket: PolymarketClient) -> int:
    """Check all pending limit orders. Returns count of filled orders.

    1. Get all pending limit orders
    2. Group by (slug, condition id) (minimize API calls)
    3. For each group, fetch live order book
    4. For each order, simulate fill with limit price
    5. If fillable: execute fill, update account/position/trade
    """
    # Expire GTD orders first so check loop only sees still-actionable orders.
    db.expire_orders(datetime.now(timezone.utc).isoformat())

    pending = db.get_pending_orders()  # all users' orders
    if not pending:
        return 0

    filled_count = 0

    # Group by (slug, condition) to minimize API calls without mixing markets
    by_market: dict[tuple[str, str], list[dict]] = {}
    for order in pending:
        key = (order["market_slug"], order["market_condition_id"])
        by_market.setdefault(key, []).append(order)

    for (slug, condition_id), orders in by_market.items():
        try:
            market = polymarket.get_market(slug)
        except Exception:
            continue  # Skip if API fails
        if market.condition_id != condition_id:
            continue  # Slug/condition mismatch: never fill against the wrong market

        # Reuse market context per token_id within this condition group.
        token_ctx: dict[str, tuple[object, int, int]] = {}

        for order in orders:
            try:
                outcome = order["outcome"]
                token_id = market.get_token_id(outcome)
                if token_id in token_ctx:
                    book, fee_rate_bps, snapshot_id = token_ctx[token_id]
                else:
                    book = polymarket.get_order_book(token_id)
                    fee_rate_bps = polymarket.get_fee_rate(token_id)
                    snapshot_id = db.save_book_snapshot(
                        token_id, dataclasses.asdict(book),
                    )
                    token_ctx[token_id] = (book, fee_rate_bps, snapshot_id)

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
                        with db.transaction():
                            if db.fill_order(order["id"]) is None or not db.debit_cash(
                                order["account_id"], total_outflow,
                            ):
                                raise _SkipOrder
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
                                book_snapshot_id=snapshot_id,
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
                        with db.transaction():
                            existing = db.get_position(
                                order["account_id"], condition_id, outcome,
                            )
                            if (
                                db.fill_order(order["id"]) is None
                                or not existing
                                or existing["shares"] < fill.total_shares
                            ):
                                raise _SkipOrder
                            db.credit_cash(order["account_id"], net_proceeds)
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
                                book_snapshot_id=snapshot_id,
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
                            filled_count += 1
            except Exception:
                continue

    return filled_count
