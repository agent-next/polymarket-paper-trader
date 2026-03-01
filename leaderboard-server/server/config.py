"""Configuration from environment variables."""
from __future__ import annotations

import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://localhost:5432/leaderboard"
)
LEADERBOARD_URL = os.environ.get(
    "LEADERBOARD_URL", "http://localhost:8000"
)
API_CACHE_DIR = os.environ.get(
    "API_CACHE_DIR", "/tmp/pm-leaderboard-cache"
)
