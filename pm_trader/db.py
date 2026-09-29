"""SQLite database layer for pm-trader."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pm_trader.models import Account, Position, Trade


SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY DEFAULT 1,
    starting_balance REAL NOT NULL DEFAULT 10000,
    cash REAL NOT NULL DEFAULT 10000,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (length(outcome) > 0),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL DEFAULT 'fok' CHECK (order_type IN ('fok', 'fak')),
    avg_price REAL NOT NULL,
    amount_usd REAL NOT NULL,
    shares REAL NOT NULL,
    fee_rate_bps INTEGER NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    slippage REAL NOT NULL DEFAULT 0,
    levels_filled INTEGER NOT NULL DEFAULT 1,
    is_partial INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    market_condition_id TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (length(outcome) > 0),
    shares REAL NOT NULL DEFAULT 0,
    avg_entry_price REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    is_resolved INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT,
    PRIMARY KEY (market_condition_id, outcome)
);

CREATE TABLE IF NOT EXISTS market_cache (
    cache_key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    """SQLite database for pm-trader paper trading state."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db_path = data_dir / "paper.db"
        self._txn_depth = 0
        self._ensure_dir()
        self._conn: sqlite3.Connection | None = None

    def _ensure_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _commit(self) -> None:
        """Commit unless an atomic() block owns the transaction."""
        if self._txn_depth == 0:
            self.conn.commit()

    @contextmanager
    def atomic(self):
        """Group every write inside the block into one transaction.

        buy()/sell() debit cash, insert a trade row and update a position as
        three independent commits — a crash between them left the cash
        debited with no position (issue #69). Inside this block the
        per-method commits are suppressed and a single COMMIT (or ROLLBACK
        on error) lands the whole sequence atomically. Nesting is refused:
        no call path needs it and silently allowing it would mask bugs.
        """
        if self._txn_depth > 0:
            raise RuntimeError("atomic() blocks do not nest")
        self._txn_depth = 1
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        finally:
            self._txn_depth = 0

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """Create all tables if they don't exist."""
        self.conn.executescript(SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def init_account(self, balance: float = 10000.0) -> Account:
        """Initialize the paper trading account. Returns the Account."""
        self.conn.execute(
            "INSERT OR REPLACE INTO account (id, starting_balance, cash) VALUES (1, ?, ?)",
            (balance, balance),
        )
        self._commit()
        return self.get_account()

    def get_account(self) -> Account | None:
        """Return the account, or None if not initialized."""
        row = self.conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
        if row is None:
            return None
        return Account(
            id=row["id"],
            starting_balance=row["starting_balance"],
            cash=row["cash"],
            created_at=row["created_at"],
        )

    def update_cash(self, new_cash: float) -> None:
        """Update the account cash balance."""
        self.conn.execute("UPDATE account SET cash = ? WHERE id = 1", (new_cash,))
        self._commit()

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Drop all data and re-create schema."""
        self.conn.executescript(
            """\
            DROP TABLE IF EXISTS trades;
            DROP TABLE IF EXISTS positions;
            DROP TABLE IF EXISTS account;
            DROP TABLE IF EXISTS market_cache;
            DROP TABLE IF EXISTS limit_orders;
            """
        )
        self.init_schema()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def insert_trade(
        self,
        *,
        market_condition_id: str,
        market_slug: str,
        market_question: str,
        outcome: str,
        side: str,
        order_type: str,
        avg_price: float,
        amount_usd: float,
        shares: float,
        fee_rate_bps: int,
        fee: float,
        slippage: float,
        levels_filled: int,
        is_partial: bool,
    ) -> Trade:
        """Insert a trade and return the Trade object."""
        cursor = self.conn.execute(
            """\
            INSERT INTO trades (
                market_condition_id, market_slug, market_question,
                outcome, side, order_type,
                avg_price, amount_usd, shares,
                fee_rate_bps, fee, slippage,
                levels_filled, is_partial
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_condition_id, market_slug, market_question,
                outcome, side, order_type,
                avg_price, amount_usd, shares,
                fee_rate_bps, fee, slippage,
                levels_filled, int(is_partial),
            ),
        )
        self._commit()
        trade_id = cursor.lastrowid
        row = self.conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        return _row_to_trade(row)

    def get_trades(self, limit: int = 50) -> list[Trade]:
        """Return recent trades, newest first."""
        rows = self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_trade(row) for row in rows]

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def upsert_position(
        self,
        *,
        market_condition_id: str,
        market_slug: str,
        market_question: str,
        outcome: str,
        shares: float,
        avg_entry_price: float,
        total_cost: float,
        realized_pnl: float = 0.0,
    ) -> Position:
        """Insert or update a position and return it."""
        self.conn.execute(
            """\
            INSERT INTO positions (
                market_condition_id, market_slug, market_question,
                outcome, shares, avg_entry_price, total_cost, realized_pnl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (market_condition_id, outcome) DO UPDATE SET
                shares = excluded.shares,
                avg_entry_price = excluded.avg_entry_price,
                total_cost = excluded.total_cost,
                realized_pnl = excluded.realized_pnl
            """,
            (
                market_condition_id, market_slug, market_question,
                outcome, shares, avg_entry_price, total_cost, realized_pnl,
            ),
        )
        self._commit()
        return self.get_position(market_condition_id, outcome)

    def get_position(self, market_condition_id: str, outcome: str) -> Position | None:
        """Return a specific position, or None."""
        row = self.conn.execute(
            "SELECT * FROM positions WHERE market_condition_id = ? AND outcome = ?",
            (market_condition_id, outcome),
        ).fetchone()
        if row is None:
            return None
        return _row_to_position(row)

    def get_open_positions(self) -> list[Position]:
        """Return all open (unresolved) positions with shares > 0."""
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE is_resolved = 0 AND shares > 0"
        ).fetchall()
        return [_row_to_position(row) for row in rows]

    def get_positions_for_market(self, market_condition_id: str) -> list[Position]:
        """Return all positions for a given market (YES and NO)."""
        rows = self.conn.execute(
            "SELECT * FROM positions WHERE market_condition_id = ?",
            (market_condition_id,),
        ).fetchall()
        return [_row_to_position(row) for row in rows]

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve_position(
        self,
        market_condition_id: str,
        outcome: str,
        payout: float,
    ) -> Position:
        """Mark a position as resolved and record the realized P&L from payout."""
        position = self.get_position(market_condition_id, outcome)
        if position is None:
            raise ValueError(
                f"No position for {market_condition_id}/{outcome}"
            )
        new_realized = position.realized_pnl + payout - position.total_cost
        self.conn.execute(
            """\
            UPDATE positions SET
                is_resolved = 1,
                resolved_at = datetime('now'),
                realized_pnl = ?,
                shares = 0
            WHERE market_condition_id = ? AND outcome = ?
            """,
            (new_realized, market_condition_id, outcome),
        )
        self._commit()
        return self.get_position(market_condition_id, outcome)

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def set_cache(self, key: str, data: dict | list) -> None:
        """Store a JSON-serializable value in the cache."""
        self.conn.execute(
            """\
            INSERT OR REPLACE INTO market_cache (cache_key, data, fetched_at)
            VALUES (?, ?, datetime('now'))
            """,
            (key, json.dumps(data)),
        )
        self._commit()

    def get_cache(self, key: str) -> dict | list | None:
        """Return cached data, or None if not found."""
        row = self.conn.execute(
            "SELECT data FROM market_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["data"])


# ---------------------------------------------------------------------------
# Row conversion helpers
# ---------------------------------------------------------------------------

def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"],
        market_condition_id=row["market_condition_id"],
        market_slug=row["market_slug"],
        market_question=row["market_question"],
        outcome=row["outcome"],
        side=row["side"],
        order_type=row["order_type"],
        avg_price=row["avg_price"],
        amount_usd=row["amount_usd"],
        shares=row["shares"],
        fee_rate_bps=row["fee_rate_bps"],
        fee=row["fee"],
        slippage=row["slippage"],
        levels_filled=row["levels_filled"],
        is_partial=bool(row["is_partial"]),
        created_at=row["created_at"],
    )


def _row_to_position(row: sqlite3.Row) -> Position:
    return Position(
        market_condition_id=row["market_condition_id"],
        market_slug=row["market_slug"],
        market_question=row["market_question"],
        outcome=row["outcome"],
        shares=row["shares"],
        avg_entry_price=row["avg_entry_price"],
        total_cost=row["total_cost"],
        realized_pnl=row["realized_pnl"],
        is_resolved=bool(row["is_resolved"]),
        resolved_at=row["resolved_at"],
    )
