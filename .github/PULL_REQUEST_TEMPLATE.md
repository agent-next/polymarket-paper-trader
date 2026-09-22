## What

<!-- One or two sentences on what changed and why. -->

## Package

- [ ] `polymarket-paper-trader` (root)
- [ ] `polymarket-benchmark`
- [ ] `polymarket-leaderboard-client`

## How verified

<!-- Commands you ran and their result. -->

```bash
python3 -m pytest tests/ -x -q -m "not live"
```

## Checklist

- [ ] Tests added or updated for the change
- [ ] The changed package's test suite passes at 100% coverage
- [ ] No secrets, tokens, or `.env` files included
