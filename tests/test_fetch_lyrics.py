"""
Tests for fetch_lyrics.py.

All HTTP calls and time.sleep are mocked - no network access needed.
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


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = ""
    return resp


# ── found ─────────────────────────────────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.requests.get")
def test_lyrics_found(mock_get, mock_sleep, tmp_db):
    _seed_track(tmp_db)
    mock_get.return_value = _mock_response(200, {"lyrics": "Hey Jude, don't make it bad…"})

    fetch_lyrics(tmp_db, batch_size=50)

    row = _get_track(tmp_db)
    assert row["lyrics_source"] == "lyrics_ovh"
    assert row["lyrics"] == "Hey Jude, don't make it bad…"
    assert row["lyrics_fetched_at"] is not None


# ── not found (404) ───────────────────────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.requests.get")
def test_lyrics_not_found(mock_get, mock_sleep, tmp_db):
    _seed_track(tmp_db)
    mock_get.return_value = _mock_response(404)

    fetch_lyrics(tmp_db, batch_size=50)

    row = _get_track(tmp_db)
    assert row["lyrics_source"] == "not_found"
    assert row["lyrics"] is None
    assert row["lyrics_fetched_at"] is not None


# ── not found (empty lyrics field) ───────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.requests.get")
def test_lyrics_empty_body(mock_get, mock_sleep, tmp_db):
    _seed_track(tmp_db)
    mock_get.return_value = _mock_response(200, {"lyrics": ""})

    fetch_lyrics(tmp_db, batch_size=50)

    row = _get_track(tmp_db)
    assert row["lyrics_source"] == "not_found"
    assert row["lyrics"] is None
    assert row["lyrics_fetched_at"] is not None


# ── request error marks track ─────────────────────────────────────────────────

@patch("fetch_lyrics.time.sleep")
@patch("fetch_lyrics.requests.get")
def test_request_error_marks_track(mock_get, mock_sleep, tmp_db):
    """Network errors should mark the track as 'error' and continue."""
    _seed_track(tmp_db)
    mock_get.side_effect = Exception("Connection timeout")

    fetch_lyrics(tmp_db, batch_size=50)

    row = _get_track(tmp_db)
    assert row["lyrics_source"] == "error"
    assert row["lyrics_fetched_at"] is not None
