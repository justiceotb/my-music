"""
Tests for app.py Flask routes.

Uses Flask's test client. subprocess.Popen is mocked so no real subprocesses
spawn. DB_PATH is overridden via an env var before the app module is imported.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def flask_app(seeded_db):
    """Return a configured Flask test app pointing at the seeded DB."""
    # Set DB_PATH before importing app so the module-level variable picks it up
    os.environ["DB_PATH"] = seeded_db

    # Force re-import so DB_PATH is read fresh
    if "app" in sys.modules:
        del sys.modules["app"]
    import app as flask_app_module

    flask_app_module.app.config["TESTING"] = True
    # Also reset the module-level DB_PATH in case it was cached
    flask_app_module.DB_PATH = seeded_db

    yield flask_app_module.app, flask_app_module

    del os.environ["DB_PATH"]


@pytest.fixture
def client(flask_app):
    app, _ = flask_app
    with app.test_client() as c:
        yield c


@pytest.fixture
def app_module(flask_app):
    _, module = flask_app
    return module


# ── /api/stats ─────────────────────────────────────────────────────────────────

def test_stats(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "albums" in data
    assert "tracks" in data
    assert "lyrics_found" in data
    assert "summarised" in data
    assert data["albums"] == 2
    assert data["tracks"] == 3
    assert data["lyrics_found"] == 2   # Something + So What have lyrics_source='genius'
    assert data["summarised"] == 1     # Only So What has summary + ai_processed_at


# ── /api/albums ────────────────────────────────────────────────────────────────

def test_albums_returns_list(client):
    resp = client.get("/api/albums")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_albums_sort_by_year(client):
    resp = client.get("/api/albums?sort=year")
    assert resp.status_code == 200
    data = resp.get_json()
    years = [a["year"] for a in data]
    assert years == sorted(years)


# ── /api/tracks ────────────────────────────────────────────────────────────────

def test_tracks_returns_paginated(client):
    resp = client.get("/api/tracks")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "total" in data
    assert "tracks" in data
    assert data["total"] == 3


def test_tracks_filter_by_album_id(client):
    resp = client.get("/api/tracks?album_id=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 2


def test_tracks_search_query(client):
    resp = client.get("/api/tracks?q=Come")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["tracks"][0]["title"] == "Come Together"


def test_tracks_filter_by_tag(client):
    resp = client.get("/api/tracks?tag=jazz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 1
    assert data["tracks"][0]["title"] == "So What"


# ── /api/track/<id> ────────────────────────────────────────────────────────────

def test_track_detail(client):
    resp = client.get("/api/track/1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["title"] == "Come Together"
    assert "album" in data


def test_track_not_found(client):
    resp = client.get("/api/track/9999")
    assert resp.status_code == 404


# ── /api/tags ──────────────────────────────────────────────────────────────────

def test_tags(client):
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    tags = [item["tag"] for item in data]
    assert "jazz" in tags
    assert "improvisation" in tags


# ── /api/fetch-lyrics ─────────────────────────────────────────────────────────

def test_fetch_lyrics_starts_job(client, app_module):
    with patch.object(app_module, "_start_job") as mock_start:
        resp = client.post("/api/fetch-lyrics", json={"batch": 10})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["job_id"] == "fetch_lyrics"
    mock_start.assert_called_once()


# ── /api/job/<id> ──────────────────────────────────────────────────────────────

def test_job_not_started(client):
    resp = client.get("/api/job/nonexistent")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "not_started"


def test_job_status(client, app_module):
    app_module._jobs["test_job"] = {"status": "running", "output": "hello\n"}
    resp = client.get("/api/job/test_job")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "running"
    assert "hello" in data["output"]


# ── /api/stop/<id> ─────────────────────────────────────────────────────────────

def test_stop_running_job(client, app_module):
    mock_proc = MagicMock()
    app_module._procs["myjob"] = mock_proc
    app_module._jobs["myjob"] = {"status": "running", "output": ""}

    resp = client.post("/api/stop/myjob")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    mock_proc.terminate.assert_called_once()
    assert app_module._jobs["myjob"]["status"] == "stopped"


def test_stop_job_not_running(client):
    resp = client.post("/api/stop/ghost_job")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False


# ── /api/sync requires token ───────────────────────────────────────────────────

def test_sync_requires_discogs_token(client):
    os.environ.pop("DISCOGS_TOKEN", None)
    resp = client.post("/api/sync")
    assert resp.status_code == 400
