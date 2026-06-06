"""
Tests for import_discogs.py.

The discogs_client library is fully mocked — no real Discogs token needed.
"""
from unittest.mock import MagicMock, patch

from db import get_connection
from import_discogs import import_collection


def _make_track(position, title, artists=None):
    t = MagicMock()
    t.position = position
    t.title = title
    t.artists = artists or []
    return t


def _make_release(rid, title, year, artists_sort, styles, formats, notes_value, tracklist):
    release = MagicMock()
    release.id = rid
    release.title = title
    release.year = year
    release.artists_sort = artists_sort
    release.styles = styles
    release.formats = formats
    release.tracklist = tracklist

    # Simulate release.notes[2]["value"]
    notes_mock = {2: {"value": notes_value}}
    release.notes = notes_mock

    collection_item = MagicMock()
    collection_item.release = release
    collection_item.notes = notes_mock
    return collection_item


def _make_discogs_client(releases):
    mock_client = MagicMock()
    mock_client.identity.return_value.name = "testuser"
    mock_client.me.collection_folders = [MagicMock()]
    mock_client.me.collection_folders[0].releases = releases
    # identity() returns me
    mock_client.identity.return_value = mock_client.me
    return mock_client


def _build_client_with_releases(releases):
    """Build a mock discogs_client.Client that yields the given release items."""
    client = MagicMock()
    me = MagicMock()
    me.name = "testuser"
    client.identity.return_value = me
    folder = MagicMock()
    folder.releases = releases
    me.collection_folders = [folder]
    return client


# ── basic import ──────────────────────────────────────────────────────────────

@patch("import_discogs.time.sleep")
@patch("import_discogs.discogs_client.Client")
def test_import_inserts_album_and_tracks(MockClient, mock_sleep, tmp_db):
    track1 = _make_track("A1", "Come Together")
    track2 = _make_track("B1", "Something")
    item = _make_release(
        rid=1,
        title="Abbey Road",
        year=1969,
        artists_sort="Beatles, The",
        styles=["Rock", "Pop"],
        formats=[{"name": "Vinyl", "descriptions": ["LP"]}],
        notes_value="",
        tracklist=[track1, track2],
    )

    MockClient.return_value = _build_client_with_releases([item])

    import_collection("fake-token", tmp_db)

    conn = get_connection(tmp_db)
    album = conn.execute("SELECT * FROM albums WHERE discogs_id = 1").fetchone()
    tracks = conn.execute("SELECT * FROM tracks WHERE album_id = 1").fetchall()
    conn.close()

    assert album is not None
    assert album["title"] == "Abbey Road"
    assert album["year"] == 1969
    assert album["artists_sort"] == "Beatles, The"
    assert len(tracks) == 2
    assert tracks[0]["title"] == "Come Together"
    assert tracks[1]["title"] == "Something"


# ── deduplication within one run ─────────────────────────────────────────────

@patch("import_discogs.time.sleep")
@patch("import_discogs.discogs_client.Client")
def test_duplicate_release_in_response_ignored(MockClient, mock_sleep, tmp_db):
    item = _make_release(
        rid=5, title="Kind of Blue", year=1959,
        artists_sort="Davis, Miles", styles=["Jazz"],
        formats=[{"name": "Vinyl", "descriptions": ["LP"]}],
        notes_value="", tracklist=[_make_track("A1", "So What")],
    )

    # Same release appears twice in the Discogs response
    MockClient.return_value = _build_client_with_releases([item, item])

    import_collection("fake-token", tmp_db)

    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM albums WHERE discogs_id = 5").fetchone()[0]
    conn.close()
    assert count == 1


# ── incremental: skip existing ────────────────────────────────────────────────

@patch("import_discogs.time.sleep")
@patch("import_discogs.discogs_client.Client")
def test_existing_album_skipped(MockClient, mock_sleep, tmp_db):
    # Pre-insert the album
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (7, 'Existing Album', 2000, 'Artist', '', '', '', '2024-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    item = _make_release(
        rid=7, title="Existing Album", year=2000,
        artists_sort="Artist", styles=[],
        formats=[], notes_value="", tracklist=[],
    )
    MockClient.return_value = _build_client_with_releases([item])

    import_collection("fake-token", tmp_db)

    conn = get_connection(tmp_db)
    count = conn.execute("SELECT COUNT(*) FROM albums WHERE discogs_id = 7").fetchone()[0]
    conn.close()
    assert count == 1  # unchanged — not doubled


# ── styles joined correctly ───────────────────────────────────────────────────

@patch("import_discogs.time.sleep")
@patch("import_discogs.discogs_client.Client")
def test_styles_joined(MockClient, mock_sleep, tmp_db):
    item = _make_release(
        rid=9, title="Thriller", year=1982,
        artists_sort="Jackson, Michael", styles=["Pop", "R&B", "Funk"],
        formats=[{"name": "Vinyl", "descriptions": ["LP"]}],
        notes_value="", tracklist=[],
    )
    MockClient.return_value = _build_client_with_releases([item])

    import_collection("fake-token", tmp_db)

    conn = get_connection(tmp_db)
    album = conn.execute("SELECT styles FROM albums WHERE discogs_id = 9").fetchone()
    conn.close()
    assert album["styles"] == "Pop R&B Funk"
