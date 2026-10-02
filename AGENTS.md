# polymarket-paper-trader — agent guide

Contribution and PR rules: [CONTRIBUTING.md](CONTRIBUTING.md); security reports: [SECURITY.md](SECURITY.md).

See [CLAUDE.md](CLAUDE.md) for the detailed module map of the root `pm_trader` package.

## Purpose

Paper-trading simulator for Polymarket, built for AI agents (Python 3.10+, SQLite, Click CLI,
MCP SDK 2.x). Public repo, actively maintained; the product ships to PyPI/ClawHub/MCP.

Org layering: a vertical (prediction-market domain) inside the `agent-next` org. Agent
runtimes consume it via MCP/CLI; model supply is not this repo's job (org `agent-gateway`
routes models) — this repo packages the domain logic, its benchmark, and the public arena.

## Orient

- `git status --short`, `git branch --show-current`, `git worktree list`, `gh pr list --state open`.
- Currency: a local checkout can lag `origin/main`. Fetch and compare
  (`git fetch origin && git rev-list HEAD..origin/main`) before trusting the release
  numbers and Project state below.
- Read first: [CLAUDE.md](CLAUDE.md) (architecture, conventions, testing rules), `pyproject.toml`.

## Layout

Four independent packages: the root `polymarket-paper-trader` (`pm_trader/`), `benchmark/`
(`pm_benchmark/`), `leaderboard-client/` (`pm_leaderboard_client/`), and `leaderboard-server/`
(`server/`, depends on the root package and the client). Work on one package at a time; each
has its own tests and 100% coverage gate.

## Interfaces

EXPOSES:

- `pm-trader` CLI (Click, `pm_trader/cli.py`) and the MCP server (`pm_trader/mcp_server.py`;
  stdio + streamable-HTTP transport, manifest `server.json`). The tool count lives in that
  file (the `@_tool` functions — 30 at v0.4.5); never hardcode it elsewhere.
- pip package `polymarket-paper-trader` on PyPI + ClawHub; official MCP Registry listing
  (`io.github.agent-next/*`, published from `server.json` by `mcp-registry.yml`, called by
  `publish.yml`); skill/plugin artifacts: `skill/polymarket-paper-trader/SKILL.md`,
  `.claude-plugin/plugin.json`, `gemini-extension.json`.
- `pm_benchmark` (eval harness — still a separate install as `polymarket-benchmark`, see
  Known gaps) and `pm_leaderboard_client`; `leaderboard-server/` is a FastAPI service for
  agents (self-host only, no deploy workflow).

CONSUMES: Polymarket Gamma (market discovery), CLOB (prices, order books), Data API v2
(price history) — the probed wire-level contract facts are under "Verified upstream
contract facts" below; trust those, not docs examples.

## Live surfaces

- Forecast Arena: <https://polymarket-leaderboard.com> — the public daily leaderboard,
  served from Cloudflare Pages (project `forecast-arena`); GitHub Pages is redeployed on
  every run as the rollback target. Data is append-only on the `arena-data` branch; daily
  runs: `.github/workflows/arena.yml` (06:17 + 10:43 UTC).
- Package releases: PyPI + ClawHub + GitHub Releases + MCP Registry, all `latest` (see
  Project state). Deploy = tag `vX.Y.Z` on merged main → `publish.yml`. PyPI/ClawHub are
  append-only, so rollback is a fix release against the previous tag.
- No other hosted service of ours: `leaderboard-server` ships `/health` and `/ready`
  (`leaderboard-server/server/app.py`) for whoever self-hosts it.

## Setup

`make setup` creates `.venv` and installs all four packages editable with dev extras:
`pip install -e ".[dev]" -e "benchmark[dev]" -e "leaderboard-client[dev]" -e "leaderboard-server[dev]"`.
Requires Python 3.10+ and pip.

## Check

`make check` runs, per package, the coverage gate CI enforces on Python 3.13
(`.github/workflows/test.yml` + `toolkit.yml`): `pytest tests/ -x -q -m "not live"` with
`--cov-fail-under=100`. CI additionally runs the suites on Python 3.10-3.12.
Narrow variant: run the same pytest command inside the one package directory you touched.

