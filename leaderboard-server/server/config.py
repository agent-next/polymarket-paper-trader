"""Configuration from environment variables."""
from __future__ import annotations

import os

# SQLite path (or ":memory:" for ephemeral). PostgreSQL support planned.
DATABASE_URL = os.environ.get("DATABASE_URL", ":memory:")
LEADERBOARD_URL = os.environ.get(
    "LEADERBOARD_URL", "http://localhost:8000"
)
API_CACHE_DIR = os.environ.get(
    "API_CACHE_DIR", "/tmp/pm-leaderboard-cache"
)
CHECK_ORDERS_INTERVAL = int(os.environ.get("CHECK_ORDERS_INTERVAL", "60"))
AUTO_RESOLVE_INTERVAL = int(os.environ.get("AUTO_RESOLVE_INTERVAL", "300"))
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/data/backups")
BACKUP_INTERVAL = int(os.environ.get("BACKUP_INTERVAL", "3600"))
# Per-client-IP requests/minute on register and account creation; 0 disables.
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
# Honour X-Forwarded-For only when behind a trusted reverse proxy.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
