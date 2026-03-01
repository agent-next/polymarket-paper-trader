# polymarket-paper-trader

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-597%20passed-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)]()
[![ClawHub](https://img.shields.io/badge/ClawHub-polymarket--paper--trader-orange.svg)](https://clawhub.com/robotlearning123/polymarket-paper-trader)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Paper trading simulator for [Polymarket](https://polymarket.com), built for AI agents.

Executes trades against **live Polymarket order books** without risking real money. Walks the book level-by-level, calculates exact fees and slippage, and tracks P&L in a local SQLite database.

Part of [agent-next](https://github.com/agent-next) — open research lab for self-evolving autonomous agents.

## 60-second demo

```bash
npx clawhub install polymarket-paper-trader    # install via ClawHub
pm-trader init --balance 10000                 # $10k paper money
pm-trader markets search "bitcoin"             # find markets
pm-trader buy will-bitcoin-hit-100k yes 500    # buy $500 of YES
pm-trader stats --card                         # shareable stats card
```

That's it. Your AI agent is now trading Polymarket with zero risk.

## Install

```bash
# via ClawHub (for OpenClaw agents)
npx clawhub install polymarket-paper-trader

# via pip (for direct use)
uv pip install -e .

# dev dependencies (tests)
uv pip install -e ".[dev]"
```

Requires Python 3.10+.

## Quick start

```bash
# Initialize with $10k paper balance
pm-trader init --balance 10000

# Browse markets
pm-trader markets list --sort liquidity
pm-trader markets search "bitcoin"

# Trade
pm-trader buy will-bitcoin-hit-100k yes 100      # buy $100 of YES
pm-trader sell will-bitcoin-hit-100k yes 50       # sell 50 shares

# Check portfolio and P&L
pm-trader portfolio
pm-trader stats
```

## CLI commands

| Command | Description |
|---------|-------------|
| `init [--balance N]` | Create paper trading account |
| `balance` | Show cash, positions value, total P&L |
| `reset --confirm` | Wipe all data |
| `markets list [--limit N] [--sort volume\|liquidity]` | Browse active markets |
| `markets search QUERY` | Full-text market search |
| `markets get SLUG` | Market details |
| `price SLUG` | YES/NO midpoints and spread |
| `book SLUG [--depth N]` | Order book snapshot |
| `watch SLUG [SLUG...] [--outcome yes\|no]` | Monitor live prices |
| `buy SLUG OUTCOME AMOUNT [--type fok\|fak]` | Buy shares (walks ask side) |
| `sell SLUG OUTCOME SHARES [--type fok\|fak]` | Sell shares (walks bid side) |
| `portfolio` | Open positions with live prices |
| `history [--limit N]` | Trade history |
| `orders place SLUG OUTCOME SIDE AMOUNT PRICE` | Limit order (GTC/GTD) |
| `orders list` | Pending limit orders |
| `orders cancel ID` | Cancel a limit order |
| `orders check` | Fill limit orders if price crosses |
| `stats` | Sharpe ratio, win rate, max drawdown, ROI |
| `export trades [--format csv\|json]` | Export trade history |
| `export positions [--format csv\|json]` | Export positions |
| `benchmark run MODULE.FUNC` | Run a trading strategy |
| `benchmark compare ACCT1 ACCT2` | Compare account performance |
| `benchmark pk STRAT_A STRAT_B` | Run two strategies head-to-head |
| `accounts list` | List named accounts |
| `accounts create NAME` | Create account for A/B testing |
| `mcp` | Start MCP server (stdio transport) |

Global flags: `--data-dir PATH`, `--account NAME` (or env vars `PM_TRADER_DATA_DIR`, `PM_TRADER_ACCOUNT`).

## MCP server

Exposes 26 tools via the [Model Context Protocol](https://modelcontextprotocol.io) for direct AI agent integration:

```bash
pm-trader-mcp  # starts on stdio
```

Add to your Claude Code config:

```json
{
  "mcpServers": {
    "polymarket-paper-trader": {
      "command": "pm-trader-mcp"
    }
  }
}
```

### MCP tools

| Tool | Purpose |
|------|---------|
| `init_account` | Create paper account with starting balance |
| `get_balance` | Cash, positions value, total P&L |
| `reset_account` | Wipe all data and start fresh |
| `search_markets` | Full-text search for markets |
| `list_markets` | Browse markets sorted by volume/liquidity |
| `get_market` | Detailed market info with outcomes and prices |
| `get_order_book` | Live order book snapshot (bids + asks) |
| `watch_prices` | Monitor midpoint prices for multiple markets |
| `buy` | Buy outcome shares — walks ask side (FOK or FAK) |
| `sell` | Sell outcome shares — walks bid side (FOK or FAK) |
| `portfolio` | Open positions with live valuations and P&L |
| `history` | Recent trade log with execution details |
| `place_limit_order` | GTC/GTD limit order at target price |
| `list_orders` | Pending limit orders |
| `cancel_order` | Cancel a pending order |
| `check_orders` | Execute pending orders against live prices |
| `stats` | Performance analytics (Sharpe, win rate, drawdown) |
| `resolve` | Resolve a closed market (winners get $1/share) |
| `resolve_all` | Resolve all closed markets |
| `backtest` | Backtest a strategy against historical snapshots |
| `stats_card` | Shareable stats card (tweet/markdown/plain) |
| `share_content` | Platform-specific content (twitter/telegram/discord) |
| `leaderboard_entry` | Generate verifiable leaderboard submission |
| `leaderboard_card` | Top 10 ranking card from all local accounts |
| `pk_card` | Head-to-head comparison between two accounts |
| `pk_battle` | Run two strategies head-to-head, auto-compare |

## Strategy examples

Three ready-to-use strategies in `examples/`:

### Momentum (`examples/momentum.py`)

Buys when YES price crosses above 0.55, takes profit at 0.70, stops loss at 0.35.

```bash
pm-trader benchmark run examples.momentum.run
```

### Mean reversion (`examples/mean_reversion.py`)

Buys when YES price drops 12+ cents below 0.50 fair value, sells when it reverts.

```bash
pm-trader benchmark run examples.mean_reversion.run
```

### Limit grid (`examples/limit_grid.py`)

Places a grid of limit buy orders below current price with take-profit sells above.

```bash
pm-trader benchmark run examples.limit_grid.run
```

### Writing your own strategy

```python
# my_strategy.py
from pm_trader.engine import Engine

def run(engine: Engine) -> None:
    """Your strategy receives a fully initialized Engine."""
    markets = engine.api.search_markets("crypto")
    for market in markets:
        if market.closed or market.yes_price < 0.3:
            continue
        engine.buy(market.slug, "yes", 100.0)
```

```bash
pm-trader benchmark run my_strategy.run
```

For backtesting with historical data:

```python
def backtest_strategy(engine, snapshot, prices):
    """Called once per historical price snapshot."""
    if snapshot.midpoint > 0.6:
        engine.buy(snapshot.market_slug, snapshot.outcome, 50.0)
```

## How it works

1. **Live order books** — Fetches real-time asks/bids from the Polymarket CLOB API
2. **Level-by-level execution** — Walks the book like a real order, consuming liquidity at each price level
3. **Exact fee model** — Polymarket's formula: `(bps/10000) * min(price, 1-price) * shares`
4. **Slippage tracking** — Records deviation from midpoint in basis points
5. **Order types** — FOK (fill-or-kill), FAK (fill-and-kill / partial), limit GTC/GTD

All state lives in `~/.pm-trader/<account>/paper.db` (SQLite, WAL mode).

## Multi-account support

Run parallel strategies with isolated accounts:

```bash
pm-trader --account aggressive init --balance 5000
pm-trader --account conservative init --balance 5000

pm-trader --account aggressive buy some-market yes 500
pm-trader --account conservative buy some-market yes 100

pm-trader benchmark compare aggressive conservative
```

## Analytics

```bash
pm-trader stats
```

Returns: Sharpe ratio (sample variance), win rate (cost-averaged), max drawdown, ROI%, total P&L, trade counts, fee totals, average trade size.

## Project structure

```
pm_trader/
  cli.py          # Click CLI (30+ commands)
  mcp_server.py   # FastMCP server (26 tools)
  engine.py       # Core orchestration
  api.py          # Polymarket HTTP client (Gamma + CLOB APIs)
  orderbook.py    # Order book simulation engine
  orders.py       # Limit order state machine
  analytics.py    # Performance metrics
  backtest.py     # Historical replay engine
  benchmark.py    # Strategy runner & comparison
  db.py           # SQLite persistence layer
  models.py       # Dataclasses and error types
  export.py       # CSV/JSON export
examples/
  momentum.py     # Momentum strategy (buy breakout, stop loss)
  mean_reversion.py  # Mean reversion (buy dips near fair value)
  limit_grid.py   # Grid trading (limit orders at multiple levels)
```

## Share your results

Generate a shareable stats card and post to X/Twitter:

```bash
pm-trader stats --tweet    # X/Twitter optimized (< 280 chars)
pm-trader stats --card     # markdown for Telegram/Discord
pm-trader stats --plain    # plain text
```

Example output:

```
🚀 My AI agent's Polymarket results:

ROI: +18.5%
P&L: +$1,850
Sharpe: 1.42 | Win: 68% | 23 trades

Paper trading with real order books, zero risk

#Polymarket #AITrading #PredictionMarkets
npx clawhub install polymarket-paper-trader
```

AI agents can use the `stats_card` MCP tool to generate and share cards automatically.

## Tests

```bash
pytest                           # 597 tests, 100% coverage
pytest tests/test_e2e_live.py    # live API integration tests
```

## OpenClaw / ClawHub

Available on [ClawHub](https://clawhub.com) as `polymarket-paper-trader`:

```bash
npx clawhub install polymarket-paper-trader
```

## License

MIT
