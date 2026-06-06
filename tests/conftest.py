"""
Shared pytest fixtures for my-music tests.
All tests use a temporary on-disk SQLite DB so no real credentials are needed.
"""
import os
import sys
import tempfile

import pytest

# Ensure the project root is on sys.path so imports work when running from
# anywhere (e.g. `pytest tests/` from the repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db import init_db, get_connection


@pytest.fixture
def tmp_db(tmp_path):
    """Empty initialised database in a temporary directory."""
    db_path = str(tmp_path / "test_music.db")
    init_db(db_path)
    yield db_path


@pytest.fixture
def seeded_db(tmp_db):
    """Database pre-populated with 2 albums and 3 tracks for query tests."""
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (1, 'Abbey Road', 1969, 'Beatles, The', 'Rock', 'Vinyl LP', '', '2024-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (2, 'Kind of Blue', 1959, 'Davis, Miles', 'Jazz', 'Vinyl LP', '', '2024-01-01T00:00:00+00:00')"
    )
    # Track with no lyrics yet (unprocessed)
    conn.execute(
        "INSERT INTO tracks (album_id, position, title, artists) VALUES (1, 'A1', 'Come Together', 'The Beatles')"
    )
    # Track with lyrics but no summary
    conn.execute(
        "INSERT INTO tracks (album_id, position, title, artists, lyrics, lyrics_fetched_at, lyrics_source) "
        "VALUES (1, 'B1', 'Something', 'The Beatles', 'I dont know', '2024-01-01T00:00:00+00:00', 'genius')"
    )
    # Fully processed track
    conn.execute(
        "INSERT INTO tracks (album_id, position, title, artists, lyrics, lyrics_fetched_at, lyrics_source, "
        "summary, theme_tags, ai_processed_at) "
        "VALUES (2, 'A1', 'So What', 'Miles Davis', 'instrumental', '2024-01-01T00:00:00+00:00', 'genius', "
        "'A modal jazz landmark.', '[\"jazz\",\"improvisation\"]', '2024-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    yield tmp_db
