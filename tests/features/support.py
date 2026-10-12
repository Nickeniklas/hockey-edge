"""Shared setup for the feature-store tests.

Source databases are temp files created from the real DDL and filled from
fixtures/features/mini.sql (real rows, scripts/make_feature_fixture.py).
Nothing here touches data/*.db.
"""

import shutil
import sqlite3
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


def rows(conn: sqlite3.Connection, table: str, where: str = "1", params: tuple = ()) -> list[tuple]:
    return [
        tuple(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY 1, 2", params
        )
    ]


class FeatureTest(unittest.TestCase):
    """Builds features from the fixture once per class, as `cls.full`."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        # Registered first, so it runs after every connection has closed.
        cls.addClassCleanup(tmp.cleanup)
        cls.dir = Path(tmp.name)
        cls.hockey, cls.snapshots = make_sources(cls.dir)
        cls.full = cls.build_features("full")

    @classmethod
    def build_features(cls, name: str, hockey: Path | None = None, snapshots: Path | None = None):
        hockey, snapshots = hockey or cls.hockey, snapshots or cls.snapshots
        path = cls.dir / f"{name}.features.db"
        feature_build.build(path, hockey, snapshots)
        conn = feature_db.connect(path, hockey, snapshots)
        cls.addClassCleanup(conn.close)
        return conn

    @classmethod
    def edited_hockey(cls, name: str, *statements: tuple[str, tuple]) -> Path:
        """A copy of the fixture hockey.db with SQL statements applied."""
        path = cls.dir / f"{name}.hockey.db"
        shutil.copy(cls.hockey, path)
        conn = sqlite3.connect(path)
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
        conn.close()
        return path
