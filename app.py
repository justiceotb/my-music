"""
app.py - Flask web UI for the music collection.

Run locally:
    python app.py

Or via Docker (see docker-compose.yml). Environment variables:
    DISCOGS_TOKEN     - for the Sync action
    ANTHROPIC_API_KEY - for Claude summarise mode (optional)
    OLLAMA_HOST       - Ollama server URL (default http://localhost:11434)
    OLLAMA_MODEL      - Ollama model name (default llama3)
    DB_PATH           - SQLite path (default music.db)
"""
import json
import os
import subprocess
import sys
from threading import Thread

from flask import Flask, jsonify, render_template, request

from db import init_db, get_connection
from version import __version__

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "music.db")

# Simple in-process job tracker so the UI can poll status
_jobs: dict[str, dict] = {}
_procs: dict[str, subprocess.Popen] = {}


def _run_job(job_id: str, cmd: list[str]) -> None:
    _jobs[job_id] = {"status": "running", "output": ""}
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        _procs[job_id] = proc
        for line in proc.stdout:
            _jobs[job_id]["output"] += line
            sys.stdout.write(f"[{job_id}] {line}")
            sys.stdout.flush()
        proc.wait(timeout=3600)
        if _jobs[job_id]["status"] != "stopped":
            _jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
    except Exception as ex:
        if _jobs[job_id]["status"] != "stopped":
            _jobs[job_id]["status"] = "error"
        _jobs[job_id]["output"] += str(ex)
    finally:
        _procs.pop(job_id, None)


def _start_job(job_id: str, cmd: list[str]) -> None:
    Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", version=__version__)


# ──────────────────────────────────────────────
# API - data
# ──────────────────────────────────────────────

@app.route("/api/albums")
def api_albums():
    sort = request.args.get("sort", "artist")
    order = {
        "artist": "artists_sort COLLATE NOCASE, year",
        "album":  "title COLLATE NOCASE",
        "year":   "year, artists_sort COLLATE NOCASE",
    }.get(sort, "artists_sort COLLATE NOCASE, year")
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        f"SELECT discogs_id, title, year, artists_sort FROM albums ORDER BY {order}"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/stats")
def api_stats():
    conn = get_connection(DB_PATH)
    stats = {
        "albums": conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
        "tracks": conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
        "lyrics_found": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE lyrics_source = 'genius'"
        ).fetchone()[0],
        "summarised": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE ai_processed_at IS NOT NULL AND summary != ''"
        ).fetchone()[0],
    }
    conn.close()
    return jsonify(stats)


