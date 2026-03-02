-- PostgreSQL schema (also works with SQLite for testing with minor adjustments)
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    agent_name      TEXT UNIQUE NOT NULL,
    api_key         TEXT UNIQUE NOT NULL,
    model           TEXT,
    links           TEXT DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id               SERIAL PRIMARY KEY,
    user_id          INT REFERENCES users(id),
    name             TEXT NOT NULL,
    starting_balance REAL NOT NULL DEFAULT 10000,
    cash             REAL NOT NULL DEFAULT 10000,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS book_snapshots (
    id          SERIAL PRIMARY KEY,
    token_id    TEXT NOT NULL,
    snapshot    TEXT NOT NULL,
    fetched_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trades (
    id                   SERIAL PRIMARY KEY,
    account_id           INT REFERENCES accounts(id),
    market_condition_id  TEXT NOT NULL,
    market_slug          TEXT NOT NULL,
    market_question      TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    side                 TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type           TEXT NOT NULL DEFAULT 'fok',
    avg_price            REAL NOT NULL,
    amount_usd           REAL NOT NULL,
    shares               REAL NOT NULL,
    fee_rate_bps         INT NOT NULL,
    fee                  REAL NOT NULL DEFAULT 0,
    slippage             REAL NOT NULL DEFAULT 0,
    levels_filled        INT NOT NULL DEFAULT 1,
    is_partial           BOOLEAN NOT NULL DEFAULT 0,
    book_snapshot_id     INT REFERENCES book_snapshots(id),
    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id                   SERIAL PRIMARY KEY,
    account_id           INT REFERENCES accounts(id),
    market_condition_id  TEXT NOT NULL,
    market_slug          TEXT NOT NULL,
    market_question      TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    shares               REAL NOT NULL DEFAULT 0,
    avg_entry_price      REAL NOT NULL DEFAULT 0,
    total_cost           REAL NOT NULL DEFAULT 0,
    realized_pnl         REAL NOT NULL DEFAULT 0,
    is_resolved          BOOLEAN NOT NULL DEFAULT 0,
    resolved_at          TIMESTAMP,
    UNIQUE(account_id, market_condition_id, outcome)
);

CREATE TABLE IF NOT EXISTS limit_orders (
    id                   SERIAL PRIMARY KEY,
    account_id           INT REFERENCES accounts(id),
    market_slug          TEXT NOT NULL,
    market_condition_id  TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    side                 TEXT NOT NULL,
    amount               REAL NOT NULL,
    limit_price          REAL NOT NULL,
    order_type           TEXT NOT NULL DEFAULT 'gtc',
    expires_at           TIMESTAMP,
    status               TEXT NOT NULL DEFAULT 'pending',
    filled_at            TIMESTAMP,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
