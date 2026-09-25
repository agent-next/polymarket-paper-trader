# polymarket-paper-trader — agent guide

Org rules: https://github.com/agent-next/.github/blob/main/AGENT-STANDARD.md (hard limits, PR/merge policy).

See [CLAUDE.md](CLAUDE.md) for the detailed module map of the root `pm_trader` package.

## Purpose

Paper-trading simulator for Polymarket, built for AI agents (Python 3.10+, SQLite, Click CLI,
FastMCP). Public repo, actively maintained; the product ships to PyPI/ClawHub/MCP.

## Orient

- `git status --short`, `git branch --show-current`, `git worktree list`, `gh pr list --state open`.
- Read first: [CLAUDE.md](CLAUDE.md) (architecture, conventions, testing rules), `pyproject.toml`.

## Layout

Three independent packages: the root `polymarket-paper-trader` (`pm_trader/`), `benchmark/`
(`pm_benchmark/`), and `leaderboard-client/` (`pm_leaderboard_client/`). Work on one package
at a time; each has its own tests and 100% coverage gate.

## Setup

`make setup` installs all three packages editable with dev extras:
`pip install -e ".[dev]" -e "benchmark[dev]" -e "leaderboard-client[dev]"`.
Requires Python 3.10+ and pip.

## Check

`make check` = the gate CI runs (`.github/workflows/test.yml` + `toolkit.yml`): per package,
`python3 -m pytest tests/ -x -q -m "not live"` with coverage `--cov-fail-under=100`.
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
- Prefer the existing helpers and JSON envelope conventions over new patterns.

## Project state (2026-09-24)

Current release: **v0.3.4** (PyPI + ClawHub + GitHub Releases, all `latest`). The client is
aligned with the current official API surface (Gamma keyset pagination, CLOB market data,
Data API v2 price history) and the fee simulation follows the official per-match curve.
Live e2e tests run weekly on CI (`live.yml`) and include bias assertions: simulated fills
must land inside the band the market actually quoted, and fees must equal the official
curve — the simulator's fidelity is tested, not assumed.

Recent history (details in [CHANGELOG.md](CHANGELOG.md)): v0.3.0 official fee curve ·
v0.3.1 tradability gates + partial-fill lifecycle · v0.3.2 cash reservation + per-level
fees · v0.3.3 keyset pagination · v0.3.4 Data API v2 `get_price_history` + bias tests
(closes #16) · README purpose-first rewrite.

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
- Fees: `fee = C × feeRate × p × (1-p)` per match, rounded to 5 decimals (minimum charge
  0.00001), taker-only — makers are never charged. Rates come from each market's
  `feeSchedule` on the wire (never hardcode category rates); zero-fee categories
  (e.g. geopolitics) carry `feesEnabled: false` and no `feeSchedule` at all.

When a documented example and the wire disagree, probe the real endpoint first and record
the outcome in the CHANGELOG entry — several of the facts above exist precisely because a
docs example was wrong.

## Conventions and pitfalls

- Version pins live in `pyproject.toml`, `server.json` (two places), and BOTH
  `skill/polymarket-paper-trader/SKILL.md` copies (they must be identical — `test_meta`
  enforces it, and the changelog heading must match the installed version; reinstall with
  `pip install -e . --no-deps` after a bump so dist-info catches up).
- Release policy: patch releases are routine; any minor/major bump is an owner decision.
  Releases: bump pins → promote `[Unreleased]` in the CHANGELOG → PR → green CI → tag
  `vX.Y.Z` on the merged main commit → `publish.yml` (tests → PyPI → ClawHub → GitHub
  Release). Verify the tag live on PyPI and ClawHub before announcing.
- The benchmark harness is a separate install (`polymarket-benchmark`); `pm-trader
  benchmark run` replays trading strategies and only imports modules from the
  `examples.` and `tests.test_benchmark.` prefixes (allowlist in
  `pm_trader/benchmark.py`).
- `pm-trader benchmark` (strategy replay) and `polymarket-benchmark run` (model eval) are
  different things — keep the distinction in docs and code.
- Tests that hit the live APIs are marked `live` and skip cleanly without network; the
  full live suite is CI-only (`live.yml`), not part of the default run.

## Open items (product backlog, uncontroversial starts)

- Expose the eval harness through `pm-trader` (subcommand or pip extra) instead of a
  separate editable install.
- Disambiguate the two "benchmark" surfaces (candidate: rename strategy replay to
  `pm-trader strategy ...`).
- FastMCP `serverInfo.version` reports the MCP library version (0.30.0), not the package
  version.
- `test_meta.TestDocCounts` could derive README counts mechanically instead of relying on
  prose staying count-free.