@app.route("/api/tracks")
def api_tracks():
    album_id = request.args.get("album_id", type=int)
    q = (request.args.get("q") or "").strip()
    tag = (request.args.get("tag") or "").strip()
    filter_mode = (request.args.get("filter") or "").strip()
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "artist")
    per_page = 50

    conn = get_connection(DB_PATH)

    clauses = []
    params: list = []

    if album_id:
        clauses.append("t.album_id = ?")
        params.append(album_id)

    if q:
        clauses.append(
            "(t.title LIKE ? OR t.artists LIKE ? OR a.title LIKE ? OR t.lyrics LIKE ? OR t.summary LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like, like])

    if tag:
        # theme_tags is a JSON array stored as text; LIKE is good enough for tags
        clauses.append("t.theme_tags LIKE ?")
        params.append(f'%"{tag}"%')

    if filter_mode == "has_lyrics":
        clauses.append("t.lyrics_source = 'lyrics_ovh'")
    elif filter_mode == "no_lyrics":
        clauses.append("(t.lyrics_source IN ('not_found', 'error') OR t.lyrics_source IS NULL)")
    elif filter_mode == "has_tags":
        clauses.append("(t.theme_tags IS NOT NULL AND t.theme_tags != '[]')")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    track_order = {
        "artist": "a.artists_sort COLLATE NOCASE, a.year, t.id",
        "album":  "a.title COLLATE NOCASE, t.id",
        "year":   "a.year, a.artists_sort COLLATE NOCASE, t.id",
        "song":   "t.title COLLATE NOCASE, a.artists_sort COLLATE NOCASE",
    }.get(sort, "a.artists_sort COLLATE NOCASE, a.year, t.id")

    total = conn.execute(
        f"SELECT COUNT(*) FROM tracks t JOIN albums a ON a.discogs_id = t.album_id {where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"""
        SELECT t.id, t.title, t.position, t.artists,
               a.title as album, a.artists_sort, a.year,
               t.lyrics_source, t.summary, t.theme_tags,
               t.ai_processed_at
        FROM tracks t
        JOIN albums a ON a.discogs_id = t.album_id
        {where}
        ORDER BY {track_order}
        LIMIT ? OFFSET ?
        """,
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    conn.close()

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "tracks": [dict(r) for r in rows],
    })


@app.route("/api/track/<int:track_id>")
def api_track(track_id: int):
    conn = get_connection(DB_PATH)
    row = conn.execute(
        """
        SELECT t.*, a.title as album, a.artists_sort, a.year, a.styles
        FROM tracks t JOIN albums a ON a.discogs_id = t.album_id
        WHERE t.id = ?
        """,
        (track_id,),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


@app.route("/api/tags")
def api_tags():
    """Return all distinct theme tags sorted by frequency."""
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        "SELECT theme_tags FROM tracks WHERE theme_tags IS NOT NULL AND theme_tags != ''"
    ).fetchall()
    conn.close()

    counts: dict[str, int] = {}
    for row in rows:
        try:
            for tag in json.loads(row["theme_tags"]):
                counts[tag] = counts.get(tag, 0) + 1
        except Exception:
            pass

    sorted_tags = sorted(counts.items(), key=lambda x: -x[1])
    return jsonify([{"tag": t, "count": c} for t, c in sorted_tags])


# ──────────────────────────────────────────────
# API - actions (run background scripts)
# ──────────────────────────────────────────────

@app.route("/api/sync", methods=["POST"])
def api_sync():
    token = os.environ.get("DISCOGS_TOKEN")
    if not token:
        return jsonify({"error": "DISCOGS_TOKEN not set"}), 400
    job_id = "sync"
    _start_job(job_id, [sys.executable, "import_discogs.py", "--token", token, "--db", DB_PATH])
    return jsonify({"job_id": job_id})


@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    token = os.environ.get("DISCOGS_TOKEN")
    if not token:
        return jsonify({"error": "DISCOGS_TOKEN not set"}), 400
    job_id = "enrich"
    _start_job(job_id, [sys.executable, "enrich_discogs.py", "--token", token, "--db", DB_PATH])
    return jsonify({"job_id": job_id})


@app.route("/api/fetch-lyrics", methods=["POST"])
def api_fetch_lyrics():
    job_id = "fetch_lyrics"
    batch = str(request.json.get("batch", 50)) if request.is_json else "50"
    _start_job(job_id, [sys.executable, "fetch_lyrics_synced.py", "--db", DB_PATH, "--batch", batch])
    return jsonify({"job_id": job_id})


@app.route("/api/summarise", methods=["POST"])
def api_summarise():
    data = request.json or {}
    model_type = data.get("model_type", "ollama")
    ollama_model = data.get("ollama_model", os.environ.get("OLLAMA_MODEL", "llama3"))
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    batch = str(data.get("batch", 20))

    cmd = [
        sys.executable, "summarise.py",
        "--model-type", model_type,
        "--ollama-model", ollama_model,
        "--ollama-host", ollama_host,
        "--db", DB_PATH,
        "--batch", batch,
    ]
    if model_type == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400

    _start_job("summarise", cmd)
    return jsonify({"job_id": "summarise"})


@app.route("/api/job/<job_id>")
def api_job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_started"})
    return jsonify(job)


@app.route("/api/stop/<job_id>", methods=["POST"])
def api_stop_job(job_id: str):
    proc = _procs.get(job_id)
    if proc:
        proc.terminate()
        if job_id in _jobs:
            _jobs[job_id]["status"] = "stopped"
        return jsonify({"ok": True})
    return jsonify({"ok": False, "reason": "not running"})


# ──────────────────────────────────────────────

if __name__ == "__main__":
    init_db(DB_PATH)
    app.run(host="0.0.0.0", port=5000, debug=False)
