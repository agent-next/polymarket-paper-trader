# polymarket-benchmark

LLM evaluation framework for prediction market trading. "SWE-bench for decision intelligence."

## Commands

```bash
python -m pytest tests/ -x -q                            # fast tests
python -m pytest tests/ -x -q -m "not live"              # skip live API tests
python -m pytest tests/ --cov=pm_benchmark --cov-report=term-missing  # coverage
polymarket-benchmark list-sets                            # list bundled market sets
polymarket-benchmark run --model claude-opus-4 --market-set mini  # run benchmark
```

## Architecture

```
cli.py → runner.py → providers.py (LLM via litellm; Jev ids → jev.py System One)
                   → market_info.py (Polymarket Gamma API)
                   → scoring.py (Brier, calibration, composite)
                   → report.py / export.py (output)

config.py — Pydantic models (LLMConfig, RunConfig)
jev.py — TypeSafe System One (Jev) typed-question provider
market_set.py — YAML market set loader
prompts.py — LLM prompt templates
```

## Conventions

- `from __future__ import annotations` at top of every module
- Full type hints: `def foo(x: int) -> str:`
- Union types: `str | None`, not `Optional[str]`
- JSON envelope: `{"ok": true, "data": ...}` or `{"ok": false, "error": "...", "code": "..."}`
- Tests mirror source: `pm_benchmark/foo.py` → `tests/test_foo.py`
- 100% test coverage maintained (`--cov-fail-under=100`)
- Atomic commits: one logical change per commit

## Verification

Before committing, always run and show output:
```bash
python3 -m pytest tests/ -x -q -m "not live" --cov=pm_benchmark --cov-fail-under=100
```
