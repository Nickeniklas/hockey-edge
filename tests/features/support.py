"""Shared setup for the feature-store tests.

Source databases are temp files created from the real DDL and filled from
fixtures/features/mini.sql (real rows, scripts/make_feature_fixture.py).
Nothing here touches data/*.db.

The fixture is loaded and built once per test process (`shared`), not once
per class: the two source databases and the full features.db are made
read-only files, and each class opens its own query-only connection to the
full build, so no test can change what another one reads. A test that
needs edited sources works on a copy (edited_hockey, edited_snapshots) and
builds into its class's own temp directory.
"""

import atexit
import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from hockey_edge.features import build as feature_build
from hockey_edge.features import db as feature_db
from hockey_edge.ingest import db as ingest_db
from hockey_edge.snapshot import storage

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "features" / "mini.sql"

UNPLAYED = "started = 0, ended = 0, home_goals = 0, away_goals = 0, finished_type = 'ACTIVE_OR_NOT_STARTED'"


def make_sources(directory: Path) -> tuple[Path, Path]:
    hockey, snapshots = directory / "hockey.db", directory / "snapshots.db"
    ingest_db.get_connection(hockey).close()
    storage.get_connection(snapshots).close()
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ? AS hockey", (str(hockey),))
    conn.execute("ATTACH DATABASE ? AS snapshots", (str(snapshots),))
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    conn.close()
    return hockey, snapshots


_SHARED: tuple[Path, Path, Path] | None = None


def shared() -> tuple[Path, Path, Path]:
    """(hockey, snapshots, full features.db): the fixture loaded and built
    once per process, all three read-only files."""
    global _SHARED
    if _SHARED is None:
        tmp = tempfile.TemporaryDirectory(prefix="hockey-edge-fixture-", ignore_cleanup_errors=True)
        atexit.register(tmp.cleanup)
        directory = Path(tmp.name)
        hockey, snapshots = make_sources(directory)
        full = directory / "full.features.db"
        feature_build.build(full, hockey, snapshots)
        for path in (hockey, snapshots, full):
            os.chmod(path, stat.S_IREAD)
        _SHARED = hockey, snapshots, full
    return _SHARED


def copy_source(source: Path, target: Path) -> Path:
    """A writable copy (shutil.copy would carry the read-only flag over)."""
    shutil.copyfile(source, target)
    return target


def rows(conn: sqlite3.Connection, table: str, where: str = "1", params: tuple = ()) -> list[tuple]:
    return [
        tuple(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY 1, 2", params
        )
    ]


class FeatureTest(unittest.TestCase):
    """The shared fixture build as `cls.full`, and a temp directory per class."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        # Registered first, so it runs after every connection has closed.
        cls.addClassCleanup(tmp.cleanup)
        cls.dir = Path(tmp.name)
        cls.hockey, cls.snapshots, cls.full_path = shared()
        cls.full = feature_db.connect(cls.full_path, cls.hockey, cls.snapshots)
        cls.addClassCleanup(cls.full.close)
        cls.full.execute("PRAGMA query_only = ON")

    @classmethod
    def build_features(cls, name: str, hockey: Path | None = None, snapshots: Path | None = None):
        hockey, snapshots = hockey or cls.hockey, snapshots or cls.snapshots
        path = cls.dir / f"{name}.features.db"
        feature_build.build(path, hockey, snapshots)
        conn = feature_db.connect(path, hockey, snapshots)
        cls.addClassCleanup(conn.close)
        return conn

    @classmethod
    def _edited(cls, source: Path, path: Path, statements) -> Path:
        copy_source(source, path)
        conn = sqlite3.connect(path)
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
        conn.close()
        return path

    @classmethod
    def edited_hockey(cls, name: str, *statements: tuple[str, tuple]) -> Path:
        """A copy of the fixture hockey.db with SQL statements applied."""
        return cls._edited(cls.hockey, cls.dir / f"{name}.hockey.db", statements)

    @classmethod
    def edited_snapshots(cls, name: str, *statements: tuple[str, tuple]) -> Path:
        """A copy of the fixture snapshots.db with SQL statements applied."""
        return cls._edited(cls.snapshots, cls.dir / f"{name}.snapshots.db", statements)
