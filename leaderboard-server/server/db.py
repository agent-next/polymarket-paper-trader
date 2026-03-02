"""Database layer. SQLite for dev/test, asyncpg for production.

Multi-tenant: every query scoped by user_id or account_id.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path


class DB:
    """Synchronous SQLite backend for dev/test. Production uses asyncpg."""

    def __init__(self, database_url: str = ":memory:"):
        self._url = database_url
        self._conn: sqlite3.Connection | None = None

    def init_schema(self) -> None:
        self._conn = sqlite3.connect(self._url, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        schema = (Path(__file__).parent / "schema.sql").read_text()
        # SQLite doesn't support SERIAL, replace with INTEGER PRIMARY KEY AUTOINCREMENT
        schema = schema.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        # Execute each statement separately
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    # -- Users --

    def create_user(self, agent_name: str, model: str | None = None) -> dict:
        api_key = f"lb_sk_{secrets.token_urlsafe(32)}"
        cur = self._conn.execute(
            "INSERT INTO users (agent_name, api_key, model) VALUES (?, ?, ?) RETURNING *",
            (agent_name, api_key, model),
        )
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    def update_model(self, user_id: int, model: str) -> dict:
        cur = self._conn.execute(
            "UPDATE users SET model = ? WHERE id = ? RETURNING *",
            (model, user_id),
        )
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    def get_user_by_api_key(self, api_key: str) -> dict | None:
        cur = self._conn.execute("SELECT * FROM users WHERE api_key = ?", (api_key,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, agent_name: str) -> dict | None:
        cur = self._conn.execute("SELECT * FROM users WHERE agent_name = ?", (agent_name,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        cur = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    # -- Accounts --

    def create_account(self, user_id: int, name: str) -> dict:
        cur = self._conn.execute(
            "INSERT INTO accounts (user_id, name) VALUES (?, ?) RETURNING *",
            (user_id, name),
        )
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    def get_account(self, account_id: int) -> dict | None:
        cur = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_user_accounts(self, user_id: int) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM accounts WHERE user_id = ? ORDER BY created_at", (user_id,)
        )
        return [dict(r) for r in cur.fetchall()]

    def update_cash(self, account_id: int, cash: float) -> None:
        self._conn.execute("UPDATE accounts SET cash = ? WHERE id = ?", (cash, account_id))
        self._conn.commit()

    # -- Trades --

    def insert_trade(self, *, account_id: int, market_condition_id: str,
                     market_slug: str, market_question: str, outcome: str,
                     side: str, order_type: str, avg_price: float,
                     amount_usd: float, shares: float, fee_rate_bps: int,
                     fee: float, slippage: float, levels_filled: int,
                     is_partial: bool, book_snapshot_id: int | None) -> dict:
        cur = self._conn.execute("""
            INSERT INTO trades (account_id, market_condition_id, market_slug,
                market_question, outcome, side, order_type, avg_price,
                amount_usd, shares, fee_rate_bps, fee, slippage,
                levels_filled, is_partial, book_snapshot_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            RETURNING *
        """, (account_id, market_condition_id, market_slug, market_question,
              outcome, side, order_type, avg_price, amount_usd, shares,
              fee_rate_bps, fee, slippage, levels_filled, is_partial,
              book_snapshot_id))
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    def get_trades(self, account_id: int, limit: int = 50) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM trades WHERE account_id = ? ORDER BY id DESC LIMIT ?",
            (account_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_trade_count(self, account_id: int) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM trades WHERE account_id = ?", (account_id,)
        )
        return cur.fetchone()[0]

    # -- Positions --

    def upsert_position(self, *, account_id: int, market_condition_id: str,
                        market_slug: str, market_question: str, outcome: str,
                        shares: float, avg_entry_price: float, total_cost: float,
                        realized_pnl: float) -> dict:
        # Try update first, then insert
        cur = self._conn.execute(
            """SELECT id FROM positions
               WHERE account_id = ? AND market_condition_id = ? AND outcome = ?""",
            (account_id, market_condition_id, outcome),
        )
        existing = cur.fetchone()
        if existing:
            cur = self._conn.execute("""
                UPDATE positions SET shares=?, avg_entry_price=?, total_cost=?, realized_pnl=?
                WHERE account_id=? AND market_condition_id=? AND outcome=?
                RETURNING *
            """, (shares, avg_entry_price, total_cost, realized_pnl,
                  account_id, market_condition_id, outcome))
        else:
            cur = self._conn.execute("""
                INSERT INTO positions (account_id, market_condition_id, market_slug,
                    market_question, outcome, shares, avg_entry_price, total_cost, realized_pnl)
                VALUES (?,?,?,?,?,?,?,?,?)
                RETURNING *
            """, (account_id, market_condition_id, market_slug, market_question,
                  outcome, shares, avg_entry_price, total_cost, realized_pnl))
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    def get_position(self, account_id: int, market_condition_id: str, outcome: str) -> dict | None:
        cur = self._conn.execute(
            """SELECT * FROM positions
               WHERE account_id = ? AND market_condition_id = ? AND outcome = ?""",
            (account_id, market_condition_id, outcome),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_open_positions(self, account_id: int) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM positions WHERE account_id = ? AND shares > 0 AND is_resolved = 0",
            (account_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_all_open_positions(self) -> list[dict]:
        """Get all open positions across all accounts (for auto-resolve job)."""
        cur = self._conn.execute(
            "SELECT * FROM positions WHERE shares > 0 AND is_resolved = 0"
        )
        return [dict(r) for r in cur.fetchall()]

    def resolve_position(self, position_id: int, payout: float) -> dict:
        cur = self._conn.execute("""
            UPDATE positions SET is_resolved = 1, resolved_at = CURRENT_TIMESTAMP,
                realized_pnl = realized_pnl + ?
            WHERE id = ?
            RETURNING *
        """, (payout, position_id))
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    # -- Book snapshots --

    def save_book_snapshot(self, token_id: str, snapshot: dict) -> int:
        cur = self._conn.execute(
            "INSERT INTO book_snapshots (token_id, snapshot) VALUES (?, ?) RETURNING id",
            (token_id, json.dumps(snapshot)),
        )
        row = cur.fetchone()
        self._conn.commit()
        return row[0]

    # -- Limit orders --

    def create_limit_order(self, *, account_id: int, market_slug: str,
                           market_condition_id: str, outcome: str, side: str,
                           amount: float, limit_price: float,
                           order_type: str = "gtc",
                           expires_at: str | None = None) -> dict:
        cur = self._conn.execute("""
            INSERT INTO limit_orders (account_id, market_slug, market_condition_id,
                outcome, side, amount, limit_price, order_type, expires_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            RETURNING *
        """, (account_id, market_slug, market_condition_id, outcome, side,
              amount, limit_price, order_type, expires_at))
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    def get_pending_orders(self, account_id: int | None = None) -> list[dict]:
        if account_id is not None:
            cur = self._conn.execute(
                "SELECT * FROM limit_orders WHERE account_id = ? AND status = 'pending' ORDER BY created_at",
                (account_id,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM limit_orders WHERE status = 'pending' ORDER BY created_at"
            )
        return [dict(r) for r in cur.fetchall()]

    def fill_order(self, order_id: int) -> dict:
        cur = self._conn.execute("""
            UPDATE limit_orders SET status = 'filled', filled_at = CURRENT_TIMESTAMP
            WHERE id = ?
            RETURNING *
        """, (order_id,))
        row = cur.fetchone()
        self._conn.commit()
        return dict(row)

    def cancel_order(self, order_id: int) -> dict | None:
        cur = self._conn.execute("""
            UPDATE limit_orders SET status = 'cancelled'
            WHERE id = ? AND status = 'pending'
            RETURNING *
        """, (order_id,))
        row = cur.fetchone()
        self._conn.commit()
        return dict(row) if row else None

    def expire_orders(self, before: str) -> int:
        cur = self._conn.execute("""
            UPDATE limit_orders SET status = 'expired'
            WHERE status = 'pending' AND order_type = 'gtd' AND expires_at <= ?
        """, (before,))
        self._conn.commit()
        return cur.rowcount

    # -- Leaderboard --

    def get_leaderboard_accounts(self, min_trades: int = 10) -> list[dict]:
        cur = self._conn.execute("""
            SELECT a.*, u.agent_name, u.model, COUNT(t.id) as trade_count
            FROM accounts a
            JOIN users u ON a.user_id = u.id
            LEFT JOIN trades t ON t.account_id = a.id
            GROUP BY a.id
            HAVING COUNT(t.id) >= ?
            ORDER BY a.id
        """, (min_trades,))
        return [dict(r) for r in cur.fetchall()]

    def get_all_accounts_with_user(self) -> list[dict]:
        cur = self._conn.execute("""
            SELECT a.*, u.agent_name, u.model
            FROM accounts a
            JOIN users u ON a.user_id = u.id
            ORDER BY a.id
        """)
        return [dict(r) for r in cur.fetchall()]

    def get_recent_trades_global(self, limit: int = 20) -> list[dict]:
        """Recent trades across all accounts with agent names."""
        cur = self._conn.execute("""
            SELECT t.*, u.agent_name, a.name as account_name
            FROM trades t
            JOIN accounts a ON t.account_id = a.id
            JOIN users u ON a.user_id = u.id
            ORDER BY t.id DESC
            LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]
