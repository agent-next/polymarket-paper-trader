"""Tests covering remaining coverage gaps to reach 100%.

Covers: auth, db, trading, orders, accounts, portfolio, leaderboard,
website, check_orders, and auto_resolve edge cases.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from server.db import DB
from server.jobs.check_orders import check_orders_job
from server.jobs.auto_resolve import auto_resolve_job
from server.adapters.polymarket import (
    Market,
    OrderBook,
    OrderBookLevel,
)

from tests.conftest import (
    MockPolymarketClient,
    EMPTY_BOOK,
    _register,
    _headers,
    _create_account,
    _sell,
    _register_and_create_account,
    _insert_trades,
)


# ---------------------------------------------------------------------------
# Shared fixtures and helpers for jobs tests
# ---------------------------------------------------------------------------

class _FailingClient(MockPolymarketClient):
    """Mock client that raises on get_market (simulates API failure)."""

    def get_market(self, slug):
        raise ConnectionError("API down")


@pytest.fixture
def jobs_db():
    """Standalone in-memory DB for jobs tests (not tied to FastAPI app)."""
    _db = DB(":memory:")
    _db.init_schema()
    yield _db
    _db.close()


JOBS_MARKET = Market(
    condition_id="0xabc123",
    slug="test-market",
    question="Test?",
    description="",
    outcomes=["Yes", "No"],
    outcome_prices=[0.65, 0.35],
    tokens=[
        {"token_id": "tok_yes", "outcome": "Yes"},
        {"token_id": "tok_no", "outcome": "No"},
    ],
    active=True,
    closed=False,
    volume=1000000,
    liquidity=100000,
    end_date="2026-12-31",
    fee_rate_bps=0,
    tick_size=0.01,
)

JOBS_BOOK = OrderBook(
    bids=[
        OrderBookLevel(price=0.64, size=150),
        OrderBookLevel(price=0.63, size=200),
    ],
    asks=[
        OrderBookLevel(price=0.45, size=500),
        OrderBookLevel(price=0.48, size=300),
    ],
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuthGaps:
    def test_auth_without_bearer_prefix(self, client):
        """Authorization header without 'Bearer ' prefix → 401."""
        resp = client.get("/accounts", headers={"Authorization": "Token abc123"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

class TestDBGaps:
    def test_get_all_accounts_with_user(self, client):
        """get_all_accounts_with_user() returns agent_name via JOIN."""
        user = _register(client, "db-bot")
        _create_account(client, user["api_key"], "acct1")
        db = client.app.state.db
        rows = db.get_all_accounts_with_user()
        assert len(rows) >= 1
        assert rows[0]["agent_name"] == "db-bot"


# ---------------------------------------------------------------------------
# Trading validation (422s — schema rejects before route handler)
# ---------------------------------------------------------------------------

class TestTradingValidation:
    def test_buy_invalid_order_type(self, client):
        """Buy with order_type not in (fok, fak) → 422."""
        user = _register(client)
        resp = client.post("/trade/buy", json={
            "account_id": 1,
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "amount_usd": 10,
            "order_type": "ioc",
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 422

    def test_sell_invalid_order_type(self, client):
        """Sell with order_type not in (fok, fak) → 422."""
        user = _register(client)
        resp = client.post("/trade/sell", json={
            "account_id": 1,
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "shares": 10,
            "order_type": "ioc",
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 422

    def test_sell_zero_shares(self, client):
        """Sell with zero shares → 422."""
        user = _register(client)
        resp = client.post("/trade/sell", json={
            "account_id": 1,
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "shares": 0,
            "order_type": "fok",
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 422

    def test_sell_negative_shares(self, client):
        """Sell with negative shares → 422."""
        user = _register(client)
        resp = client.post("/trade/sell", json={
            "account_id": 1,
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "shares": -5,
            "order_type": "fok",
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Trading authorization
# ---------------------------------------------------------------------------

class TestTradingAuth:
    def test_buy_nonexistent_account(self, client):
        """Buy on non-existent account → 404."""
        user = _register(client)
        resp = client.post("/trade/buy", json={
            "account_id": 9999,
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "amount_usd": 10,
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 404

    def test_buy_other_users_account(self, client):
        """Buy on another user's account → 403."""
        user1 = _register(client, "user1")
        user2 = _register(client, "user2")
        account1 = _create_account(client, user1["api_key"], "acct1")
        resp = client.post("/trade/buy", json={
            "account_id": account1["id"],
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "amount_usd": 10,
        }, headers=_headers(user2["api_key"]))
        assert resp.status_code == 403

    def test_buy_invalid_outcome(self, client):
        """Buy with invalid outcome → 400 INVALID_OUTCOME."""
        _, account, headers = _register_and_create_account(client)
        resp = client.post("/trade/buy", json={
            "account_id": account["id"],
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "maybe",
            "amount_usd": 10,
        }, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_OUTCOME"

    def test_sell_nonexistent_account(self, client):
        """Sell on non-existent account → 404."""
        user = _register(client)
        resp = client.post("/trade/sell", json={
            "account_id": 9999,
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "shares": 10,
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 404

    def test_sell_other_users_account(self, client):
        """Sell on another user's account → 403."""
        user1 = _register(client, "seller1")
        user2 = _register(client, "seller2")
        account1 = _create_account(client, user1["api_key"], "acct1")
        resp = client.post("/trade/sell", json={
            "account_id": account1["id"],
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "yes",
            "shares": 10,
        }, headers=_headers(user2["api_key"]))
        assert resp.status_code == 403

    def test_sell_invalid_outcome(self, client):
        """Sell with invalid outcome → 400 INVALID_OUTCOME."""
        _, account, headers = _register_and_create_account(client)
        resp = client.post("/trade/sell", json={
            "account_id": account["id"],
            "market_slug": "will-bitcoin-hit-100k",
            "outcome": "maybe",
            "shares": 10,
        }, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "INVALID_OUTCOME"


# ---------------------------------------------------------------------------
# Trading error paths
# ---------------------------------------------------------------------------

class TestTradingErrors:
    def test_sell_on_closed_market(self, client_closed_market):
        """Sell on a closed market → 400 MARKET_CLOSED."""
        client = client_closed_market
        user, account, _ = _register_and_create_account(client)
        db = client.app.state.db
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xclosed",
            market_slug="closed-market",
            market_question="Already resolved?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.5,
            total_cost=50,
            realized_pnl=0,
        )
        resp = _sell(client, user["api_key"], account["id"],
                     shares=10, outcome="yes", slug="closed-market")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "MARKET_CLOSED"

    def test_sell_fok_empty_book(self, client_empty_book):
        """Sell FOK with no bids → 400 ORDER_REJECTED."""
        client = client_empty_book
        user, account, _ = _register_and_create_account(client)
        db = client.app.state.db
        db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc123",
            market_slug="will-bitcoin-hit-100k",
            market_question="Will Bitcoin hit $100k by end of 2026?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.65,
            total_cost=65,
            realized_pnl=0,
        )
        resp = _sell(client, user["api_key"], account["id"],
                     shares=10, outcome="yes")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "ORDER_REJECTED"


# ---------------------------------------------------------------------------
# Orders validation (422s — schema rejects before route handler)
# ---------------------------------------------------------------------------

class TestOrdersValidation:
    def test_place_order_zero_amount(self, client):
        """Place order with zero amount → 422."""
        user = _register(client)
        resp = client.post("/accounts/1/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 0,
            "limit_price": 0.5,
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 422

    def test_place_order_invalid_order_type(self, client):
        """Place order with invalid order_type → 422."""
        user = _register(client)
        resp = client.post("/accounts/1/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 100,
            "limit_price": 0.5,
            "order_type": "ioc",
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 422

    def test_place_order_nonexistent_account(self, client):
        """Place order on non-existent account → 404."""
        user = _register(client)
        resp = client.post("/accounts/9999/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 100,
            "limit_price": 0.5,
        }, headers=_headers(user["api_key"]))
        assert resp.status_code == 404

    def test_gtd_without_expires_at(self, client):
        """GTD order without expires_at → 400."""
        _, account, headers = _register_and_create_account(client)
        resp = client.post(f"/accounts/{account['id']}/orders", json={
            "market_slug": "test",
            "market_condition_id": "0xabc",
            "outcome": "yes",
            "side": "buy",
            "amount": 100,
            "limit_price": 0.5,
            "order_type": "gtd",
        }, headers=headers)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Orders authorization
# ---------------------------------------------------------------------------

class TestOrdersAuth:
    def test_list_orders_other_users_account(self, client):
        """List orders on another user's account → 403."""
        user1 = _register(client, "orders1")
        user2 = _register(client, "orders2")
        account1 = _create_account(client, user1["api_key"], "acct1")
        resp = client.get(
            f"/accounts/{account1['id']}/orders",
            headers=_headers(user2["api_key"]),
        )
        assert resp.status_code == 403

    def test_list_orders_nonexistent_account(self, client):
        """List orders on non-existent account → 404."""
        user = _register(client)
        resp = client.get(
            "/accounts/9999/orders",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 404

    def test_cancel_order_other_users_account(self, client):
        """Cancel order on another user's account → 403."""
        user1 = _register(client, "cancel1")
        user2 = _register(client, "cancel2")
        account1 = _create_account(client, user1["api_key"], "acct1")
        resp = client.delete(
            f"/accounts/{account1['id']}/orders/1",
            headers=_headers(user2["api_key"]),
        )
        assert resp.status_code == 403

    def test_cancel_order_nonexistent_account(self, client):
        """Cancel order on non-existent account → 404."""
        user = _register(client)
        resp = client.delete(
            "/accounts/9999/orders/1",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Accounts validation
# ---------------------------------------------------------------------------

class TestAccountsValidation:
    def test_create_account_empty_name(self, client):
        """Create account with empty name → 422."""
        user = _register(client)
        resp = client.post(
            "/accounts",
            json={"name": "   "},
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Portfolio authorization
# ---------------------------------------------------------------------------

class TestPortfolioAuth:
    def test_portfolio_other_users_account(self, client):
        """Access another user's portfolio → 403."""
        user1 = _register(client, "port1")
        user2 = _register(client, "port2")
        account1 = _create_account(client, user1["api_key"], "acct1")
        resp = client.get(
            f"/accounts/{account1['id']}/portfolio",
            headers=_headers(user2["api_key"]),
        )
        assert resp.status_code == 403

    def test_portfolio_nonexistent_account(self, client):
        """Access non-existent account portfolio → 404."""
        user = _register(client)
        resp = client.get(
            "/accounts/9999/portfolio",
            headers=_headers(user["api_key"]),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Leaderboard gaps
# ---------------------------------------------------------------------------

class TestLeaderboardGaps:
    def test_leaderboard_account_with_broken_stats(self, client):
        """Account where compute_stats raises → skipped on leaderboard."""
        user = _register(client, "broken-bot")
        account = _create_account(client, user["api_key"], "default")
        _insert_trades(client, account["id"], count=10)
        with patch("server.routes.leaderboard.compute_stats", side_effect=Exception("boom")):
            resp = client.get("/leaderboard")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 0

    def test_head_to_head_no_trades(self, client):
        """head_to_head with accounts that have no trades → stats is None fallback."""
        user = _register(client, "pk-bot")
        headers = _headers(user["api_key"])
        resp1 = client.post("/accounts", json={"name": "a"}, headers=headers)
        resp2 = client.post("/accounts", json={"name": "b"}, headers=headers)
        acc_a = resp1.json()["data"]["id"]
        acc_b = resp2.json()["data"]["id"]
        resp = client.get(f"/leaderboard/pk/{acc_a}/{acc_b}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        # When stats is None, total_value falls back to float(acc["cash"])
        assert data["a"]["total_value"] == 10000.0
        assert data["b"]["total_value"] == 10000.0

    def test_head_to_head_first_account_nonexistent(self, client):
        """head_to_head with first account non-existent → 404."""
        user = _register(client, "pk-miss-a")
        account = _create_account(client, user["api_key"], "default")
        resp = client.get(f"/leaderboard/pk/9999/{account['id']}")
        assert resp.status_code == 404

    def test_head_to_head_second_account_nonexistent(self, client):
        """head_to_head with second account non-existent → 404."""
        user = _register(client, "pk-miss-b")
        account = _create_account(client, user["api_key"], "default")
        resp = client.get(f"/leaderboard/pk/{account['id']}/9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Website gaps
# ---------------------------------------------------------------------------

TIER_CASES = [
    ("diamond", 10, {"total_trades": 55, "roi_pct": 25.0, "sharpe_ratio": 2.0,
                      "pnl": 2500.0, "total_value": 12500.0, "win_rate": 0.7,
                      "max_drawdown": 0.05}),
    ("gold",    10, {"total_trades": 35, "roi_pct": 15.0, "sharpe_ratio": 1.2,
                      "pnl": 1500.0, "total_value": 11500.0, "win_rate": 0.6,
                      "max_drawdown": 0.08}),
    ("silver",  10, {"total_trades": 22, "roi_pct": 8.0,  "sharpe_ratio": 0.5,
                      "pnl": 800.0,  "total_value": 10800.0, "win_rate": 0.55,
                      "max_drawdown": 0.1}),
]


class TestWebsiteGaps:
    @pytest.mark.parametrize("tier,trade_count,stats", TIER_CASES)
    def test_homepage_tier_rendering(self, client, tier, trade_count, stats):
        """Homepage renders accounts at the correct tier."""
        user = _register(client, f"{tier}-bot")
        account = _create_account(client, user["api_key"], "default")
        _insert_trades(client, account["id"], count=trade_count)
        with patch("server.routes.website.compute_stats", return_value=stats):
            resp = client.get("/")
        assert resp.status_code == 200
        assert tier.upper() in resp.text

    def test_homepage_stats_exception_skips_account(self, client):
        """Homepage skips accounts where compute_stats raises."""
        user = _register(client, "crash-bot")
        account = _create_account(client, user["api_key"], "default")
        _insert_trades(client, account["id"], count=10)
        with patch("server.routes.website.compute_stats", side_effect=Exception("boom")):
            resp = client.get("/")
        assert resp.status_code == 200
        assert "No qualified accounts" in resp.text

    def test_user_page_account_no_trades(self, client):
        """User page with account that has no trades → _stats_for_account returns None."""
        user = _register(client, "notrade-bot")
        _create_account(client, user["api_key"], "empty-acct")
        resp = client.get("/u/notrade-bot")
        assert resp.status_code == 200

    def test_account_page_missing_user(self, client):
        """Account page with missing user → shows 'unknown'."""
        user = _register(client, "ghost-bot")
        account = _create_account(client, user["api_key"], "default")
        db = client.app.state.db
        with patch.object(db, "get_user_by_id", return_value=None):
            resp = client.get(f"/a/{account['id']}")
        assert resp.status_code == 200
        assert "unknown" in resp.text


# ---------------------------------------------------------------------------
# Jobs: check_orders error handling
# ---------------------------------------------------------------------------

class TestCheckOrdersGaps:
    def test_api_failure_skips_group(self, jobs_db):
        """When get_market raises, that group is skipped."""
        user = jobs_db.create_user("api-fail-bot")
        account = jobs_db.create_account(user["id"], "default")
        jobs_db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100,
            limit_price=0.50,
        )
        pm = _FailingClient(JOBS_MARKET, JOBS_BOOK)
        count = check_orders_job(jobs_db, pm)
        assert count == 0
        assert len(jobs_db.get_pending_orders(account["id"])) == 1

    def test_buy_fills_into_existing_position(self, jobs_db):
        """Buy limit order fills into existing position → cost averaging."""
        user = jobs_db.create_user("avg-bot")
        account = jobs_db.create_account(user["id"], "default")
        jobs_db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xabc123",
            market_slug="test-market",
            market_question="Test?",
            outcome="yes",
            shares=50,
            avg_entry_price=0.40,
            total_cost=20,
            realized_pnl=0,
        )
        jobs_db.create_limit_order(
            account_id=account["id"],
            market_slug="test-market",
            market_condition_id="0xabc123",
            outcome="yes",
            side="buy",
            amount=100,
            limit_price=0.50,
        )
        pm = MockPolymarketClient(JOBS_MARKET, JOBS_BOOK)
        count = check_orders_job(jobs_db, pm)
        assert count == 1
        pos = jobs_db.get_position(account["id"], "0xabc123", "yes")
        assert pos["shares"] > 50  # Original + new fill

    def test_per_order_exception_continues(self, jobs_db):
        """If one order raises during processing, next order still checked."""
        user = jobs_db.create_user("multi-bot")
        account = jobs_db.create_account(user["id"], "default")
        for _ in range(2):
            jobs_db.create_limit_order(
                account_id=account["id"],
                market_slug="test-market",
                market_condition_id="0xabc123",
                outcome="yes",
                side="buy",
                amount=100,
                limit_price=0.50,
            )

        call_count = 0
        original_get_account = jobs_db.get_account

        def fail_first_get_account(account_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated failure")
            return original_get_account(account_id)

        with patch.object(jobs_db, "get_account", side_effect=fail_first_get_account):
            count = check_orders_job(jobs_db, MockPolymarketClient(JOBS_MARKET, JOBS_BOOK))
        assert count == 1


# ---------------------------------------------------------------------------
# Jobs: auto_resolve error handling
# ---------------------------------------------------------------------------

class TestAutoResolveGaps:
    def test_market_fetch_fails_skips(self, jobs_db, caplog):
        """When get_market raises, that slug is skipped."""
        caplog.set_level(logging.WARNING)
        user = jobs_db.create_user("resolve-fail")
        account = jobs_db.create_account(user["id"], "default")
        jobs_db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xfail",
            market_slug="failing-market",
            market_question="Test?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.5,
            total_cost=50,
            realized_pnl=0,
        )
        pm = _FailingClient()
        count = auto_resolve_job(jobs_db, pm)
        assert count == 0
        assert len(jobs_db.get_open_positions(account["id"])) == 1
        assert "failed to fetch market slug=failing-market" in caplog.text

    def test_no_winning_outcome_skips(self, jobs_db, caplog):
        """Closed market with no outcome >= 0.99 → skip."""
        caplog.set_level(logging.INFO)
        user = jobs_db.create_user("ambig-bot")
        account = jobs_db.create_account(user["id"], "default")
        jobs_db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xambig",
            market_slug="ambiguous-market",
            market_question="Test?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.5,
            total_cost=50,
            realized_pnl=0,
        )
        ambiguous_market = Market(
            condition_id="0xambig",
            slug="ambiguous-market",
            question="Test?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.50, 0.50],  # No clear winner
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
            volume=0,
            liquidity=0,
            end_date="2026-01-01",
            fee_rate_bps=0,
            tick_size=0.01,
        )
        pm = MockPolymarketClient(ambiguous_market, EMPTY_BOOK)
        count = auto_resolve_job(jobs_db, pm)
        assert count == 0
        assert "skipped closed market without clear winner slug=ambiguous-market" in caplog.text

    def test_already_resolved_position_skipped(self, jobs_db):
        """Position marked is_resolved=1 → skipped."""
        user = jobs_db.create_user("resolved-bot")
        account = jobs_db.create_account(user["id"], "default")
        jobs_db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xresolved",
            market_slug="resolved-market",
            market_question="Test?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.5,
            total_cost=50,
            realized_pnl=0,
        )
        jobs_db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xresolved",
            market_slug="resolved-market",
            market_question="Test?",
            outcome="no",
            shares=50,
            avg_entry_price=0.5,
            total_cost=25,
            realized_pnl=0,
        )
        # Resolve YES position manually
        positions = jobs_db.get_all_open_positions()
        yes_pos = [p for p in positions if p["outcome"] == "yes"][0]
        jobs_db.resolve_position(yes_pos["id"], 50.0)

        resolved_market = Market(
            condition_id="0xresolved",
            slug="resolved-market",
            question="Test?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes", "outcome": "Yes"},
                {"token_id": "tok_no", "outcome": "No"},
            ],
            active=False,
            closed=True,
            volume=0,
            liquidity=0,
            end_date="2026-01-01",
            fee_rate_bps=0,
            tick_size=0.01,
        )
        pm = MockPolymarketClient(resolved_market, EMPTY_BOOK)
        # Patch to include the already-resolved YES position so the
        # is_resolved guard in auto_resolve_job's inner loop is exercised.
        all_positions = jobs_db._conn.execute("SELECT * FROM positions").fetchall()
        all_positions_dicts = [dict(r) for r in all_positions]

        with patch.object(jobs_db, "get_all_open_positions", return_value=all_positions_dicts):
            count = auto_resolve_job(jobs_db, pm)
        # Only the NO position should be resolved; YES already resolved
        assert count == 1

    def test_cross_market_positions_filtered(self, jobs_db):
        """Positions for different market slugs are filtered correctly."""
        user = jobs_db.create_user("cross-bot")
        account = jobs_db.create_account(user["id"], "default")
        jobs_db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xmktA",
            market_slug="market-a",
            market_question="Market A?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.5,
            total_cost=50,
            realized_pnl=0,
        )
        jobs_db.upsert_position(
            account_id=account["id"],
            market_condition_id="0xmktB",
            market_slug="market-b",
            market_question="Market B?",
            outcome="yes",
            shares=100,
            avg_entry_price=0.5,
            total_cost=50,
            realized_pnl=0,
        )
        resolved_a = Market(
            condition_id="0xmktA",
            slug="market-a",
            question="Market A?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[1.0, 0.0],
            tokens=[
                {"token_id": "tok_yes_a", "outcome": "Yes"},
                {"token_id": "tok_no_a", "outcome": "No"},
            ],
            active=False,
            closed=True,
            volume=0,
            liquidity=0,
            end_date="2026-01-01",
            fee_rate_bps=0,
            tick_size=0.01,
        )
        active_b = Market(
            condition_id="0xmktB",
            slug="market-b",
            question="Market B?",
            description="",
            outcomes=["Yes", "No"],
            outcome_prices=[0.65, 0.35],
            tokens=[
                {"token_id": "tok_yes_b", "outcome": "Yes"},
                {"token_id": "tok_no_b", "outcome": "No"},
            ],
            active=True,
            closed=False,
            volume=1000000,
            liquidity=100000,
            end_date="2026-12-31",
            fee_rate_bps=0,
            tick_size=0.01,
        )

        class MultiMarketClient(MockPolymarketClient):
            def get_market(self, slug):
                if slug == "market-a":
                    return resolved_a
                return active_b

        pm = MultiMarketClient()
        count = auto_resolve_job(jobs_db, pm)
        assert count == 1
        open_pos = jobs_db.get_open_positions(account["id"])
        assert len(open_pos) == 1
        assert open_pos[0]["market_slug"] == "market-b"