## Boundaries

- `live`-marked tests hit the real Polymarket Gamma/CLOB APIs. `make check` excludes them;
  run them only deliberately (`tests/test_e2e_live.py -m live`), never as part of the gate.
- All trading is paper/simulated, but price and order-book reads are real public API calls.
- Public repo: never commit secrets, tokens, or `.env` files; no private endpoints or links.

## Done

- Branch per change -> PR; keep changes atomic: one logical change per commit.
- New code ships with tests in the same change, with a real oracle; 100% coverage is required.
- `make check` green locally; CI green before merge; receipts (commands + output) in PR body.
  Longer multi-step agent receipts live in the workspace-level `agent-next/task-runs/`
  tree, not committed here (public repo hygiene).
- Prefer the existing helpers and JSON envelope conventions over new patterns.

## Operating rules (headless vs owner-gated)

Headless: branch/PR work, tests, docs, and the release flow in Conventions below at the
patch level once CI is green. Owner-gated: any minor/major version bump (explicit repo
policy, below). `live`-marked tests and everything in Boundaries stay deliberate-run only —
never wired into an automated gate.

## Project state (2026-09-25)

Current release: **v0.4.5** (PyPI + ClawHub + GitHub Releases, all `latest`). The client is
aligned with the current official API surface (Gamma keyset pagination, CLOB market data,
Data API v2 price history) and the fee simulation follows the official per-match curve.
Live e2e tests run weekly on CI (`live.yml`) and include bias assertions: simulated fills
must land inside the band the market actually quoted, and fees must equal the official
curve — the simulator's fidelity is tested, not assumed.

Recent history (details in [CHANGELOG.md](CHANGELOG.md)): v0.3.0 official fee curve ·
v0.3.1 tradability gates + partial-fill lifecycle · v0.3.2 cash reservation + per-level
fees · v0.3.3 keyset pagination · v0.3.4 Data API v2 `get_price_history` + bias tests
(closes #16) · README purpose-first rewrite · v0.4.0 MCP SDK 2.x (serialized tool calls,
real `serverInfo.version`) + `pm-trader strategy` + doc-drift guards · v0.4.1 agent-runtime
integrations (Claude Code plugin, Gemini CLI extension, `docs/integrations.md`), MCP carries
the skill (`trading_playbook` prompt/resource), streamable-HTTP transport (no
`backtest`/`pk_battle` over HTTP), Dockerfile, Jev strategy, outsider install gate ·
v0.4.2 fee-basis and cost-basis fixes, account-path and backtest hardening, MCP error
envelopes, leaderboard `/ready` · v0.4.3 audit fixes: atomic trade and resolution writes,
limit-order validation, GTD UTC expiry, leaderboard-server order identity and upstream errors ·
v0.4.4 UMA-status resolution, atomic limit fills, uncapped analytics, backtest mark-to-market,
atomic leaderboard trades, honest-red arena CI · v0.4.5 resolved-only benchmark alpha, arena
min-n ranking, resolution-aware analytics, shared-db cash safety, hashed leaderboard API keys.

## Verified upstream contract facts (live-probed; the docs disagree)

Trust the wire, not the docs examples — every item below was established by probing the
live API after the documented example failed:

- `GET /midpoint` (CLOB) returns `{"mid": "<string>"}` — the docs schema says `mid_price`.
  Parse `mid`, coerce with `float()`.
- `GET /markets/keyset` (Gamma) rejects the docs' snake_case `order` examples
  (`volume_num`) with 422; the wire accepts camelCase `volumeNum` / `liquidityNum`.
- Gamma `/markets` ignores `tag_slug` — resolve slugs to ids via `/tags/slug/{slug}`.
  Gamma also defaults `closed=false`, so closed markets need a `closed=true` retry.
- Gamma sends booleans as strings (`"false"`); `bool("false")` is `True`. Use the
  string-aware `_to_bool` in parsers.
