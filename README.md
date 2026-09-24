# polymarket-paper-trader

[![PyPI](https://img.shields.io/pypi/v/polymarket-paper-trader.svg)](https://pypi.org/project/polymarket-paper-trader/)
[![Tests](https://github.com/agent-next/polymarket-paper-trader/actions/workflows/test.yml/badge.svg)](https://github.com/agent-next/polymarket-paper-trader/actions/workflows/test.yml)
[![ClawHub](https://img.shields.io/badge/ClawHub-install-orange.svg)](https://clawhub.com/robotlearning123/polymarket-paper-trader)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/agent-next/polymarket-paper-trader/blob/main/LICENSE)

**A zero-risk gym for AI agents on real prediction markets — real order books, official fees, verified fill fidelity. Practice, evaluate, and benchmark decision intelligence.**

Agents make probability judgments all day. Polymarket is the world's largest prediction market, and its order books are the honest scoreboard: real money, real prices, real outcomes. But you cannot hand an agent a wallet to learn with. So this project gives every agent what SWE-bench gave coders — a faithful environment where judgment has consequences and gets scored:

- **Practice** — your agent trades $10k of paper money against live Polymarket order books, with the same fee model and fill mechanics as the real exchange
- **Evaluate** — the bundled benchmark harness scores any model on prediction-market decision sets (Brier score, calibration, alpha)
- **Compare** — multi-account battles and leaderboards rank agents against each other

Part of [agent-next](https://github.com/agent-next) — building an agentic world.

## 60-second demo

```bash
npx clawhub install polymarket-paper-trader    # install via ClawHub
pm-trader init --balance 10000                 # $10k paper money
pm-trader markets search "bitcoin"             # find markets
pm-trader buy will-bitcoin-hit-100k yes 500    # buy $500 of YES
pm-trader stats --card                         # shareable stats card
```

That's it. Your AI agent is now trading Polymarket with zero risk.

## How it works (from the user's point of view)

- **Your order walks the real book.** A buy consumes live ask levels from the lowest price upward, exactly like a real taker order. A $500 order into a thin book fills at worse prices than a $10 order — that's slippage, and you see it in basis points
- **Fees are the official curve.** Polymarket charges takers `fee = C × rate × p × (1-p)` per match (C = shares, p = price), rounded to 5 decimals, with makers always exempt. We charge the same, per filled level, using each market's published fee schedule — not an approximation
- **Paper cash, real discipline.** Resting buy orders reserve their cash, partial fills keep their remainder open, closed or paused markets reject trades, and limit prices are validated against tick size
- **Resolution pays $1/share.** When a market closes, call `resolve` and winning positions pay out like the real thing
- **Prices are never cached.** Order books and midpoints are fetched live on every trade; market metadata is cached 5 minutes

## Why trust the simulation

Other tools mock prices or roll dice. We simulate the actual exchange, and we test the simulation against the real thing:

- **Level-by-level order book execution** against live Polymarket books
- **Bias-tested against real market data**: our live test suite asserts that simulated fills land inside the band of prices the market actually quoted (Data API v2 price history) and that fees match the official curve exactly — run weekly against real APIs on CI
- **Upstream-aligned**: Gamma keyset pagination, CLOB market data, Data API v2 price history — the client tracks the current official API surface, contract-verified by live probes
- **823 tests, 100% coverage** on the core package, plus 44 end-to-end tests against the live API
- **Multi-outcome markets** — not just YES/NO, any number of outcomes

## Install

```bash
# via pip
pip install polymarket-paper-trader

# via ClawHub (for OpenClaw agents)
npx clawhub install polymarket-paper-trader

# from source (development)
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
| `balance` | Show cash, reserved/available cash, positions value, total P&L |
| `reset --confirm` | Wipe all data |
| `markets list [--limit N] [--sort volume\|liquidity]` | Browse active markets |
| `markets search QUERY` | Full-text market search |
| `markets get SLUG` | Market details |
| `price SLUG` | YES/NO midpoints and spread |
| `book SLUG [--depth N]` | Order book snapshot |
| `watch SLUG [SLUG...] [--outcome yes\|no]` | Monitor live prices |
| `buy SLUG OUTCOME AMOUNT [--type fok\|fak]` | Buy at market price |
| `sell SLUG OUTCOME SHARES [--type fok\|fak]` | Sell at market price |
| `portfolio` | Open positions with live prices |
| `history [--limit N]` | Trade history |
| `orders place SLUG OUTCOME SIDE AMOUNT PRICE` | Limit order (GTC/GTD) |
| `orders list` | Open limit orders (pending and partially filled) |
| `orders cancel ID` | Cancel a limit order |
| `orders check` | Fill limit orders if price crosses |
| `stats [--card\|--tweet\|--plain]` | Win rate, ROI, profit, max drawdown |
| `leaderboard` | Local account rankings |
| `pk ACCOUNT_A ACCOUNT_B` | Battle: who's the better trader? |
| `export trades [--format csv\|json]` | Export trade history |
| `export positions [--format csv\|json]` | Export positions |
| `benchmark run MODULE.FUNC` | Run a trading strategy |
| `benchmark compare ACCT1 ACCT2` | Compare account performance |
| `benchmark pk STRAT_A STRAT_B` | Battle: who's the better trader? |
| `accounts list` | List named accounts |
| `accounts create NAME` | Create account for A/B testing |
| `mcp` | Start MCP server (stdio transport) |

Global flags: `--data-dir PATH`, `--account NAME` (or env vars `PM_TRADER_DATA_DIR`, `PM_TRADER_ACCOUNT`).

## MCP server — what your agent can do

Your agent gets the following tools via the [Model Context Protocol](https://modelcontextprotocol.io):

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

| Tool | What it does |
|------|---------|
| `init_account` | Create paper account with starting balance |
| `get_balance` | Cash, reserved/available cash, positions value, total P&L |
| `reset_account` | Wipe all data and start fresh |
| `search_markets` | Find markets by keyword |
| `list_markets` | Browse markets sorted by volume/liquidity |
| `get_tags` | All market categories/tags for filtering |
| `get_markets_by_tag` | Markets in a specific category/tag |
| `get_event` | Event details — a group of related markets |
| `get_market` | Market details with outcomes and prices |
| `get_order_book` | Live order book snapshot (bids + asks) |
| `watch_prices` | Monitor prices for multiple markets |
| `buy` | Buy shares at best available prices |
| `sell` | Sell shares at best available prices |
| `portfolio` | Open positions with live valuations and P&L |
| `history` | Recent trade log with execution details |
| `place_limit_order` | Limit order — stays open until filled or cancelled/expired |
| `list_orders` | Pending limit orders |
| `cancel_order` | Cancel a pending order |
| `cancel_all_orders` | Cancel all pending limit orders at once |
| `check_orders` | Execute pending orders against live prices |
| `stats` | Win rate, ROI, profit, max drawdown |
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

## Evaluate your agent: the benchmark harness

The paper trader is the gym; the bundled `benchmark` package is the scoreboard. It scores any litellm model — or TypeSafe's Jev decision model — on prediction-market sets:

```bash
pip install -e "benchmark[dev]"
cd benchmark && polymarket-benchmark run --model opencode/jev-1.13-free --market-set mini
```

Metrics: Brier score, calibration, alpha vs the market. Run two models head-to-head to see whose judgment is actually better.

## Multi-account support

Run parallel strategies with isolated accounts:

```bash
pm-trader --account aggressive init --balance 5000
pm-trader --account conservative init --balance 5000

pm-trader --account aggressive buy some-market yes 500
pm-trader --account conservative buy some-market yes 100

pm-trader benchmark compare aggressive conservative
```

## Share your results

Generate a shareable stats card and post to X/Twitter:

```bash
pm-trader stats --tweet    # X/Twitter optimized
pm-trader stats --card     # markdown for Telegram/Discord
pm-trader stats --plain    # plain text
```

AI agents can use the `stats_card` MCP tool to generate and share cards automatically.

## Honest limits

- **Paper only.** No wallet, no keys, no real trades, no real money — ever. Resolution payouts are simulated $1/share
- Simulation quality is verified against live order books and price history, but real execution adds queue position, latency, and counterparty behavior no simulator can promise
- Live market data needs network access to Polymarket's public APIs (no key required)

## OpenClaw / ClawHub

Available on [ClawHub](https://clawhub.com) as `polymarket-paper-trader`:

```bash
npx clawhub install polymarket-paper-trader
```

## GitHub bot

Comment `/oc` or `/opencode` on an issue or PR. New issues get a triage reply; non-draft PRs get a shallow review. The public bot uses [FreeInference](https://freeinference.org) (`qwen3.6-35b`) via a repo Actions secret — no wallet, no real trades. Sessions are not shared.

## Also in this repository

The paper-trader is the product; two companion packages live alongside it.

| Package | Directory | What it is |
|---------|-----------|------------|
| `polymarket-benchmark` | [`benchmark/`](benchmark) | LLM evaluation harness — scores models on prediction-market sets (Brier, calibration, alpha). Supports any litellm model and TypeSafe's Jev decision model. |
| `polymarket-leaderboard-client` | [`leaderboard-client/`](leaderboard-client) | Client SDK for a compatible leaderboard server: register an agent, trade, read portfolio and stats. |

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to work on each package and [CHANGELOG.md](CHANGELOG.md) for release history.

## Tests

```bash
pytest -m "not live"             # unit + integration, 100% coverage gate
pytest                           # full suite (requires network)
pytest tests/test_e2e_live.py    # live API integration tests only
```

## License

MIT

<!-- mcp-name: io.github.agent-next/polymarket-paper-trader -->
