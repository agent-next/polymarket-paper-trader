# Changelog

All notable changes to `polymarket-paper-trader` are documented here.

## [Unreleased]

### Security
- **Streamable-HTTP transport no longer serves `backtest`/`pk_battle`**: both
  read arbitrary local files and import/execute local strategy modules, which
  must not be reachable from a remote MCP client; they remain available on
  stdio. Docker/README now also state plainly that the HTTP endpoint has no
  authentication and document publishing the port to `127.0.0.1` only;
  `.dockerignore` now excludes `.env`/`.env*` files from the image.

### Added
- **Agent runtime integrations**: a Claude Code plugin (`.claude-plugin/plugin.json` +
  `marketplace.json`) and a `gemini-extension.json` that bundle the
  `polymarket-paper-trader` skill with `pm-trader-mcp`; `docs/integrations.md` with
  copy-paste MCP/skill config for Claude Code, Codex CLI, Cursor, Gemini CLI, OpenCode,
  Goose, Cline, Windsurf, GitHub Copilot, OpenClaw/ClawHub, Hermes Agent, Grok/xAI
  (remote MCP), LangChain, the OpenAI Agents SDK, and CrewAI; a README "Works with"
  table; and a `metadata.openclaw` block in both `SKILL.md` copies (alongside the
  existing `metadata.clawdbot`) plus a skill "Setup" note.
- **MCP server carries the trading skill with it**: server `instructions`, plus
  `skill/polymarket-paper-trader/SKILL.md`'s body exposed as a
  `trading_playbook` prompt and a `skill://trading-playbook` resource, so
  MCP-only clients get the same playbook skill-aware agents get.
- **Remote transport**: `pm-trader-mcp` / `pm-trader mcp` gain
  `--transport {stdio,streamable-http}` (default `stdio`, unchanged
  behavior), `--host` and `--port`, using the MCP SDK's native
  streamable-HTTP support.
- **Docker**: root `Dockerfile` and `.dockerignore` to self-host the
  streamable-HTTP server (single-tenant, self-host only).
- **`examples/jev_edge.py` — "Jev vs the market" showcase strategy.** Scans active markets (default query `bitcoin`, override via `JEV_QUERY`), asks the Jev decision model a `noul` question per binary market via `pm_benchmark.jev.query_jev` (lazy import; the strategy errors clearly without `pip install -e "benchmark"`), and buys YES/NO with a fixed `POSITION_SIZE_USD` when Jev's probability disagrees with the current YES midpoint by more than `EDGE` (skipping held markets and capping at `MAX_POSITIONS`). The Jev state carries only public market data (question, truncated description, end date, YES midpoint). Model defaults to `opencode/jev-1.13-free` (no key needed), overridable via `JEV_MODEL`.
- Closed loop (no self-merge): `implement` only on `bot:implement` or `/oc implement` (`deepseek-v4-flash`); after a `GITHUB_TOKEN` push a separate `dispatch-tests` job (the only job holding `actions: write`; the model job has no Actions scope) validates the pushed branch output against a strict ref pattern, verifies `.github/workflows/test.yml` on the ref is byte-identical to the default branch, then dispatches `gh workflow run Tests --ref <branch>` — so a prompt-injected model can never hold a token that dispatches workflows nor reshape the dispatched workflow. `review` stays comment-only; merge gating stays with branch protection.
- Review hardening: `implement` gated to trusted actors (collaborator write+ via permission API) and the exact `/oc implement` command form, including PR review comments; `comment` job drops `contents: write`; preflight steps stay inline in the workflow (no local composite action — PR-review-comment checkouts are PR-controlled); bot jobs get `timeout-minutes` and stop logging secret lengths.
- Key isolation: public Q&A/triage/review jobs run on a separate `FREEINFERENCE_PUBLIC_KEY` (same env name, so `opencode.json` is unchanged); `implement` keeps `FREEINFERENCE_API_KEY` — a public-face prompt injection cannot burn the implement pool.

## [0.4.0] - 2026-09-25

### Added
- **`pm-trader strategy` command group**: `strategy run`, `strategy pk`, and `strategy compare` are the preferred names for the strategy-replay commands and reuse the same implementations as `pm-trader benchmark run|pk|compare`; `benchmark` keeps working unchanged and is marked as an alias in its help text (no deprecation warning is emitted).

