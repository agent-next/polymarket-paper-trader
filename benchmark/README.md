[![CI](https://github.com/agent-next/polymarket-paper-trader/actions/workflows/test.yml/badge.svg)](https://github.com/agent-next/polymarket-paper-trader/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

# polymarket-benchmark

**SWE-bench for decision intelligence** — evaluate LLMs on real prediction market trading decisions.

polymarket-benchmark measures how well language models analyze, predict, and trade on [Polymarket](https://polymarket.com) events. Run any litellm-compatible model — or TypeSafe's [Jev](https://docs.typesafe.ai/) decision model — against curated market sets and get Brier scores, calibration error, alpha (model vs market edge), and a weighted composite score.

## Quick Start

```bash
pip install -e ".[dev]"
# Optional: install the leaderboard SDK for --leaderboard-url agent mode
pip install -e "../leaderboard-client"

# LLM-only evaluation (no trades)
polymarket-benchmark run --model claude-opus-4 --market-set mini

# Agent mode (trades on leaderboard)
polymarket-benchmark run --model claude-opus-4 --market-set mixed \
  --leaderboard-url https://leaderboard.example.com
```

## Jev (TypeSafe System One) predictions

Jev is a decision model: it evaluates a `state` against typed questions and returns
probabilities directly, instead of generating JSON for the harness to parse. Model ids
containing `jev` are routed to it automatically, so the probability it returns feeds the
existing Brier/calibration/alpha scoring unchanged.

```bash
# free model on OpenCode Zen, no key required
polymarket-benchmark run --model opencode/jev-1.13-free --market-set mini

# paid model, direct TypeSafe endpoint
JEV_BASE_URL=https://api.typesafe.ai/v1/systemone JEV_API_KEY=... \
  polymarket-benchmark run --model jev-1.13 --market-set mixed
```

| Env var | Default | Purpose |
|---------|---------|---------|
| `JEV_BASE_URL` | `https://opencode.ai/zen/v1/systemone` | System One endpoint |
| `JEV_API_KEY` / `OPENCODE_API_KEY` | _(none)_ | Bearer token; optional on the free model |

Jev is asked one `noul` (yes/no) probability question per market, plus `choice`
questions for action, confidence, and position size. A `none`/unrecognized size with a
non-skip action degrades to `skip`.

## Two Modes

| Mode | Flag | What happens |
|------|------|-------------|
| **LLM-only** | _(default)_ | Model predicts probabilities, scored against market prices |
| **Agent** | `--leaderboard-url URL` | Model predicts + executes trades via the in-repo [`leaderboard-client`](../leaderboard-client) SDK |

Weight profiles differ per mode — see [Scoring](#scoring-methodology).

## CLI Reference

| Command | Description |
|---------|-------------|
| `run` | Run a benchmark evaluation |
| `list-sets` | List available market sets |
| `score` | Re-score a previous run with optional resolved outcomes |
| `compare` | Compare multiple benchmark runs |
| `export` | Export results to external format (HuggingFace JSONL) |
| `history` | Show run history summary |
| `backfill` | Backfill resolved outcomes and re-score |

## Key `run` Options

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | _(required)_ | litellm model string, or a Jev id (`jev-1.13`, `opencode/jev-1.13-free`) |
| `--market-set` | `mini` | Market set name or YAML path |
| `--budget` | `10000` | Starting budget |
| `--temperature` | `0.3` | LLM temperature |
| `--n-rounds` | `1` | Number of evaluation rounds |
| `--blind` | `false` | Hide market prices from LLM |
| `--leaderboard-url` | _(none)_ | Enables agent mode with trading |
| `--verbose` | `false` | Show progress details |

## Scoring Methodology

### Brier Score (lower is better, 0–1)
Mean squared error between model probability and actual outcome. Uses market price as proxy truth when markets are unresolved.

### Alpha Score (negative is better)
`alpha = model_brier - market_brier`. Measures whether the model outperforms market consensus. Negative = model beats market.

### Calibration Error (lower is better, 0–1)
Expected Calibration Error (ECE). Bins predictions and compares average predicted probability vs average actual outcome.

### Composite Score (higher is better, 0–100)

**LLM-only mode** (no trades):

| Metric | Weight |
|--------|--------|
| Brier | 35% |
| Alpha | 30% |
| Calibration | 20% |
| Skip Rate | 15% |

**Agent mode** (with trades):

| Metric | Weight |
|--------|--------|
| ROI | 20% |
| Brier | 20% |
| Alpha | 15% |
| Sharpe | 10% |
| Win Rate | 10% |
| Calibration | 10% |
| Max Drawdown | 10% |
| Skip Rate | 5% |

## Market Sets

| Set | Markets | Description |
|-----|---------|-------------|
| `mini` | 3 | Quick smoke test |
| `crypto` | 5 | Cryptocurrency markets |
| `political` | 5 | Political events |
| `science` | 5 | Science & technology |
| `sports` | 5 | Sports outcomes |
| `mixed` | 15 | Cross-category benchmark |

Custom sets: create a YAML file and pass the path to `--market-set`.

## Forecast Arena

The Forecast Arena is a daily, self-running board answering *"can AI forecast
real-world events better than the crowd?"* AI entrants and naive baselines each
publish one YES probability per soon-resolving Polymarket binary market —
**before** it resolves — and are scored once outcomes land.

How it works:

- `predict` selects open binary markets ending in 1–14 days (liquidity ≥ $10k,
  YES price in [0.03, 0.97], at most 2 markets per Gamma event, top 20 by
  volume) and records one forecast per (entrant, market). AI entrants answer a
  single-shot prompt containing question + description + end date — **no market
  price**; the crowd is the opponent, not an input. Baselines are
  deterministic: `crowd` records the market price, `coin` 0.5, `favorite` 0.9
  toward the market favorite. Errors become `skip` rows, never 0.5.
- `resolve` records outcomes for forecast markets that have resolved
  (`resolutions.json`).
- `build` writes `site/data.json` — the leaderboard board: per-entrant Brier,
  ECE, alpha vs crowd with a bootstrap CI, open forecasts, duels (largest
  AI-vs-crowd disagreements), and a Hall of Wrong. When the
  `pm_benchmark.arena_site` renderer is installed it also writes
  `site/index.html` + `site/CNAME`.

Data is append-only JSONL under a data directory (the CI job uses a checked-out
`arena-data` branch): `forecasts/YYYY-MM-DD.jsonl` + `resolutions.json`.
Re-running `predict` the same day adds nothing.

```bash
# local run (hits the live Gamma API; needs model API keys for AI entrants)
polymarket-benchmark arena predict --data /tmp/arena-data
polymarket-benchmark arena resolve --data /tmp/arena-data
polymarket-benchmark arena build --data /tmp/arena-data --out site/
```

Entrants live in [`arena.yaml`](arena.yaml): `id`, `label`, `kind` (`ai` or
`baseline`), `model` (litellm id; `api_base`/`api_key_env` for custom endpoints
like GitHub Models), plus disclosure metadata (`web_access`, `cutoff`).

## Architecture

```
cli.py → runner.py → providers.py (LLM via litellm)
                   → market_info.py (Polymarket Gamma API)
                   → scoring.py (Brier, calibration, composite)
                   → report.py / export.py (output)

config.py — Pydantic models (LLMConfig, RunConfig)
market_set.py — YAML market set loader
prompts.py — LLM prompt templates
```

## Key Features

- **Multi-model**: Any litellm-compatible model (OpenAI, Anthropic, Gemini, local)
- **Multi-round**: Re-evaluate markets over time with `--n-rounds`
- **Blind mode**: Test without market price anchoring via `--blind`
- **Alpha scoring**: Measure model edge vs market consensus
- **Agent bridge**: Connect to a leaderboard server via the in-repo [`leaderboard-client`](../leaderboard-client) SDK
- **Resolution backfill**: Re-score old runs with true outcomes via `backfill`
- **HuggingFace export**: Share results in standard JSONL format
- **JSONL history**: Track runs over time

## Development

```bash
pip install -e ".[dev]"
# Optional: install the leaderboard SDK bridge for agent mode
pip install -e "../leaderboard-client"

# Run tests (215 total, 100% coverage)
python -m pytest tests/ -x -q -m "not live"

# With coverage
python -m pytest tests/ --cov=pm_benchmark --cov-report=term-missing
```

## License

MIT
