"""Tests for db.py - schema init, connection factory, transaction rollback."""
import sqlite3

import pytest

from db import get_connection, init_db, transaction


def test_init_db_creates_tables(tmp_db):
    conn = get_connection(tmp_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "albums" in tables
    assert "tracks" in tables


def test_init_db_idempotent(tmp_db):
    # Running init_db twice should not raise
    init_db(tmp_db)
    init_db(tmp_db)


def test_get_connection_row_factory(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (99, 'Test', 2000, 'Tester', '', '', '', '2024-01-01T00:00:00+00:00')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM albums WHERE discogs_id = 99").fetchone()
    conn.close()
    # Row factory should allow column-name access
    assert row["title"] == "Test"
    assert row["discogs_id"] == 99


def test_get_connection_wal_mode(tmp_db):
    conn = get_connection(tmp_db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_transaction_commits_on_success(tmp_db):
    with transaction(tmp_db) as conn:
        conn.execute(
            "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
            "VALUES (10, 'Commit Test', 1970, 'Artist', '', '', '', '2024-01-01T00:00:00+00:00')"
        )
    # Verify committed in a fresh connection
    conn2 = get_connection(tmp_db)
    row = conn2.execute("SELECT title FROM albums WHERE discogs_id = 10").fetchone()
    conn2.close()
    assert row is not None
    assert row["title"] == "Commit Test"


def test_transaction_rollback_on_exception(tmp_db):
    with pytest.raises(ValueError):
        with transaction(tmp_db) as conn:
            conn.execute(
                "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
                "VALUES (11, 'Rollback Test', 1970, 'Artist', '', '', '', '2024-01-01T00:00:00+00:00')"
            )
            raise ValueError("forced rollback")

    conn2 = get_connection(tmp_db)
    row = conn2.execute("SELECT title FROM albums WHERE discogs_id = 11").fetchone()
    conn2.close()
    assert row is None
