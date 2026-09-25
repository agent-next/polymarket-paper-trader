# Strategy Examples

Three ready-to-run strategies for pm-trader. Each works with both live markets and backtesting.

## Quick Start

```bash
# Install
npx clawhub install polymarket-paper-trader

# Init account
pm-trader init --balance 10000

# Run a strategy
pm-trader strategy run examples.momentum.run
pm-trader strategy run examples.mean_reversion.run
pm-trader strategy run examples.limit_grid.run

# Compare results
pm-trader strategy compare momentum mean_reversion limit_grid
```

## Strategies

### Momentum (`momentum.py`)
Buy when YES price crosses above 0.55, sell at 0.70 or cut loss at 0.35.
Best in trending markets with strong directional moves.

### Mean Reversion (`mean_reversion.py`)
Buy when price dips 12+ cents below 0.50 fair value, sell when it reverts.
Best in choppy markets that oscillate around equilibrium.

### Limit Grid (`limit_grid.py`)
Place 5 limit buy orders at 3-cent intervals below market price, with
take-profit sells 5 cents above entry. Profits from range-bound oscillation.

### Jev Edge (`jev_edge.py`)
"Jev vs the market": asks the Jev decision model for a YES probability on each
scanned binary market and buys the side it favors when Jev's probability
disagrees with the YES midpoint by more than `EDGE`, skipping markets priced
outside `[MIN_PRICE, MAX_PRICE]` (default `[0.05, 0.95]`) since fees eat a
near-fixed fraction of stake at the extremes. Requires the benchmark package
(`pip install -e "benchmark"`); model defaults to `opencode/jev-1.13-free`
(no key needed), override with `JEV_MODEL`. Run from the repo root with
`PYTHONPATH=.` (a strict editable install does not map the `examples`
namespace package):
```
PYTHONPATH=. pm-trader strategy run examples.jev_edge.run
```

## Write Your Own

A strategy is any function with this signature:

```python
from pm_trader.engine import Engine

def my_strategy(engine: Engine) -> None:
    """Your strategy logic here."""
    # 1. Get market data
    markets = engine.api.list_markets(sort_by="liquidity", limit=10)

    # 2. Analyze and trade
    for market in markets:
        if market.closed:
            continue
        price = market.yes_price
        if price < 0.40:  # your signal
            engine.buy(market.slug, "yes", 200.0)

    # 3. Check limit orders
    engine.check_orders()
```

Run it:
```bash
pm-trader strategy run my_module.my_strategy
```

### Available Engine Methods

| Method | Description |
|--------|-------------|
| `engine.buy(slug, outcome, usd)` | Market buy |
| `engine.sell(slug, outcome, shares)` | Market sell |
| `engine.place_limit_order(...)` | GTC/GTD limit order |
| `engine.check_orders()` | Execute pending limit orders |
| `engine.get_portfolio()` | List open positions |
| `engine.get_balance()` | Cash + positions value |
| `engine.resolve_market(slug)` | Settle resolved market |
| `engine.resolve_all()` | Settle all resolved markets |
| `engine.api.list_markets(...)` | Browse markets |
| `engine.api.search_markets(q)` | Search markets |
| `engine.api.get_order_book(token_id)` | Live order book |

### Backtest Version

For backtesting, add a second function:

```python
def backtest_strategy(engine: Engine, snapshot, prices: dict) -> None:
    """Called once per price snapshot."""
    mid = snapshot.midpoint
    if mid < 0.40:
        engine.buy(snapshot.market_slug, snapshot.outcome, 200.0)
```

Backtests run through the `backtest` MCP tool (`pm_trader.backtest.run_backtest`) — there is no `pm-trader backtest` CLI command.

## MCP Integration

Agents can run strategies through MCP tools:

```
init_account → list_markets → buy/sell → check_orders → stats → share_content
```

The SKILL.md tells agents how to trade autonomously. Install via ClawHub and let your agent trade.
