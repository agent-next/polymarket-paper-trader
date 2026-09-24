# Provenance

This repository absorbs code that originated in other (private) repositories.
This file records where each imported subtree came from.

## leaderboard-server/ (imported 2026-09-24, merge `67e2dda`)

- **Source repo:** `agent-next/polymarket-platform` (private monorepo)
- **Source commit:** `54dd0fb`
- **Source path:** `packages/pm_leaderboard` -> `leaderboard-server/`
- **Method:** `git filter-repo` subtree extraction + unrelated-histories merge.
  The org-specific container-registry slug was rewritten to the
  `<your-registry>` placeholder during the rewrite, so history carries no
  private registry names.
- **Secrets:** all 42 imported commits scanned with gitleaks — no leaks.
- **Earlier origins:** the service began in `agent-next/polymarket-leaderboard`
  (`a88c876`); the benchmark harness began in `agent-next/polymarket-benchmark`
  (`29bf237`) and was imported earlier into `benchmark/` via PR #24.
