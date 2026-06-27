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
    aside               TEXT,
    bsides              TEXT,
    side                TEXT,
    year                INTEGER,
    fetched_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_track_singles_track ON track_singles(track_id);

CREATE TABLE IF NOT EXISTS lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS list_tracks (
    list_id    INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    track_id   INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0,
    added_at   TEXT    NOT NULL,
    PRIMARY KEY (list_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_list_tracks_list  ON list_tracks(list_id);
CREATE INDEX IF NOT EXISTS idx_list_tracks_track ON list_tracks(track_id);
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
                    aside               TEXT,
                    bsides              TEXT,
                    side                TEXT,
                    year                INTEGER,
                    fetched_at          TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_track_singles_track ON track_singles(track_id);
            """)
            conn.commit()
        except Exception:
            pass
        # Migration: add aside and side columns to track_singles
        for col in ("aside TEXT", "side TEXT"):
            try:
                conn.execute(f"ALTER TABLE track_singles ADD COLUMN {col}")
                conn.commit()
            except Exception:
                pass
        # Migration: add lists and list_tracks tables
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS lists (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT    NOT NULL,
                    created_at TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS list_tracks (
                    list_id    INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
                    track_id   INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                    position   INTEGER NOT NULL DEFAULT 0,
                    added_at   TEXT    NOT NULL,
                    PRIMARY KEY (list_id, track_id)
                );
                CREATE INDEX IF NOT EXISTS idx_list_tracks_list  ON list_tracks(list_id);
                CREATE INDEX IF NOT EXISTS idx_list_tracks_track ON list_tracks(track_id);
            """)
            conn.commit()
        except Exception:
            pass


def resolve_track_id(conn, title: str, artist: str):
    """Return the track id for a title/artist match in the local collection, or None."""
    row = conn.execute(
        """
        SELECT t.id FROM tracks t
        JOIN albums a ON a.discogs_id = t.album_id
        WHERE LOWER(t.title) = LOWER(?)
          AND (LOWER(a.artists_sort) = LOWER(?) OR LOWER(t.artists) = LOWER(?))
        LIMIT 1
        """,
        (title, artist, artist),
    ).fetchone()
    return row["id"] if row else None


def resolve_track_ids(conn, titles: list, artist: str) -> list:
    return [tid for t in titles if (tid := resolve_track_id(conn, t, artist)) is not None]


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
