"""
Shared database helpers - schema creation and connection factory.
"""
import os
import sqlite3
from contextlib import contextmanager

from version import __version__

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
    summary_casual    TEXT,
    theme_tags        TEXT,
    ai_processed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracks_album   ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_lyrics  ON tracks(lyrics_fetched_at);
CREATE INDEX IF NOT EXISTS idx_tracks_ai      ON tracks(ai_processed_at);

CREATE TABLE IF NOT EXISTS tag_themes (
    tag   TEXT PRIMARY KEY,
    theme TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS track_singles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id            INTEGER NOT NULL REFERENCES tracks(id),
    discogs_release_id  INTEGER,
    single_title        TEXT,
    bsides              TEXT,
    year                INTEGER,
    fetched_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_track_singles_track ON track_singles(track_id);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    """Create tables if they don't exist yet, and migrate existing schemas."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        # Migration: add summary_casual column to existing databases
        try:
            conn.execute("ALTER TABLE tracks ADD COLUMN summary_casual TEXT")
            conn.commit()
        except Exception:
            pass  # Column already exists
        # Migration: add tag_themes table
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tag_themes (
                    tag   TEXT PRIMARY KEY,
                    theme TEXT NOT NULL
                );
            """)
            conn.commit()
        except Exception:
            pass
        # Migration: add singles_checked_at to tracks
        try:
            conn.execute("ALTER TABLE tracks ADD COLUMN singles_checked_at TEXT")
            conn.commit()
        except Exception:
            pass
        # Migration: add track_singles table
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS track_singles (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id            INTEGER NOT NULL REFERENCES tracks(id),
                    discogs_release_id  INTEGER,
                    single_title        TEXT,
                    bsides              TEXT,
                    year                INTEGER,
                    fetched_at          TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_track_singles_track ON track_singles(track_id);
            """)
            conn.commit()
        except Exception:
            pass


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
