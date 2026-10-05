# polymarket-paper-trader — agent guide

Org standard: https://github.com/agent-next/.github/blob/main/AGENT-STANDARD.md
Contribution and PR rules: [CONTRIBUTING.md](CONTRIBUTING.md); security reports: [SECURITY.md](SECURITY.md).

## Purpose

Paper-trading simulator for Polymarket, built for AI agents (Python 3.10+, SQLite, Click CLI,
MCP SDK 2.x). Public repo, actively maintained; the product ships to PyPI/ClawHub/MCP.

Org layering: a vertical (prediction-market domain) inside the `agent-next` org. Agent
runtimes consume it via MCP/CLI; model supply is not this repo's job (org `agent-gateway`
routes models) — this repo packages the domain logic, its benchmark, and the public arena.

## Orient

- `git status --short`, `git branch --show-current`, `git worktree list`, `gh pr list --state open`.
- Currency: a local checkout can lag `origin/main`; fetch and compare before trusting release numbers.
- Module map, code conventions, testing rules: [docs/architecture.md](docs/architecture.md).
- Interfaces, live surfaces, release flow, verified upstream API facts, project state, known gaps:
  [docs/agent-reference.md](docs/agent-reference.md). Read it before touching API parsing or releases.

## Layout

Four independent packages: the root `polymarket-paper-trader` (`pm_trader/`), `benchmark/`
(`pm_benchmark/`), `leaderboard-client/` (`pm_leaderboard_client/`), and `leaderboard-server/`
(`server/`, depends on the root package and the client). Work on one package at a time; each
has its own tests and 100% coverage gate.

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
- Headless: branch/PR work, tests, docs, patch-level releases once CI is green. Owner-gated: any minor/major bump.
