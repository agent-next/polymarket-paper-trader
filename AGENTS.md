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
