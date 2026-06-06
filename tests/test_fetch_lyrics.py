"""
Tests for fetch_lyrics.py.

All Genius API calls and time.sleep are mocked — no real token needed.
"""
from unittest.mock import MagicMock, patch

from db import get_connection
from fetch_lyrics import fetch_lyrics


def _seed_track(db_path, track_id=1, album_id=100, title="Hey Jude", artist="The Beatles"):
    """Insert a minimal album + unprocessed track."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO albums "
        "(discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (?, 'Abbey Road', 1969, ?, '', '', '', '2024-01-01T00:00:00+00:00')",
        (album_id, artist),
    )
    conn.execute(
        "INSERT INTO tracks (id, album_id, position, title, artists) VALUES (?, ?, 'A1', ?, ?)",
        (track_id, album_id, title, artist),
    )
    conn.commit()
    conn.close()


def _get_track(db_path, track_id=1):
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    conn.close()
    return row


# ── found ─────────────────────────────────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.lyricsgenius.Genius")
def test_lyrics_found(MockGenius, mock_sleep, tmp_db):
    _seed_track(tmp_db)

    mock_song = MagicMock()
    mock_song.lyrics = "Hey Jude, don't make it bad…"
    instance = MockGenius.return_value
    instance.search_song.return_value = mock_song

    fetch_lyrics("fake-token", tmp_db, batch_size=50)

    row = _get_track(tmp_db)
    assert row["lyrics_source"] == "genius"
    assert row["lyrics"] == "Hey Jude, don't make it bad…"
    assert row["lyrics_fetched_at"] is not None


# ── not found ─────────────────────────────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.lyricsgenius.Genius")
def test_lyrics_not_found(MockGenius, mock_sleep, tmp_db):
    _seed_track(tmp_db)

    instance = MockGenius.return_value
    instance.search_song.return_value = None

    fetch_lyrics("fake-token", tmp_db, batch_size=50)

    row = _get_track(tmp_db)
    assert row["lyrics_source"] == "not_found"
    assert row["lyrics"] is None
    assert row["lyrics_fetched_at"] is not None


# ── 403 abort ─────────────────────────────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.lyricsgenius.Genius")
def test_403_aborts_run(MockGenius, mock_sleep, tmp_db):
    """A 403 exception should abort the run; the track must remain unprocessed."""
    _seed_track(tmp_db)

    instance = MockGenius.return_value
    instance.search_song.side_effect = Exception("403 Forbidden")

    fetch_lyrics("fake-token", tmp_db, batch_size=50)

    # The track was not committed as processed — it should still have NULL fetched_at
    row = _get_track(tmp_db)
    assert row["lyrics_fetched_at"] is None


# ── non-403 error marks track ──────────────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.lyricsgenius.Genius")
def test_generic_error_marks_track(MockGenius, mock_sleep, tmp_db):
    """Non-403 errors should mark the track as 'error' and continue."""
    _seed_track(tmp_db)

    instance = MockGenius.return_value
    instance.search_song.side_effect = Exception("Connection timeout")

    fetch_lyrics("fake-token", tmp_db, batch_size=50)

    row = _get_track(tmp_db)
    assert row["lyrics_source"] == "error"
    assert row["lyrics_fetched_at"] is not None
