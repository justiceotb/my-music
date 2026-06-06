"""
Tests for enrich_discogs.py.

Discogs API is fully mocked — no real token needed.
"""
from unittest.mock import MagicMock, patch

from db import get_connection
from enrich_discogs import enrich_collection


def _seed_incomplete_album(db_path, discogs_id=1, artists_sort="", year=None, styles=""):
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (?, 'Test Album', ?, ?, ?, '', '', '2024-01-01T00:00:00+00:00')",
        (discogs_id, year, artists_sort, styles),
    )
    conn.commit()
    conn.close()


def _make_client(discogs_id, artists_sort, year, styles, formats=None, artists=None):
    client = MagicMock()
    me = MagicMock()
    me.name = "testuser"
    client.identity.return_value = me

    release = MagicMock()
    release.artists_sort = artists_sort
    release.year = year
    release.styles = styles
    release.formats = formats or []
    if artists is not None:
        release.artists = [MagicMock(name=a) for a in artists]
    client.release.return_value = release
    return client


# ── fills in missing fields ───────────────────────────────────────────────────

@patch("enrich_discogs.time.sleep")
@patch("enrich_discogs.discogs_client.Client")
def test_fills_missing_artist_and_year(MockClient, mock_sleep, tmp_db):
    _seed_incomplete_album(tmp_db, discogs_id=1, artists_sort="", year=None)

    MockClient.return_value = _make_client(
        discogs_id=1,
        artists_sort="Beatles, The",
        year=1969,
        styles=["Rock"],
    )

    enrich_collection("fake-token", tmp_db)

    conn = get_connection(tmp_db)
    row = conn.execute("SELECT artists_sort, year, styles FROM albums WHERE discogs_id = 1").fetchone()
    conn.close()

    assert row["artists_sort"] == "Beatles, The"
    assert row["year"] == 1969
    assert row["styles"] == "Rock"


# ── does not overwrite existing good data ─────────────────────────────────────

@patch("enrich_discogs.time.sleep")
@patch("enrich_discogs.discogs_client.Client")
def test_does_not_overwrite_existing_artist(MockClient, mock_sleep, tmp_db):
    # Seed with styles missing but artists_sort already present
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (2, 'Album', 1970, 'Existing Artist', '', '', '', '2024-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    MockClient.return_value = _make_client(
        discogs_id=2,
        artists_sort="Different Artist",
        year=1970,
        styles=["Jazz"],
    )

    enrich_collection("fake-token", tmp_db)

    conn = get_connection(tmp_db)
    row = conn.execute("SELECT artists_sort FROM albums WHERE discogs_id = 2").fetchone()
    conn.close()
    # COALESCE(NULLIF(artists_sort, ''), ?) keeps the existing non-empty value
    assert row["artists_sort"] == "Existing Artist"


# ── nothing to do ─────────────────────────────────────────────────────────────

@patch("enrich_discogs.time.sleep")
@patch("enrich_discogs.discogs_client.Client")
def test_nothing_to_do_when_all_complete(MockClient, mock_sleep, seeded_db):
    """seeded_db albums already have artists_sort, year, and styles — nothing to enrich."""
    client = MagicMock()
    me = MagicMock()
    me.name = "testuser"
    client.identity.return_value = me
    MockClient.return_value = client

    enrich_collection("fake-token", seeded_db)

    # release() should never be called because there are no incomplete albums
    client.release.assert_not_called()


# ── fallback artist from artists list ────────────────────────────────────────

@patch("enrich_discogs.time.sleep")
@patch("enrich_discogs.discogs_client.Client")
def test_falls_back_to_artists_list(MockClient, mock_sleep, tmp_db):
    _seed_incomplete_album(tmp_db, discogs_id=3, artists_sort="", year=None)

    # artists_sort is empty on the release but artists list has names
    client = MagicMock()
    me = MagicMock()
    me.name = "testuser"
    client.identity.return_value = me

    release = MagicMock()
    release.artists_sort = ""
    release.year = 2000
    release.styles = []
    release.formats = []
    a1, a2 = MagicMock(), MagicMock()
    a1.name = "Artist One"
    a2.name = "Artist Two"
    release.artists = [a1, a2]
    client.release.return_value = release
    MockClient.return_value = client

    enrich_collection("fake-token", tmp_db)

    conn = get_connection(tmp_db)
    row = conn.execute("SELECT artists_sort FROM albums WHERE discogs_id = 3").fetchone()
    conn.close()
    assert row["artists_sort"] == "Artist One / Artist Two"