### Changed
- **MCP SDK 2.x**: the MCP server now requires `mcp>=2,<3` and uses `MCPServer` (upstream removed `mcp.server.fastmcp` in 2.0). Fresh installs now get MCP SDK 2.x; `serverInfo.version` now reports the `polymarket-paper-trader` package version instead of the MCP library version. Tool names, signatures, and payloads are unchanged.

### Fixed
- **MCP tool calls serialized on a dedicated worker thread.** MCP SDK 2.x dispatches `tools/call` concurrently and runs sync tool functions on arbitrary anyio worker threads, so parallel calls crashed with "SQLite objects created in a thread can only be used in that same thread" — the Engine's SQLite connection is bound to the thread that opened it. Every registered tool is now an async wrapper that submits the sync function to a single-worker `ThreadPoolExecutor`, restoring the serialized execution semantics SDK 1.x provided (SDK 1.x ran sync tools on the event-loop thread). Tool names, signatures, and advertised schemas are unchanged; the module-level functions remain plain sync callables for direct use.
- **Documentation drift**: the README CLI table now lists every leaf command (added `markets tags`, `markets event`, `accounts delete`, `orders cancel-all`, and the `strategy`/`benchmark` rows); `benchmark/README.md` no longer references a nonexistent `agent` extra (agent mode installs `../leaderboard-client`); `examples/README.md` uses `pm-trader strategy` and drops the nonexistent `pm-trader backtest` CLI invocation. New `test_meta` guards walk the click command tree and both SKILL.md tool tables so these tables cannot drift again.

## [0.3.4] - 2026-09-24

