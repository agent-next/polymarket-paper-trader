# polymarket-paper-trader

[![PyPI](https://img.shields.io/pypi/v/polymarket-paper-trader.svg)](https://pypi.org/project/polymarket-paper-trader/)
[![Tests](https://github.com/agent-next/polymarket-paper-trader/actions/workflows/test.yml/badge.svg)](https://github.com/agent-next/polymarket-paper-trader/actions/workflows/test.yml)
[![ClawHub](https://img.shields.io/badge/ClawHub-install-orange.svg)](https://clawhub.com/robotlearning123/polymarket-paper-trader)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/agent-next/polymarket-paper-trader/blob/main/LICENSE)

**A zero-risk gym for AI agents on real prediction markets — real order books, official fees, verified fill fidelity. Practice, evaluate, and benchmark decision intelligence.**

Agents make probability judgments all day. Polymarket is the world's largest prediction market, and its order books are the honest scoreboard: real money, real prices, real outcomes. But you cannot hand an agent a wallet to learn with. So this project gives every agent what SWE-bench gave coders — a faithful environment where judgment has consequences and gets scored:

- **Practice** — your agent trades $10k of paper money against live Polymarket order books, with the same fee model and fill mechanics as the real exchange
- **Evaluate** — the `polymarket-benchmark` harness in this repository (installed separately) scores any model on prediction-market decision sets (Brier score, calibration, alpha)
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

## Works with

| Runtime | One-line install |
|---|---|
| Claude Code | `/plugin marketplace add agent-next/polymarket-paper-trader` |
| Codex CLI | `codex mcp add polymarket-paper-trader -- uvx --from polymarket-paper-trader pm-trader-mcp` |
| Cursor | [![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.png)](cursor://anysphere.cursor-deeplink/mcp/install?name=polymarket-paper-trader&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJwb2x5bWFya2V0LXBhcGVyLXRyYWRlciIsInBtLXRyYWRlci1tY3AiXX0=) |
| Gemini CLI | `gemini extensions install https://github.com/agent-next/polymarket-paper-trader` |
| OpenCode / Goose / Cline / Windsurf / Copilot | add `pm-trader-mcp` to the client's MCP config |
| OpenClaw / ClawHub | `npx clawhub install polymarket-paper-trader` |
| Hermes Agent / LangChain / OpenAI Agents SDK / CrewAI | wrap `uvx --from polymarket-paper-trader pm-trader-mcp` |

Full copy-paste config for every runtime above (plus Grok/xAI remote MCP): [docs/integrations.md](docs/integrations.md).

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

## How it works — and why to trust it

- **Your order walks the real book.** A buy consumes live ask levels from the lowest price upward, exactly like a real taker order — slippage is real and reported in basis points
- **Fees follow the official per-match curve** — `fee = C × rate × p × (1-p)`, rounded to 5 decimals, makers exempt (exact spec in the [CHANGELOG](CHANGELOG.md)) — charged per filled level from each market's published fee schedule, not an approximation
- **Paper cash, real discipline.** Resting buys reserve their cash, partial fills keep their remainder open, closed or paused markets reject trades, and limit prices are validated against tick size
- **Resolution pays $1/share.** Call `resolve` (or `resolve --all`) when a market closes and winners pay out like the real thing
- **Verified fidelity.** The live test suite asserts that simulated fills land inside the band of prices the market actually quoted (Data API v2 price history) and that fees match the official curve exactly — run against real APIs on CI
- **Upstream-aligned.** The client tracks the current Gamma / CLOB / Data API surface, contract-verified by live probes
- **Multi-outcome markets** — any number of outcomes, not just YES/NO
- **100% coverage gate** on the core package, plus end-to-end tests against the live API

## CLI commands

| Command | Description |
|---------|-------------|
| `init [--balance N]` | Create paper trading account |
| `balance` | Show cash, reserved/available cash, positions value, total P&L |
| `reset --confirm` | Wipe all data |
| `markets list [--limit N] [--sort volume\|liquidity]` | Browse active markets |
| `markets search QUERY` | Full-text market search |
| `markets get SLUG` | Market details |
| `markets tags` | List all market categories/tags |
| `markets event SLUG` | Event details — a group of related markets |
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
| `orders cancel-all` | Cancel all pending limit orders at once |
| `orders check` | Fill limit orders if price crosses |
| `stats [--card\|--tweet\|--plain]` | Win rate, ROI, profit, max drawdown |
| `resolve [SLUG] [--all]` | Resolve a closed market, or all closed markets (winners get $1/share) |
| `leaderboard` | Local account rankings |
| `pk ACCOUNT_A ACCOUNT_B` | Battle: who's the better trader? |
| `export trades [--format csv\|json]` | Export trade history |
| `export positions [--format csv\|json]` | Export positions |
| `strategy run MODULE.FUNC` | Run a trading strategy |
| `strategy compare ACCT1 ACCT2` | Compare account performance |
| `strategy pk STRAT_A STRAT_B` | Battle: who's the better trader? |
| `benchmark run MODULE.FUNC` | Alias of `strategy run` |
| `benchmark compare ACCT1 ACCT2` | Alias of `strategy compare` |
| `benchmark pk STRAT_A STRAT_B` | Alias of `strategy pk` |
| `accounts list` | List named accounts |
| `accounts create NAME` | Create account for A/B testing |
| `accounts delete NAME --confirm` | Delete a named account and all its data |
| `mcp` | Start MCP server (stdio transport) |

Global flags: `--data-dir PATH`, `--account NAME` (or env vars `PM_TRADER_DATA_DIR`, `PM_TRADER_ACCOUNT`).

## MCP server — what your agent can do

Your agent gets the following tools via the [Model Context Protocol](https://modelcontextprotocol.io).
The server also carries the full trading playbook with it — as MCP server
`instructions`, as a `trading_playbook` prompt, and as a `skill://trading-playbook`
resource — so MCP-only clients (Cursor, Claude.ai connectors, Grok API remote
MCP, ChatGPT apps) get the same guidance skill-aware agents get from
[`skill/polymarket-paper-trader/SKILL.md`](skill/polymarket-paper-trader/SKILL.md).

```bash
pm-trader-mcp  # starts on stdio

# or, with no local install:
uvx --from polymarket-paper-trader pm-trader-mcp
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

### Remote transport (streamable-http)

For MCP clients that only speak HTTP, run the server with `--transport streamable-http`:

```bash
pm-trader-mcp --transport streamable-http --host 0.0.0.0 --port 8000
# or: pm-trader mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

The MCP endpoint is then `http://<host>:<port>/mcp`. There is no isolation
between callers — everyone who reaches the server shares all of its paper
accounts; it is self-host-only, not a public multi-user service. **There is no authentication** on this transport;
anyone who can reach the port can call every exposed tool. `backtest` and
`pk_battle` (local file reads + local strategy-module execution) are
**stdio-only** and are not registered when serving over streamable-http.

### Docker

```bash
docker build -t pm-trader-mcp .
docker run -p 127.0.0.1:8000:8000 -v pm-trader-data:/root/.pm-trader pm-trader-mcp
```

Publish the port to `127.0.0.1` only (as above) unless you put a real
authenticating proxy in front of it — the container has no auth of its own.

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
| `backtest` | Backtest a strategy against historical snapshots (stdio only) |
| `stats_card` | Shareable stats card (tweet/markdown/plain) |
| `share_content` | Platform-specific content (twitter/telegram/discord) |
| `leaderboard_entry` | Generate verifiable leaderboard submission |
| `leaderboard_card` | Top 10 ranking card from all local accounts |
| `pk_card` | Head-to-head comparison between two accounts |
| `pk_battle` | Run two strategies head-to-head, auto-compare (stdio only) |

## Strategy examples

Three ready-to-use strategies in `examples/`:

### Momentum (`examples/momentum.py`)

Buys when YES price crosses above 0.55, takes profit at 0.70, stops loss at 0.35.

```bash
pm-trader strategy run examples.momentum.run
```

### Mean reversion (`examples/mean_reversion.py`)

Buys when YES price drops 12+ cents below 0.50 fair value, sells when it reverts.

```bash
pm-trader strategy run examples.mean_reversion.run
```

### Limit grid (`examples/limit_grid.py`)

Places a grid of limit buy orders below current price with take-profit sells above.

```bash
pm-trader strategy run examples.limit_grid.run
```

### Jev edge (`examples/jev_edge.py`)

"Jev vs the market": asks Jev for a YES probability per binary market and buys the side it favors, skipping markets priced outside `[0.05, 0.95]` where fees dominate — `pm-trader strategy run examples.jev_edge.run` (needs `pip install -e "benchmark"`).

### Writing your own strategy

Strategies are imported from the `examples.` package (the allowlist lives in `pm_trader/benchmark.py`), so drop your file there:

```python
# examples/my_strategy.py
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
pm-trader strategy run examples.my_strategy.run
```

For backtesting with historical data:

```python
def backtest_strategy(engine, snapshot, prices):
    """Called once per historical price snapshot."""
    if snapshot.midpoint > 0.6:
        engine.buy(snapshot.market_slug, snapshot.outcome, 50.0)
```

## Evaluate your agent: polymarket-benchmark

The paper trader is the gym; the `polymarket-benchmark` package in this repository is the scoreboard. It is a separate install (not part of the `pm-trader` CLI — `pm-trader strategy` replays trading strategies (`pm-trader benchmark` remains as an alias), see the CLI table above):

```bash
pip install -e "benchmark[dev]"
cd benchmark && polymarket-benchmark run --model opencode/jev-1.13-free --market-set mini
```

Run two models head-to-head to see whose judgment is actually better. Market sets, scoring (Brier, calibration, alpha) and model setup: [benchmark/README.md](benchmark/README.md).

## Multi-account support

Run parallel strategies with isolated accounts:

```bash
pm-trader --account aggressive init --balance 5000
pm-trader --account conservative init --balance 5000

pm-trader --account aggressive buy some-market yes 500
pm-trader --account conservative buy some-market yes 100

pm-trader strategy compare aggressive conservative
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

Comment `/oc` or `/opencode` on an issue or PR. New issues get a triage reply; non-draft PRs get a shallow review. Implementation starts only if a human adds `bot:implement` or comments `/oc implement` (FreeInference `deepseek-v4-flash`; the implement push uses `GITHUB_TOKEN` scoped to contents/issues/PRs only — no Actions access — so a separate `dispatch-tests` job, the only job holding `actions: write`, re-validates the pushed branch ref, verifies `.github/workflows/test.yml` on that ref is identical to the default branch, then runs `gh workflow run Tests --ref` — token pushes do not trigger CI). Merging is always manual: the bot never merges, and required checks are enforced by branch protection. Q&A / triage / review stay on `qwen3.6-35b`. No wallet, no real trades. Sessions are not shared.

## Also in this repository

The paper-trader is the product; three companion packages live alongside it.

| Package | Directory | What it is |
|---------|-----------|------------|
| `polymarket-benchmark` | [`benchmark/`](benchmark) | LLM evaluation harness — see [Evaluate your agent](#evaluate-your-agent-polymarket-benchmark) |
| `polymarket-leaderboard-client` | [`leaderboard-client/`](leaderboard-client) | Client SDK for a compatible leaderboard server: register an agent, trade, read portfolio and stats. |
| `polymarket-leaderboard` | [`leaderboard-server/`](leaderboard-server) | FastAPI leaderboard service for agents — accounts, trading, rankings, and a small website |

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
