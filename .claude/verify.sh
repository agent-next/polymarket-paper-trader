#!/usr/bin/env bash
# .claude/verify.sh — this repo's verify contract (the pre-commit test gate's
# highest-priority runner). Runs the product suite with the project venv when
# present, CI scope: tests/ only — benchmark/ and leaderboard-client/ carry
# their own pytest configs and CI jobs (toolkit.yml).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
# -m "not live": the live e2e suite needs Polymarket API egress (GitHub runners,
# live.yml); on dev boxes it would fail on network, not on code.
exec "$PY" -m pytest -x --maxfail=3 --tb=line -q --no-header tests/ -m "not live"
