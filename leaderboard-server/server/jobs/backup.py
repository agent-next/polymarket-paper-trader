"""Background job: automated SQLite backups."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from server.db import DB


def backup_db(db: DB, backup_dir: str) -> str:
    """Copy database to backup_dir using SQLite's safe .backup() API.

    Returns the backup file path.
    """
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = os.path.join(backup_dir, f"leaderboard-{ts}.db")
    dest_conn = sqlite3.connect(dest)
    try:
        db._conn.backup(dest_conn)
    finally:
        dest_conn.close()
    _rotate_backups(backup_dir, keep=24)
    return dest


def _rotate_backups(backup_dir: str, keep: int = 24) -> None:
    """Keep only the most recent `keep` backups, delete older ones."""
    backups = sorted(Path(backup_dir).glob("leaderboard-*.db"))
    for old in backups[:-keep]:
        old.unlink()
