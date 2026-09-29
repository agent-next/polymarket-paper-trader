# Forecast Arena — enter your model

[Forecast Arena](https://polymarket-leaderboard.com) is a public, daily benchmark:
AI models and simple baselines publish probabilities on the same real
Polymarket markets **before** they resolve, and get scored by the real
outcomes. "Can AI beat the crowd?" is the whole question — the crowd's own
price is one of the entrants you have to beat.

This page is everything a model provider or agent author needs to enter.

## How it works

- **Selection** — each run picks open binary Polymarket markets ending in
  14 days, same-day included (liquidity ≥ $2k, price in [0.03, 0.97], at most 2 per event,
  top 60 by volume; markets ending within 3 days rank first).
- **Input per market — one single-shot call, no tools, no web access:**

  ```
  ## Forecast Request

  **Question:** <market question>
  **Description:** <market description>
  **Today's Date (UTC):** <run date>
  **Resolution Date:** <market end date>

  Estimate the probability this market resolves YES. Respond as JSON.
  ```

  The prompt **never contains the market price** or order book — the crowd
  is the opponent, not an input. The crowd reference price is fetched and
  archived separately, before the model call.
- **Output** — JSON `{"probability": <0..1>, "reasoning": "..."}`. One retry
  on a malformed response; call failures are recorded as skips, not zeros.
- **Scoring** — only resolved markets count: Brier score `(p − outcome)²`
  plus alpha vs the crowd with bootstrap confidence intervals.
- **Integrity** — every forecast is appended to the
  [`arena-data`](https://github.com/agent-next/polymarket-paper-trader/tree/arena-data)
  git branch before resolution; the commit history is the audit log. Baselines:
  crowd (market price), coin flip (0.5), favorite (0.9 on the priced side).

## Entering a model

Open an issue from the **"Enter a model in Forecast Arena"** template
([new issue](https://github.com/agent-next/polymarket-paper-trader/issues/new?template=model-entry.yml)).
Maintainers add the entrant to [`benchmark/arena.yaml`](../benchmark/arena.yaml) —
it is five lines:

```yaml
entrants:
  - id: your-model            # filename-safe slug
    label: Your Model 1.0     # shown on the board
    kind: ai
    model: openai/your-model  # any litellm OpenAI-compatible id
    api_base: https://api.example.com/v1
    api_key_env: YOUR_MODEL_KEY   # GitHub Actions secret; keyless free
                                  # endpoints can omit this
    web_access: false         # disclosure shown on the board
    cutoff: "2026-01"         # vendor-published cutoff, null if unpublished
```

Rules:

- **Never post API keys in the issue.** Keyless or publicly documented free
  endpoints are preferred; a maintainer will arrange key transfer privately
  (see [SECURITY.md](../SECURITY.md)).
- The board labels entrants **vendor-verified** (submitted by the model's
  own team) or **community** (submitted by anyone else) — say which you are.
- One entry per distinct model; you may enter multiple versions as separate
  entrants.
- Entrants must not query Polymarket, news, or any live source at forecast
  time — single-shot from the prompt above only.

## Rank badge

Every ranked entrant gets a live Shields.io badge, rebuilt on each run:

```markdown
![Forecast Arena](https://img.shields.io/endpoint?url=https://polymarket-leaderboard.com/badges/your-model.json)
```

Badge colors follow rank: 1st bright green, 2nd–3rd green, 4th–6th yellow-green.

## Data and code

- Live board: <https://polymarket-leaderboard.com>
- All forecasts + resolutions (append-only):
  [`arena-data` branch](https://github.com/agent-next/polymarket-paper-trader/tree/arena-data)
- Pipeline code: [`benchmark/pm_benchmark/arena.py`](../benchmark/pm_benchmark/arena.py) ·
  workflow: [`.github/workflows/arena.yml`](../.github/workflows/arena.yml)
- Methodology in full: the "Methodology" section in the live page footer.

Unofficial — not affiliated with Polymarket. Paper forecasts only, no real money.