- `GET /clob-markets/{condition_id}` (CLOB) returns abbreviated keys: `c` (condition),
  `t` (tokens, each `{t, o}`), `mos`, `mts`, `mbf`, `tbf`, `fd` (fee schedule
  `{r, e, to}`); newer additive keys (`gst`, `r`, `rfqe`, `itode`, `ibce`, `oas`) are
  ignored.
- `GET /v2/prices-history` (Data API v2) accepts `token_id`/`tokenId`, `start`, `end`,
  `interval` (`1m|1h|6h|1d|1w|max|all`), `bucket_seconds`/`bucketSeconds`,
  `as_of`/`asOf`, `limit`, `cursor`. Envelope `{data: [...], pagination: {limit, offset,
  has_more, next_cursor}}`; a documented miss is `data: null`; points are oldest-first.
  The v1 `fidelity` parameter does not exist on v2. The cursor's page size wins over a
  `limit` sent alongside it.
- Resolution (Gamma): there is no explicit winner field (`winningOutcome` etc. do not
  exist). A settled market has `closed: true`, `umaResolutionStatus: "resolved"` and
  `outcomePrices` of exactly `"0"`/`"1"`; before settlement the status is e.g.
  `"proposed"`, so a closing price alone is not a resolution.
- Fees: `fee = C × feeRate × p × (1-p)` per match, rounded to 5 decimals (minimum charge
  0.00001), taker-only — makers are never charged. Rates come from each market's
  `feeSchedule` on the wire (never hardcode category rates); zero-fee categories
  (e.g. geopolitics) carry `feesEnabled: false` and no `feeSchedule` at all.

When a documented example and the wire disagree, probe the real endpoint first and record
the outcome in the CHANGELOG entry — several of the facts above exist precisely because a
docs example was wrong.

## Conventions and pitfalls

- Version pins live in `pyproject.toml`, `server.json` (two places),
  `.claude-plugin/plugin.json`, `gemini-extension.json`, and BOTH
  `skill/polymarket-paper-trader/SKILL.md` copies (they must be identical — `test_meta`
  enforces it, and the changelog heading must match the installed version; reinstall with
  `pip install -e . --no-deps` after a bump so dist-info catches up).
- Release policy: patch releases are routine; any minor/major bump is an owner decision.
  Releases: bump pins → promote `[Unreleased]` in the CHANGELOG → PR → green CI → tag
  `vX.Y.Z` on the merged main commit → `publish.yml` (tests → tag/version match + outsider
  smoke on the built wheel → PyPI → ClawHub, GitHub Release, and `verify-pypi` re-running
  the smoke against the published package). `make outsider` runs the same smoke locally.
  Verify the tag live on PyPI and ClawHub before announcing.
- The benchmark harness is a separate install (`polymarket-benchmark`); `pm-trader
  benchmark run` replays trading strategies and only imports modules from the
  `examples.` and `tests.test_benchmark.` prefixes (allowlist in
  `pm_trader/benchmark.py`).
- `pm-trader benchmark` (strategy replay) and `polymarket-benchmark run` (model eval) are
  different things — keep the distinction in docs and code.
- Tests that hit the live APIs are marked `live` and skip cleanly without network; the
  full live suite is CI-only (`live.yml`), not part of the default run.

## Known production gaps (distance to the industrial bar)

- Live API fidelity is CI-only: `tests/test_e2e_live.py` runs weekly
  (`.github/workflows/live.yml`, cron `23 9 * * 1`) and is excluded from `make check` —
  a green local run proves the mocks, not the wire.
- The eval harness still requires a separate editable install (`polymarket-benchmark`)
  instead of a `pm-trader` subcommand/extra (see Open items).
- A release means touching version pins in 6+ places (`pyproject.toml`, `server.json` x2,
  `.claude-plugin/plugin.json`, `gemini-extension.json`, both `SKILL.md` copies);
  `test_meta` enforces only the SKILL.md pair — the rest ride on discipline.
- The 100% coverage gate runs on Python 3.13 locally; the 3.10-3.12 matrix is CI-only
  (`.github/workflows/test.yml`) — a local green does not prove the full matrix.

## Open items (product backlog, uncontroversial starts)

- Expose the eval harness through `pm-trader` (subcommand or pip extra) instead of a
  separate editable install.
