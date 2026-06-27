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
from datetime import datetime, timezone
from threading import Thread

from flask import Flask, jsonify, render_template, request, send_file

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
            "SELECT COUNT(*) FROM tracks WHERE lyrics IS NOT NULL AND lyrics_source NOT IN ('not_found', 'error')"
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
        clauses.append("(t.lyrics IS NOT NULL AND t.lyrics_source NOT IN ('not_found', 'error'))")
    elif filter_mode == "no_lyrics":
        clauses.append("(t.lyrics_source IN ('not_found', 'error') OR t.lyrics_source IS NULL)")
    elif filter_mode == "has_tags":
        clauses.append("(t.theme_tags IS NOT NULL AND t.theme_tags != '[]')")
    elif filter_mode == "owned_singles":
        clauses.append("a.format LIKE '%Single%'")
    elif filter_mode == "released_as_single":
        clauses.append("ts.singles_count IS NOT NULL")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    singles_join = """
        LEFT JOIN (
            SELECT track_id,
                   COUNT(*) as singles_count,
                   GROUP_CONCAT(bsides, '|||') as singles_bsides
            FROM track_singles
            GROUP BY track_id
        ) ts ON ts.track_id = t.id
    """

    track_order = {
        "artist": "a.artists_sort COLLATE NOCASE, a.year, t.id",
        "album":  "a.title COLLATE NOCASE, t.id",
        "year":   "a.year, a.artists_sort COLLATE NOCASE, t.id",
        "song":   "t.title COLLATE NOCASE, a.artists_sort COLLATE NOCASE",
    }.get(sort, "a.artists_sort COLLATE NOCASE, a.year, t.id")

    total = conn.execute(
        f"""SELECT COUNT(*) FROM tracks t
            JOIN albums a ON a.discogs_id = t.album_id
            {singles_join}
            {where}""",
        params,
    ).fetchone()[0]

    rows = conn.execute(
        f"""
        SELECT t.id, t.title, t.position, t.artists,
               a.title as album, a.artists_sort, a.year,
               a.format as album_format,
               t.lyrics_source, t.summary, t.theme_tags,
               t.ai_processed_at,
               ts.singles_count, ts.singles_bsides,
               CASE WHEN EXISTS (SELECT 1 FROM list_tracks lt WHERE lt.track_id = t.id) THEN 1 ELSE 0 END as in_list
        FROM tracks t
        JOIN albums a ON a.discogs_id = t.album_id
        {singles_join}
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
        SELECT t.*, a.title as album, a.artists_sort, a.year, a.styles, a.format as album_format
        FROM tracks t JOIN albums a ON a.discogs_id = t.album_id
        WHERE t.id = ?
        """,
        (track_id,),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    result = dict(row)
    singles = conn.execute(
        "SELECT single_title, bsides, year FROM track_singles WHERE track_id = ? ORDER BY year",
        (track_id,),
    ).fetchall()
    result["singles"] = [dict(s) for s in singles]
    conn.close()
    return jsonify(result)


@app.route("/api/tags")
def api_tags():
    """Return all distinct theme tags sorted by frequency. Optional ?theme= filter."""
    theme = (request.args.get("theme") or "").strip()

    conn = get_connection(DB_PATH)
    rows = conn.execute(
        "SELECT theme_tags FROM tracks WHERE theme_tags IS NOT NULL AND theme_tags != ''"
    ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        try:
            for tag in json.loads(row["theme_tags"]):
                counts[tag] = counts.get(tag, 0) + 1
        except Exception:
            pass

    if theme:
        theme_tags_set = {
            r["tag"] for r in conn.execute(
                "SELECT tag FROM tag_themes WHERE theme = ?", (theme,)
            ).fetchall()
        }
        sorted_tags = sorted(
            ((t, c) for t, c in counts.items() if t in theme_tags_set),
            key=lambda x: -x[1],
        )
    else:
        sorted_tags = sorted(counts.items(), key=lambda x: -x[1])

    conn.close()
    return jsonify([{"tag": t, "count": c} for t, c in sorted_tags])


@app.route("/api/themes")
def api_themes():
    """Return all tag themes sorted by number of tags."""
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        "SELECT theme, COUNT(*) as tag_count FROM tag_themes GROUP BY theme ORDER BY theme COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


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


@app.route("/api/fetch-singles", methods=["POST"])
def api_fetch_singles():
    data = request.get_json(silent=True) or {}
    if data.get("reset"):
        _start_job("fetch_singles", [sys.executable, "fetch_singles.py",
                                      "--reset", "--db", DB_PATH])
        return jsonify({"job_id": "fetch_singles"})
    token = os.environ.get("DISCOGS_TOKEN")
    if not token:
        return jsonify({"error": "DISCOGS_TOKEN not set"}), 400
    batch = str(data.get("batch", 20))
    _start_job("fetch_singles", [sys.executable, "fetch_singles.py",
                                  "--token", token, "--db", DB_PATH, "--batch", batch])
    return jsonify({"job_id": "fetch_singles"})


@app.route("/api/fetch-lyrics/<int:track_id>", methods=["POST"])
def api_fetch_lyrics_track(track_id: int):
    job_id = f"fetch_lyrics_{track_id}"
    _start_job(job_id, [sys.executable, "fetch_lyrics_synced.py", "--db", DB_PATH, "--track-id", str(track_id)])
    return jsonify({"job_id": job_id})


@app.route("/api/fetch-lyrics", methods=["POST"])
def api_fetch_lyrics():
    job_id = "fetch_lyrics"
    data = request.json or {}
    batch = str(data.get("batch", 50))
    retry_all = data.get("retry_all", False)
    retry_failed = data.get("retry_failed", False)
    cmd = [sys.executable, "fetch_lyrics_synced.py", "--db", DB_PATH, "--batch", batch]
    if retry_all:
        cmd.append("--retry-all")
    elif retry_failed:
        cmd.append("--retry-failed")
    _start_job(job_id, cmd)
    return jsonify({"job_id": job_id})


@app.route("/api/summarise/<int:track_id>", methods=["POST"])
def api_summarise_track(track_id: int):
    data = request.json or {}
    model_type = data.get("model_type", "ollama")
    if model_type == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400
    ollama_model = data.get("ollama_model", os.environ.get("OLLAMA_MODEL", "llama3"))
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    job_id = f"summarise_{track_id}"
    cmd = [
        sys.executable, "summarise.py",
        "--model-type", model_type,
        "--ollama-model", ollama_model,
        "--ollama-host", ollama_host,
        "--db", DB_PATH,
        "--track-id", str(track_id),
    ]
    _start_job(job_id, cmd)
    return jsonify({"job_id": job_id})


@app.route("/api/summarise", methods=["POST"])
def api_summarise():
    data = request.json or {}
    model_type = data.get("model_type", "ollama")
    ollama_model = data.get("ollama_model", os.environ.get("OLLAMA_MODEL", "llama3"))
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    batch = str(data.get("batch", 20))
    mode = data.get("mode", "")

    if model_type == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400

    cmd = [
        sys.executable, "summarise.py",
        "--model-type", model_type,
        "--ollama-model", ollama_model,
        "--ollama-host", ollama_host,
        "--db", DB_PATH,
        "--batch", batch,
    ]
    if mode == "backfill_casual":
        cmd.append("--backfill-casual")
        job_id = "summarise_backfill_casual"
    else:
        job_id = "summarise"

    _start_job(job_id, cmd)
    return jsonify({"job_id": job_id})


@app.route("/api/group-tags", methods=["POST"])
def api_group_tags():
    data = request.json or {}
    model_type = data.get("model_type", "ollama")
    ollama_model = data.get("ollama_model", os.environ.get("OLLAMA_MODEL", "llama3"))
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    chunk = str(data.get("chunk", 200))
    if model_type == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400
    cmd = [
        sys.executable, "group_tags.py",
        "--model-type", model_type,
        "--ollama-model", ollama_model,
        "--ollama-host", ollama_host,
        "--db", DB_PATH,
        "--chunk", chunk,
    ]
    job_id = "group_tags"
    _start_job(job_id, cmd)
    return jsonify({"job_id": job_id})


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
# API - lists
# ──────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.route("/api/lists")
def api_lists():
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        """SELECT l.id, l.name, l.created_at,
                  COUNT(lt.track_id) as track_count
           FROM lists l
           LEFT JOIN list_tracks lt ON lt.list_id = l.id
           GROUP BY l.id
           ORDER BY l.name COLLATE NOCASE"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/lists", methods=["POST"])
def api_lists_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    conn = get_connection(DB_PATH)
    cur = conn.execute(
        "INSERT INTO lists (name, created_at) VALUES (?, ?)",
        (name, _now_utc()),
    )
    conn.commit()
    list_id = cur.lastrowid
    row = conn.execute("SELECT id, name, created_at FROM lists WHERE id = ?", (list_id,)).fetchone()
    conn.close()
    return jsonify({**dict(row), "track_count": 0}), 201


@app.route("/api/lists/<int:list_id>", methods=["PATCH"])
def api_lists_rename(list_id: int):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    conn = get_connection(DB_PATH)
    conn.execute("UPDATE lists SET name = ? WHERE id = ?", (name, list_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lists/<int:list_id>", methods=["DELETE"])
def api_lists_delete(list_id: int):
    conn = get_connection(DB_PATH)
    conn.execute("DELETE FROM list_tracks WHERE list_id = ?", (list_id,))
    conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/lists/<int:list_id>/tracks")
def api_list_tracks(list_id: int):
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        """SELECT t.id as track_id, t.title, t.artists, t.position as track_position,
                  a.title as album_title, a.discogs_id as album_discogs_id, a.year,
                  a.artists_sort,
                  lt.added_at, lt.position as list_position
           FROM list_tracks lt
           JOIN tracks t ON t.id = lt.track_id
           JOIN albums a ON a.discogs_id = t.album_id
           WHERE lt.list_id = ?
           ORDER BY lt.position, lt.added_at""",
        (list_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/lists/<int:list_id>/tracks/<int:track_id>", methods=["POST"])
def api_list_tracks_add(list_id: int, track_id: int):
    conn = get_connection(DB_PATH)
    # Get next position
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM list_tracks WHERE list_id = ?",
        (list_id,),
    ).fetchone()
    next_pos = row[0]
    try:
        conn.execute(
            "INSERT INTO list_tracks (list_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
            (list_id, track_id, next_pos, _now_utc()),
        )
        conn.commit()
    except Exception:
        conn.close()
        return jsonify({"error": "Already in list"}), 409
    conn.close()
    return jsonify({"ok": True}), 201


@app.route("/api/lists/<int:list_id>/tracks/<int:track_id>", methods=["DELETE"])
def api_list_tracks_remove(list_id: int, track_id: int):
    conn = get_connection(DB_PATH)
    conn.execute(
        "DELETE FROM list_tracks WHERE list_id = ? AND track_id = ?",
        (list_id, track_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/track/<int:track_id>/lists")
def api_track_lists(track_id: int):
    conn = get_connection(DB_PATH)
    rows = conn.execute(
        "SELECT list_id FROM list_tracks WHERE track_id = ?",
        (track_id,),
    ).fetchall()
    conn.close()
    return jsonify([r["list_id"] for r in rows])


# ──────────────────────────────────────────────
# API - debug / maintenance
# ──────────────────────────────────────────────

@app.route("/api/debug/health")
def api_debug_health():
    conn = get_connection(DB_PATH)
    data = {
        "pending_lyrics": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE lyrics_source IS NULL"
        ).fetchone()[0],
        "pending_summary": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE lyrics IS NOT NULL AND ai_processed_at IS NULL"
        ).fetchone()[0],
        "stuck_summary": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE ai_processed_at IS NOT NULL AND (summary IS NULL OR summary = '')"
        ).fetchone()[0],
        "no_lyrics": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE lyrics_source IN ('not_found', 'error')"
        ).fetchone()[0],
        "total_tracks": conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
        "total_albums": conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
        "with_lyrics": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE lyrics IS NOT NULL AND lyrics_source NOT IN ('not_found', 'error')"
        ).fetchone()[0],
        "with_summary": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE ai_processed_at IS NOT NULL AND summary IS NOT NULL AND summary != ''"
        ).fetchone()[0],
        "with_tags": conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE theme_tags IS NOT NULL AND theme_tags != '[]' AND theme_tags != ''"
        ).fetchone()[0],
    }
    conn.close()
    return jsonify(data)


@app.route("/api/tags/suggest-merges", methods=["POST"])
def api_suggest_tag_merges():
    """Ask the local AI to find near-duplicate tags and suggest merges."""
    data = request.json or {}
    model_type = data.get("model_type", "ollama")

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

    if len(counts) < 2:
        return jsonify({"suggestions": []})

    tag_list = "\n".join(
        f"  {tag} ({count})"
        for tag, count in sorted(counts.items(), key=lambda x: -x[1])
    )

    prompt = (
        "You are a tag deduplication assistant for a music collection app.\n"
        "Here is a list of theme tags with their usage counts:\n\n"
        f"{tag_list}\n\n"
        "Identify tags that are near-duplicates: plurals of the same word, synonyms, or minor spelling variants.\n"
        'Return a JSON array of merge suggestions, each with:\n'
        '  {"keep": "the canonical form to keep", "remove": "the duplicate to eliminate", "reason": "brief explanation"}\n'
        "Only suggest high-confidence merges. Do not suggest merges for conceptually distinct tags.\n"
        "Respond ONLY with a valid JSON array. No markdown, no extra text."
    )

    try:
        if model_type == "claude":
            import anthropic
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 400
            client = anthropic.Anthropic(api_key=key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        else:
            from openai import OpenAI as _OpenAI
            ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ollama_model = os.environ.get("OLLAMA_MODEL", "llama3")
            client = _OpenAI(base_url=f"{ollama_host}/v1", api_key="ollama")
            response = client.chat.completions.create(
                model=ollama_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]

        suggestions = json.loads(raw.strip())
        return jsonify({"suggestions": suggestions})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/tags/merge", methods=["POST"])
def api_merge_tags():
    """Replace all occurrences of one tag with another across all tracks."""
    data = request.json or {}
    keep = (data.get("keep") or "").strip()
    remove = (data.get("remove") or "").strip()
    if not keep or not remove:
        return jsonify({"error": "keep and remove are required"}), 400
    if keep == remove:
        return jsonify({"error": "keep and remove must differ"}), 400

    conn = get_connection(DB_PATH)
    rows = conn.execute(
        "SELECT id, theme_tags FROM tracks WHERE theme_tags LIKE ?",
        (f'%"{remove}"%',),
    ).fetchall()

    merged_count = 0
    for row in rows:
        try:
            tags = json.loads(row["theme_tags"])
        except Exception:
            continue
        if remove not in tags:
            continue
        new_tags = [keep if t == remove else t for t in tags]
        # deduplicate while preserving order
        seen: set[str] = set()
        deduped = []
        for t in new_tags:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        conn.execute(
            "UPDATE tracks SET theme_tags = ? WHERE id = ?",
            (json.dumps(deduped), row["id"]),
        )
        merged_count += 1

    conn.commit()
    conn.close()
    return jsonify({"merged_count": merged_count, "keep": keep, "remove": remove})


@app.route("/api/debug/reset-stuck", methods=["POST"])
def api_debug_reset_stuck():
    conn = get_connection(DB_PATH)
    result = conn.execute(
        "UPDATE tracks SET ai_processed_at = NULL WHERE ai_processed_at IS NOT NULL AND (summary IS NULL OR summary = '')"
    )
    count = result.rowcount
    conn.commit()
    conn.close()
    return jsonify({"reset": count})


@app.route("/api/db/download")
def api_db_download():
    """Download the SQLite database file as a backup."""
    import datetime
    db_path = os.path.abspath(DB_PATH)
    if not os.path.exists(db_path):
        return jsonify({"error": "Database file not found"}), 404
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    download_name = f"music_backup_{timestamp}.db"
    return send_file(db_path, as_attachment=True, download_name=download_name)


# ──────────────────────────────────────────────

if __name__ == "__main__":
    init_db(DB_PATH)
    app.run(host="0.0.0.0", port=5000, debug=False)
