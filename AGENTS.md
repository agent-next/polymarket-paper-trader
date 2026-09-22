# AGENTS.md

Guidance for coding agents working in this repository. See [CLAUDE.md](CLAUDE.md) for the
detailed module map of the root `pm_trader` package.

## Layout

Three independent packages: the root `polymarket-paper-trader` (`pm_trader/`), `benchmark/`
(`pm_benchmark/`), and `leaderboard-client/` (`pm_leaderboard_client/`). Work on one package
at a time; each has its own tests and 100% coverage gate.

## Rules

- Run the tests for the package you touched before finishing:
  `python3 -m pytest tests/ -x -q -m "not live"` from that package directory.
- 100% coverage is required; new code ships with tests in the same change.
- Never commit secrets, tokens, or `.env` files.
- Keep changes atomic: one logical change per commit.
- Prefer the existing helpers and JSON envelope conventions over new patterns.
