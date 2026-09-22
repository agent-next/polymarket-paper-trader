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
