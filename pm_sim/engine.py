"""Trade execution engine for pm-sim.

Orchestrates the full buy/sell/resolve workflow by wiring together
the API client, order book simulator, and database layer.
"""

from __future__ import annotations

from pathlib import Path

from pm_sim.api import PolymarketClient
from pm_sim.db import Database
from pm_sim.models import (
    Account,
    InsufficientBalanceError,
    InvalidOutcomeError,
    MarketClosedError,
    NoPositionError,
    NotInitializedError,
    OrderRejectedError,
    Position,
    ResolveResult,
    Trade,
    TradeResult,
)
from pm_sim.orderbook import simulate_buy_fill, simulate_sell_fill

MIN_ORDER_USD = 1.0  # Polymarket minimum order size


class Engine:
    """Paper trading engine — 1:1 faithful to Polymarket execution."""

    def __init__(self, data_dir: Path) -> None:
        self.db = Database(data_dir)
        self.db.init_schema()
        self.api = PolymarketClient(self.db)

    def close(self) -> None:
        self.api.close()
        self.db.close()

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def init_account(self, balance: float = 10_000.0) -> Account:
        return self.db.init_account(balance)

    def get_account(self) -> Account:
        account = self.db.get_account()
        if account is None:
            raise NotInitializedError()
        return account

    def reset(self) -> None:
        self.db.reset()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _require_account(self) -> Account:
        return self.get_account()

    @staticmethod
    def _validate_outcome(outcome: str) -> str:
        outcome = outcome.lower().strip()
        if outcome not in ("yes", "no"):
            raise InvalidOutcomeError(outcome)
        return outcome

    # ------------------------------------------------------------------
    # BUY — spend USD, receive shares
    # ------------------------------------------------------------------

    def buy(
        self,
        slug_or_id: str,
        outcome: str,
        amount_usd: float,
        order_type: str = "fok",
    ) -> TradeResult:
        """Execute a buy order: spend amount_usd to receive shares.

        Walks the real order book ASK side level-by-level.
        """
        account = self._require_account()
        outcome = self._validate_outcome(outcome)

        if amount_usd < MIN_ORDER_USD:
            raise OrderRejectedError(
                f"Minimum order size is ${MIN_ORDER_USD:.2f}"
            )

        # Fetch market, live order book, and fee rate
        market, book, fee_rate_bps = self.api.get_trade_context(
            slug_or_id, outcome
        )

        if market.closed:
            raise MarketClosedError(market.slug)

        # Simulate fill against the real order book
        fill = simulate_buy_fill(book, amount_usd, fee_rate_bps, order_type)

        if not fill.filled and not fill.is_partial:
            raise OrderRejectedError(
                "Insufficient liquidity in order book (FOK rejected)"
            )

        # Check cash: need total_cost + fee
        total_outflow = fill.total_cost + fill.fee
        if total_outflow > account.cash:
            raise InsufficientBalanceError(
                required=total_outflow, available=account.cash
            )

        # Update cash
        new_cash = account.cash - total_outflow
        self.db.update_cash(new_cash)

        # Record trade
        trade = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="buy",
            order_type=order_type,
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )

        # Update position
        self._update_position_after_buy(
            market=market,
            outcome=outcome,
            new_shares=fill.total_shares,
            cost=fill.total_cost + fill.fee,
            avg_fill_price=fill.avg_price,
        )

        updated_account = self.get_account()
        return TradeResult(trade=trade, account=updated_account)

    def _update_position_after_buy(
        self,
        *,
        market,
        outcome: str,
        new_shares: float,
        cost: float,
        avg_fill_price: float,
    ) -> None:
        """Update or create position after a buy."""
        existing = self.db.get_position(market.condition_id, outcome)
        if existing and existing.shares > 0:
            total_shares = existing.shares + new_shares
            total_cost = existing.total_cost + cost
            avg_entry = total_cost / total_shares if total_shares > 0 else 0.0
        else:
            total_shares = new_shares
            total_cost = cost
            avg_entry = avg_fill_price

        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares=total_shares,
            avg_entry_price=avg_entry,
            total_cost=total_cost,
            realized_pnl=existing.realized_pnl if existing else 0.0,
        )

    # ------------------------------------------------------------------
    # SELL — sell shares, receive USD
    # ------------------------------------------------------------------

    def sell(
        self,
        slug_or_id: str,
        outcome: str,
        shares: float,
        order_type: str = "fok",
    ) -> TradeResult:
        """Execute a sell order: sell shares to receive USD.

        Walks the real order book BID side level-by-level.
        """
        account = self._require_account()
        outcome = self._validate_outcome(outcome)

        # Must have a position to sell
        market = self.api.get_market(slug_or_id)
        position = self.db.get_position(market.condition_id, outcome)
        if position is None or position.shares <= 0:
            raise NoPositionError(market.slug, outcome)

        if shares > position.shares:
            raise OrderRejectedError(
                f"Cannot sell {shares:.4f} shares, only hold {position.shares:.4f}"
            )

        if market.closed:
            raise MarketClosedError(market.slug)

        # Fetch live book and fee rate
        token_id = (
            market.yes_token_id if outcome == "yes" else market.no_token_id
        )
        book = self.api.get_order_book(token_id)
        fee_rate_bps = self.api.get_fee_rate(token_id)

        # Simulate fill against the real order book
        fill = simulate_sell_fill(book, shares, fee_rate_bps, order_type)

        if not fill.filled and not fill.is_partial:
            raise OrderRejectedError(
                "Insufficient liquidity in order book (FOK rejected)"
            )

        # Net proceeds = gross - fee
        net_proceeds = fill.total_cost - fill.fee

        # Update cash
        new_cash = account.cash + net_proceeds
        self.db.update_cash(new_cash)

        # Record trade
        trade = self.db.insert_trade(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            side="sell",
            order_type=order_type,
            avg_price=fill.avg_price,
            amount_usd=fill.total_cost,
            shares=fill.total_shares,
            fee_rate_bps=fee_rate_bps,
            fee=fill.fee,
            slippage=fill.slippage_bps,
            levels_filled=fill.levels_filled,
            is_partial=fill.is_partial,
        )

        # Update position
        self._update_position_after_sell(
            market=market,
            outcome=outcome,
            sold_shares=fill.total_shares,
            proceeds=net_proceeds,
        )

        updated_account = self.get_account()
        return TradeResult(trade=trade, account=updated_account)

    def _update_position_after_sell(
        self,
        *,
        market,
        outcome: str,
        sold_shares: float,
        proceeds: float,
    ) -> None:
        """Update position after a sell."""
        existing = self.db.get_position(market.condition_id, outcome)
        if existing is None:
            return

        remaining_shares = existing.shares - sold_shares
        # Cost basis of sold portion
        cost_of_sold = (
            existing.avg_entry_price * sold_shares
            if existing.shares > 0
            else 0.0
        )
        realized_pnl = existing.realized_pnl + (proceeds - cost_of_sold)
        remaining_cost = existing.total_cost - cost_of_sold

        self.db.upsert_position(
            market_condition_id=market.condition_id,
            market_slug=market.slug,
            market_question=market.question,
            outcome=outcome,
            shares=max(remaining_shares, 0.0),
            avg_entry_price=existing.avg_entry_price,
            total_cost=max(remaining_cost, 0.0),
            realized_pnl=realized_pnl,
        )

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    def get_portfolio(self) -> list[dict]:
        """Return open positions with live prices and unrealized P&L."""
        self._require_account()
        positions = self.db.get_open_positions()
        result = []
        for pos in positions:
            try:
                token_id = self._get_token_id_for_position(pos)
                live_price = self.api.get_midpoint(token_id)
            except Exception:
                live_price = 0.0

            result.append({
                "market_slug": pos.market_slug,
                "market_question": pos.market_question,
                "outcome": pos.outcome,
                "shares": pos.shares,
                "avg_entry_price": pos.avg_entry_price,
                "total_cost": pos.total_cost,
                "live_price": live_price,
                "current_value": pos.current_value(live_price),
                "unrealized_pnl": pos.unrealized_pnl(live_price),
                "percent_pnl": pos.percent_pnl(live_price),
            })
        return result

    def _get_token_id_for_position(self, pos: Position) -> str:
        """Resolve a position to its token_id for price lookups."""
        market = self.api.get_market(pos.market_slug)
        if pos.outcome == "yes":
            return market.yes_token_id
        return market.no_token_id

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    def get_balance(self) -> dict:
        """Return cash, positions value, and total account value."""
        account = self._require_account()
        portfolio = self.get_portfolio()
        positions_value = sum(p["current_value"] for p in portfolio)
        return {
            "cash": account.cash,
            "starting_balance": account.starting_balance,
            "positions_value": positions_value,
            "total_value": account.cash + positions_value,
            "pnl": (account.cash + positions_value) - account.starting_balance,
        }

    # ------------------------------------------------------------------
    # Trade history
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 50) -> list[Trade]:
        """Return recent trades."""
        self._require_account()
        return self.db.get_trades(limit)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve_market(self, slug_or_id: str) -> list[ResolveResult]:
        """Resolve a market's positions, paying out $1/share for winner."""
        account = self._require_account()
        market = self.api.get_market(slug_or_id)

        if not market.closed:
            raise MarketClosedError(
                f"{market.slug} is not yet closed/resolved"
            )

        positions = self.db.get_positions_for_market(market.condition_id)
        if not positions:
            raise NoPositionError(market.slug, "any")

        results = []
        for pos in positions:
            if pos.is_resolved or pos.shares <= 0:
                continue

            # Determine payout: $1/share for winning outcome, $0 for losing
            winning_outcome = _determine_winner(market)
            if pos.outcome == winning_outcome:
                payout = pos.shares * 1.0
            else:
                payout = 0.0

            resolved_pos = self.db.resolve_position(
                market.condition_id, pos.outcome, payout
            )

            # Add payout to cash
            account = self.get_account()
            new_cash = account.cash + payout
            self.db.update_cash(new_cash)
            account = self.get_account()

            results.append(ResolveResult(
                position=resolved_pos,
                payout=payout,
                account=account,
            ))

        return results

    def resolve_all(self) -> list[ResolveResult]:
        """Resolve all open positions in closed markets."""
        self._require_account()
        positions = self.db.get_open_positions()
        all_results = []

        seen_markets: set[str] = set()
        for pos in positions:
            if pos.market_condition_id in seen_markets:
                continue
            try:
                market = self.api.get_market(pos.market_slug)
                if market.closed:
                    seen_markets.add(pos.market_condition_id)
                    results = self.resolve_market(pos.market_slug)
                    all_results.extend(results)
            except Exception:
                continue

        return all_results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _determine_winner(market) -> str:
    """Determine the winning outcome from a resolved market's prices."""
    for i, outcome in enumerate(market.outcomes):
        price = market.outcome_prices[i] if i < len(market.outcome_prices) else 0.0
        if price >= 0.99:
            return outcome.lower()
    return ""
