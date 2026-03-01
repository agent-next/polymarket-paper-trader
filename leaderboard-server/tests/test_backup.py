"""Test SQLite backup job."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from server.db import DB
from server.jobs.backup import backup_db, _rotate_backups


@pytest.fixture
def db():
    _db = DB(":memory:")
    _db.init_schema()
    yield _db
    _db.close()


@pytest.fixture
def backup_dir(tmp_path):
    return str(tmp_path / "backups")


class TestBackupDb:
    def test_creates_backup_file(self, db, backup_dir):
        path = backup_db(db, backup_dir)
        assert os.path.exists(path)
        assert path.startswith(backup_dir)
        assert "leaderboard-" in path
        assert path.endswith(".db")

    def test_backup_contains_data(self, db, backup_dir):
        """Backup should be a valid SQLite DB with same data."""
        db.create_user("backup-test-bot")
        path = backup_db(db, backup_dir)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE agent_name = ?", ("backup-test-bot",)).fetchone()
        conn.close()
        assert row is not None
        assert dict(row)["agent_name"] == "backup-test-bot"

    def test_creates_backup_dir(self, tmp_path):
        """Backup dir is created if it doesn't exist."""
        _db = DB(":memory:")
        _db.init_schema()
        nested = str(tmp_path / "a" / "b" / "c")
        path = backup_db(_db, nested)
        assert os.path.exists(path)
        _db.close()

    def test_multiple_backups_unique_names(self, db, backup_dir):
        """Each backup gets a unique timestamp-based name."""
        p1 = backup_db(db, backup_dir)
        # Force a different timestamp by renaming
        p2_name = p1.replace(".db", "-2.db")
        os.rename(p1, p2_name)
        p2 = backup_db(db, backup_dir)
        assert p2 != p2_name


class TestRotateBackups:
    def test_keeps_latest_n(self, tmp_path):
        backup_dir = str(tmp_path)
        # Create 30 fake backup files
        for i in range(30):
            (tmp_path / f"leaderboard-2026010{i:02d}T000000Z.db").touch()
        _rotate_backups(backup_dir, keep=24)
        remaining = list(tmp_path.glob("leaderboard-*.db"))
        assert len(remaining) == 24

    def test_keeps_all_when_under_limit(self, tmp_path):
        backup_dir = str(tmp_path)
        for i in range(5):
            (tmp_path / f"leaderboard-2026010{i}T000000Z.db").touch()
        _rotate_backups(backup_dir, keep=24)
        remaining = list(tmp_path.glob("leaderboard-*.db"))
        assert len(remaining) == 5

    def test_rotation_keeps_newest(self, tmp_path):
        """Oldest files are deleted, newest kept."""
        backup_dir = str(tmp_path)
        names = []
        for i in range(5):
            name = f"leaderboard-2026010{i}T000000Z.db"
            (tmp_path / name).touch()
            names.append(name)
        _rotate_backups(backup_dir, keep=3)
        remaining = {p.name for p in tmp_path.glob("leaderboard-*.db")}
        # Should keep the last 3 (sorted alphabetically = chronologically)
        assert remaining == {names[2], names[3], names[4]}
