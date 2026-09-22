# Contributing

Thanks for helping improve the Polymarket paper-trading toolkit.

## Repository layout

This repository hosts three Python packages:

| Path | Package | What it is |
|------|---------|------------|
| `.` (root) | `polymarket-paper-trader` | Paper-trading simulator, CLI, and MCP server |
| `benchmark/` | `polymarket-benchmark` | LLM evaluation harness ("decision intelligence" benchmark) |
| `leaderboard-client/` | `polymarket-leaderboard-client` | Client SDK for a compatible leaderboard server |

Each package is self-contained with its own `pyproject.toml`, tests, and coverage gate.

## Development setup

```bash
# root package
pip install -e ".[dev]"

# benchmark
pip install -e "benchmark[dev]"

# leaderboard client
pip install -e "leaderboard-client[dev]"
```

## Running tests

Run the suite for the package you changed:

```bash
python3 -m pytest tests/ -x -q -m "not live"                    # root
cd benchmark && python3 -m pytest tests/ -x -q -m "not live"    # benchmark
cd leaderboard-client && python3 -m pytest tests/ -x -q         # client
```

Every package maintains **100% test coverage**; the CI gate fails below that. Add tests
for new behavior in the same change — a change is not done until its tests pass.

Live tests hit the real Polymarket APIs and are marked `live`; they are skipped by
default and run on a schedule.

## Conventions

- `from __future__ import annotations` at the top of every module.
- Full type hints; union types use `|` (`str | None`, not `Optional[str]`).
- Custom errors carry a `code` class attribute.
- CLI/MCP responses use the JSON envelope `{"ok": true, "data": ...}` or
  `{"ok": false, "error": "...", "code": "..."}`.
- Atomic commits: one logical change per commit.
- Never commit credentials, tokens, or `.env` files.

## Pull requests

1. Branch from `main`.
2. Keep the change focused; include tests.
3. Make sure the package's tests and coverage gate pass locally.
4. Describe what changed and how you verified it.

Bug reports and feature requests use the issue templates. For security issues, see
[SECURITY.md](SECURITY.md).
