"""
Shared database helpers — schema creation and connection factory.
"""
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS albums (
    discogs_id   INTEGER PRIMARY KEY,
    title        TEXT    NOT NULL,
    year         INTEGER,
    artists_sort TEXT,
    styles       TEXT,
    format       TEXT,
    notes        TEXT,
    imported_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id          INTEGER NOT NULL REFERENCES albums(discogs_id),
    position          TEXT,
    title             TEXT    NOT NULL,
    artists           TEXT,
    lyrics            TEXT,
    lyrics_fetched_at TEXT,
    lyrics_source     TEXT,
    summary           TEXT,
    theme_tags        TEXT,
    ai_processed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracks_album   ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_lyrics  ON tracks(lyrics_fetched_at);
CREATE INDEX IF NOT EXISTS idx_tracks_ai      ON tracks(ai_processed_at);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    """Create tables if they don't exist yet."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def transaction(db_path: str):
    """Yield a connection inside a transaction; commit on success, rollback on error."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
