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

`make check` runs, per package, `pytest tests/ -x -q -m "not live"` with `--cov-fail-under=100`,
using the venv's Python (no version pin). CI (`.github/workflows/test.yml` + `toolkit.yml`)
runs coverage on Python 3.13 and the 3.10-3.12 matrix without coverage.
Narrow variant: run the same pytest command inside the one package directory you touched.

## Conventions

Full module map and design decisions: [docs/architecture.md](docs/architecture.md).

- Every module starts with `from __future__ import annotations`; type-hint every function;
  unions as `str | None`, not `Optional[str]`; private helpers prefixed `_`.
- Outcomes are always lowercase `"yes"`/`"no"` (normalized by `_validate_outcome`).
- Errors: `SimError` subclasses, each with a `code` class attribute. CLI and MCP return the
  envelope `{"ok": true, "data": {...}}` or `{"ok": false, "error": "msg", "code": "CODE"}`
  via `_ok`/`_err`; `_err_from` in `mcp_server.py` exposes only `SimError`/`ValueError`/
  `TypeError` messages and sanitizes everything else to `"Internal error"`.
- Reuse `_market_to_dict` (`mcp_server.py`) and `_parse_market_list` (`api.py`); never cache
  empty API responses (guard `len(data) > 0` before `_set_cached()`).
- Security: account names go through `_validate_account_name()`; `MAX_RESULTS = 100` caps
  every market-listing limit.
- Design: no price/book caching (metadata cached 5 min); fees follow the market's
  `feeSchedule`; slippage is reported vs the best quote; FOK is all-or-nothing, FAK allows
  partial fills; limit orders are GTC or GTD; accounts live at `~/.pm-trader/<account>/paper.db`.

## Testing

- Run `python3 -m pytest tests/ -x -q -m "not live"` after every change; update tests in the
  same pass as fixes or refactors. Use `pragma: no cover` only on `if __name__` guards.
- Test files mirror source (`pm_trader/engine.py` -> `tests/test_engine.py`); behavior tests
  (`test_behavior.py`) exercise full agent workflows via the `_mock(engine, market=..., book=..., fee=...)`
  helper; fixtures live in `conftest.py`; live tests `pytest.skip()` when data is unavailable.
- Use `pytest.approx()` for floats and `pytest.raises(ErrorType)` for exceptions.

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
- If a rebase fails twice, reset and cherry-pick instead.
- Headless: branch/PR work, tests, docs, patch-level releases once CI is green. Owner-gated: any minor/major bump.