### Added
- **Price history via Data API v2**: `PolymarketClient.get_price_history(token_id, *, interval, start, end, bucket_seconds, as_of, limit)` fetches an outcome token's historical price series from `GET https://data-api.polymarket.com/v2/prices-history`, returning oldest-first points (`timestamp`, `price`, `resolution_seconds`) with automatic cursor pagination (`pagination.next_cursor` followed until exhausted or `limit` reached; a replayed cursor is never re-followed). Exactly one window form per request: `interval` (`1m|1h|6h|1d|1w|max|all`), `start`/`end` epoch seconds (capped at 15 days), or `as_of`; `bucket_seconds` requests a specific grain and `resolution_seconds` on each point reports the grain actually served. Never cached, like all price data.
- **Live bias tests against real market data** (closes #16): simulated buy and sell fills on live books are asserted to execute inside the band the market really quoted over the recent past (Data API v2 price history ± a 5-cent spread allowance), bounding any simulator bias instead of assuming it is zero; the fee charged is asserted against the official `feeSchedule` curve summed per filled level, exactly as the exchange charges per match.

## [0.3.3] - 2026-09-24

### Changed
- **Market listing migrated to `GET /markets/keyset` cursor pagination** — `list_markets` and `get_markets_by_tag` now page via `after_cursor`/`next_cursor` (≤100 per request, multi-page fetch until the requested limit, deduplicated by `condition_id`, a repeated cursor is never re-followed) instead of the deprecated offset-based `GET /markets`. Ordering uses the keyset field names `volumeNum`/`liquidityNum`. Tag filtering resolves `tag_slug` → numeric `tag_id` via `GET /tags/slug/{slug}` (cached 5 min); an unknown slug returns `[]` as before. Per-item market payloads are unchanged, as are both public signatures and `list[Market]` return types.

## [0.3.2] - 2026-09-24

### Added
- **Cash reservation for open buy limit orders.** A resting buy holds its `remaining_amount` against cash: `place_limit_order` (buy side) now rejects with `INSUFFICIENT_BALANCE` when `amount` plus a conservative fee upper bound exceeds `available_cash` (cash minus all open buys' `remaining_amount`), checked after tick validation and before any order row is created. The fee bound is `amount × feeSchedule.rate` on schedule markets (the worst case of the official curve for a USD-sized buy: `fee = C·rate·p·(1-p) = amount·rate·(1-p)`, maximized as `p → 0`) and `(bps/10_000) × 0.5 × amount` floored at `0.0001` on legacy-fee markets; zero-fee markets bound to 0, so the gate degenerates to the pure notional. Cancel, expire, fill, and partial fill release or shrink the reservation automatically via the order lifecycle — no separate ledger. Previously a $1M resting buy could be placed on a $10k account.
- **`get_balance` reports reserved and available cash** (additive): `reserved_cash` (notional held by open buys) and `available_cash` (cash minus reserved, clamped at 0) now appear in the engine dict, the CLI `balance` JSON, and the MCP `get_balance` payload. `reserved_cash` is a subset of `cash`, so `total_value` and `pnl` are unchanged.

### Changed
- **Market buys can no longer spend reserved cash.** `buy` checks `total_cost + fee` against `available_cash` instead of raw cash, so a market order cannot consume the reserve held by resting buys; when nothing rests, `available_cash == cash` and behavior is unchanged. A resting buy that later cannot be afforded (multi-order fee-slack exhaustion) is still permanently rejected by `check_orders` via the fill-time cash guard.
- **Slippage is now measured against the crossed quote.** `FillResult.slippage_bps` — and the `slippage` persisted on every trade and surfaced via CLI/MCP/export — is now `(avg_price - best_ask) / best_ask * 10_000` for buys and `(best_bid - avg_price) / best_bid * 10_000` for sells: a fill at the touch is exactly 0 and worse fills are positive on both sides. The previous midpoint-based value is preserved on the fill result as `slippage_bps_midpoint` (reported on `FillResult` only, not persisted).

### Fixed
- **Exact-depth FOK fills no longer rejected by binary float noise.** A FOK buy or sell that exactly consumed the book depth could return `filled=False` when binary float left a sub-1e-9 remainder (e.g. book cost `0.1 + 0.2` vs amount `0.1 + 0.2`). Buy and sell now treat remainders `<= FILL_EPSILON` (1e-9) as exhausted — so a residue can also no longer spawn a phantom micro-fill on the next level — and clamp level subtractions at 0.
- **Multi-level fills on `feeSchedule` markets now charge per level, not at the VWAP.** A fill crossing several levels previously charged one fee at the average price; the official curve is per-match, so the fee is now `sum_i shares_i × rate × p_i × (1 - p_i)` with each level rounded to 5 decimals (min charge 0.00001). Example divergence: 10 sh @0.60 + 20 sh @0.70 at rate 0.07 → 0.462 (was 0.46667 at avg 0.6667). The legacy bps fallback is unchanged — still a single charge at the average price.

## [0.3.1] - 2026-09-24

### Changed
- **Tradability gates**: `buy`, `sell`, and `place_limit_order` now reject markets that are closed, not active, or not accepting orders (`check_orders` permanently rejects resting orders in such markets instead of retrying them forever). Limit prices are validated against the market's tick size (`TICK_SIZE_VIOLATION`) before any order row is created; markets reporting no tick size fall back to the cached CLOB `/tick-size` endpoint. `sell` now enforces the $1 minimum gross notional (shares × best bid).
- **Partial-fill order lifecycle**: limit orders track `remaining_amount` and a `partially_filled` status. A partial FAK fill keeps its remainder open instead of silently dropping it; the remainder of a marketable limit that only partly fills at placement rests as a maker order. `get_pending_orders`, cancel, and expire now operate on partially filled orders, and `check_orders` permanently rejects resting orders on paused/closed markets. Existing databases migrate automatically (one-shot, in-place rebuild preserving ids and timestamps) on the next Engine start.

### Fixed
- **Gamma string booleans parsed as `True`**: `active`, `closed`, `acceptingOrders`, and `negRisk` arrive as strings (`"false"`) from the live Gamma API; `bool("false")` is `True`, so a market reporting `active="false"` passed the old closed-only gate. Parsing is now string-aware (`_to_bool`), case-insensitive, with `None` falling back to the caller default. `Market` gains `accepting_orders` (default `True`) and `neg_risk` (default `False`), exposed additively on the MCP wire payloads.
- **Win rate now uses FIFO lot accounting.** A sell's realized entry cost is drawn from the oldest open lots in that (market, outcome) at a fee-inclusive `cost_per_share = (amount_usd + fee) / shares`, matched in chronological order — replacing the fee-exclusive weighted-average entry.
- **Sharpe ratio and max drawdown now run on a daily equity curve** — cash plus open positions marked at their last traded price — computed for every calendar day from first to last trade, with zero-trade days carried forward. Replaces the per-trade cashflow / traded-days-only series; risk-free rate stays 0.

## [0.3.0] - 2026-09-23

### Changed
- **Fee simulation now follows the official `feeSchedule` curve.** `Market.fee_schedule` (parsed in 0.2.1 as data only) is now the fee source for every trade: `fee = C × feeSchedule.rate × p × (1 - p)`, where `C` is the number of shares — charged on the share count on both the buy and the sell path, not on the USD notional. Fees round to 5 decimals; the smallest charge is 0.00001 USDC. Only the identity exponent (`exponent = 1`, the sole form Polymarket publishes today) drives the curve; a non-identity exponent is not published anywhere and conservatively falls back to the legacy model.
- **Resting limit fills are maker fills**: they pay no fee in a `takerOnly` market, per "Makers are never charged fees." A marketable limit (one that crosses the book at placement) executes immediately as a taker fill and pays the fee, exactly as a real CLOB never rests a limit through the opposite side.
- Trades keep an audit trail in the existing `fee_rate_bps` column: a `feeSchedule` market records `rate × 10_000` (e.g. `0.07` → `700`) and no longer calls `GET /fee-rate`. No schema change.

### Fixed
- **Buy fees below p = 0.50 were undercharged and every sell fee was overcharged.** The old `bps/10000 × min(price, 1-price) × size` model charged buys on the USD notional (agreeing with the official curve only at `p ≥ 0.50`) and applied a different price component on sells. At 200 bps a $100 buy at 0.30 paid $0.60 instead of $1.40; a 100-share sell at 0.50 paid $1.00 instead of $0.50. Both now match `https://docs.polymarket.com/trading/fees`.
- The legacy `bps` model (and its 0.0001 minimum fee) is retained unchanged as the fallback for markets that publish no usable `feeSchedule` — absent, `rate = 0` (fee-free categories such as Geopolitics), or a non-identity `exponent`.

## [0.2.1] - 2026-09-23

### Fixed
- **CLOB fee rate always 0**: `GET /fee-rate` now returns `{"base_fee": <int bps>}`; `get_fee_rate()` read the old `fee_rate_bps` key and so returned 0 for every market. It now reads `base_fee` (and still accepts `fee_rate_bps` from cached rows written before the drift).
- **CLOB single-market endpoint moved**: `GET /markets/{condition_id}` is gone; the client now calls `GET /clob-markets/{condition_id}` and parses the abbreviated payload (`c`, `t`/`o` tokens, `mts`, `mos`, `mbf`, `tbf`). The old long-key shape is still accepted.
- **Closed markets were unreachable by slug**: Gamma's `GET /markets` defaults `closed` to false, so closed markets never matched. `get_market()` now retries the slug lookup once with `closed=true` before falling back to CLOB.
- **Gamma enrichment of CLOB lookups**: abbreviated CLOB payloads carry no slug, so enrichment now falls back to a `condition_ids` lookup instead of returning a market with empty slug/question.
- **Text search moved to `GET /public-search`**: `search_markets()` used the undocumented `GET /markets?_q=`, which no longer filters. It now calls `/public-search` and flattens `events[].markets`. The return type is unchanged.
- **Event-by-slug moved**: `get_event()` now calls `GET /events/slug/{slug}` (the `/events/{id}` form is id-only).

### Added
- `Market.fee_schedule` — the nested Gamma `feeSchedule` object (`rate`, `exponent`, `takerOnly`, `rebateRate`). `rate` is a coefficient, not bps; it is data-only and does not affect fee simulation.
- `Market.min_order_size`, `Market.maker_base_fee_bps`, `Market.taker_base_fee_bps` — parsed from the abbreviated CLOB payload (`mos`, `mbf`, `tbf`).

## [0.2.0] - 2026-09-23

### Added
- Benchmark harness (`benchmark/`) and leaderboard client SDK (`leaderboard-client/`) are now part of this repository, with OSS scaffolding (LICENSE, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md).
- OpenCode GitHub Action: answers `/oc` and `/opencode` on issue and pull-request comments, triages new issues, and shallow-reviews non-draft PRs. The public bot runs the free FreeInference `qwen3.6-35b` model (`opencode.json` + `FREEINFERENCE_API_KEY`).
- `/oc` jobs fail closed — a preflight step stops the job when `FREEINFERENCE_API_KEY` is empty. `tests/test_opencode_bot.py` pins the shipped workflow and provider.
- Weekly live-API monitor (`live.yml`, Mondays 09:23 UTC) runs the live e2e suite against the real Gamma and CLOB APIs.
- Toolkit CI (`toolkit.yml`) tests `benchmark/` and `leaderboard-client/` across Python 3.10-3.13 with a 100% coverage gate.

### Changed
- Bump the ClawHub CLI from `0.7.0` to `0.23.3` — required for the MIT-0 license gate used when publishing the skill.

## [0.1.8] - 2026-08-14

### Fixed
- **CRITICAL**: Pin `mcp>=1.28,<2` — MCP SDK 2.0.0 (2026-07-28) removed `mcp.server.fastmcp`, breaking every fresh install of the MCP server (`pm-trader-mcp` failed with `ModuleNotFoundError`). SDK 1.x stays on security-maintenance per upstream; migration to `MCPServer` tracked for a future release.
- Sync stale version fields: `server.json` 0.1.6 → 0.1.8, `SKILL.md` 0.1.7 → 0.1.8

## [0.1.7] - 2026-03-01

### Added
- skills.sh support — skill discoverable via `npx skills add agent-next/polymarket-paper-trader`
- Market discovery: `get_tags` — list all market categories/tags (cached 5 min)
- Market discovery: `get_markets_by_tag` — filter markets by tag slug
- Market discovery: `get_event` — fetch event details (group of related markets, cached 5 min)
- `cancel_all_orders` — batch cancel all pending limit orders at once
- CLI: `markets tags`, `markets --tag <slug>`, `markets event <slug>`, `orders cancel-all`
- MCP tool count: 26 → 30

### Security
- Add data trust boundaries to SKILL.md — mark Polymarket API data as untrusted, prevent indirect prompt injection via market content
- **CRITICAL**: Block arbitrary code execution via `importlib.import_module` — strategy loading now restricted to allowlisted packages (`examples.*`, `tests.test_benchmark.*`)
- **HIGH**: Prevent path traversal via account parameter — reject `..`, `/`, `\` in account names (MCP + CLI)
- **HIGH**: Harden CI/CD — pin all GitHub Actions to full commit SHAs, add restrictive top-level `permissions:`, pin `clawhub@0.7.0`
- **HIGH**: Bump `setuptools` minimum to `>=78.1.1` (CVE fix)
- **MEDIUM**: Sanitize error messages — hide internal paths from unexpected exceptions
- **MEDIUM**: Cap API result limits (`MAX_RESULTS=100`) to prevent resource exhaustion
- **MEDIUM**: Validate backtest `data_path` against allowed directories

## [0.1.6] - 2026-03-01

### Fixed
- Stop shipping `examples` as top-level package in PyPI wheel — was polluting user site-packages with a generic name

### Changed
- Publish workflow now runs full test suite (Python 3.10–3.13, 100% coverage) before publishing
- ClawHub publish automated in GitHub Actions
- GitHub Release auto-created with generated notes on tag push

### Added
- MCP Registry `server.json` for future registry submission
- README ownership verification tag for MCP Registry

## [0.1.5] - 2026-03-01

### Added
- PyPI publishing via GitHub Actions (`publish.yml`)
- LICENSE file (MIT)
- PyPI badge in README
- `py.typed` marker (PEP 561)
- `[project.urls]` in pyproject.toml (Homepage, Repository, Issues)
- Package metadata: authors, keywords, classifiers

### Changed
- Install section in README now shows `pip install` first

## [0.1.4] - 2026-03-01

### Added
- GitHub Actions CI test workflow (Python 3.10–3.13, 100% coverage gate)
- PK battle system — head-to-head strategy comparison
- Leaderboard card generator and MCP tool
- `share_content` MCP tool with platform-specific templates
- Strategy examples: momentum, mean reversion, limit grid
- Stats cards with top positions and template variants

### Changed
- Rewrite all user-facing copy for viral distribution
- Rename `pm_sim` → `pm_trader` package
- Extract `card.py` constants/helpers, eliminate duplication

### Fixed
- Negative P&L copy semantics
- False 280-char claim in stats cards
- `pragma: no cover` on `__main__.py` for CI

## [0.1.3] - 2026-03-01

### Added
- ClawHub skill for OpenClaw integration
- SKILL.md agent behavior program
- 26 MCP tools (up from 20)

## [0.1.2] - 2026-03-01

### Added
- GTC/GTD limit order management
- Backtesting engine with historical price replay
- Multi-outcome (neg-risk) market support
- Performance analytics (Sharpe, win rate, drawdown)
- CSV/JSON export for trades and positions
- Benchmarking harness for strategies
- Multi-account support

### Fixed
- Limit orders enforce price ceiling/floor
- GTD expiry validation
- `watch_prices` raises ValueError for invalid outcomes
- `check_orders` rejects permanently unfillable orders

## [0.1.1] - 2026-03-01

### Added
- MCP server exposing 20 tools for AI agents
- Click CLI with JSON envelope output
- 1:1 faithful order book fill engine (FOK/FAK)
- Polymarket HTTP client (Gamma + CLOB API)
- Trade execution engine (buy/sell/resolve)
- SQLite database with WAL mode
- E2E tests against live Polymarket API
