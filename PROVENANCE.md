# Provenance

Where imported subtrees in this repository came from.

## leaderboard-server/ (imported 2026-09-24, merge `67e2dda`)

- Developed in a separate, now-retired agent-next repository and imported with its full
  git history (`git filter-repo` subtree rewrite + unrelated-histories merge), so
  `git log -- leaderboard-server/` shows the original commits and authors.
- An org-specific container-registry name was replaced by the `<your-registry>`
  placeholder throughout the imported history.
- All 42 imported commits were scanned with gitleaks before import: no leaks.

## benchmark/ and leaderboard-client/ (imported 2026-09-22, PR #24)

- Moved in from retired agent-next repositories; see PR #24.
