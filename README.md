# My Music Meaning

A local, searchable database of vinyl records enriched with lyrics and AI-generated thematic summaries. Built with Python, SQLite, Flask, and Docker.

## Features

- Imports your Discogs vinyl collection into a SQLite database (incremental - safe to re-run)
- Fetches lyrics from [lyrics.ovh](https://github.com/NTag/lyrics.ovh) for every track (no API token required)
- Generates 3–5 sentence thematic summaries and tag lists (e.g. `["longing", "travel", "alcohol"]`) using a local Ollama LLM or Claude
- Responsive web UI: search by artist, album, title, lyrics, or theme tag; browse by album; click any track for full lyrics and summary
- All background tasks (sync, lyrics, summarise) are triggerable from the UI

## Project Structure

```
my-music/
├── all-songs.py          # Original Discogs → Excel exporter (unchanged)
├── db.py                 # Shared DB helpers (schema, connection, transactions)
├── import_discogs.py     # Discogs → SQLite importer
├── fetch_lyrics_ovh.py   # lyrics.ovh lyrics fetcher
├── fetch_lyrics_genius.py  # Genius lyrics fetcher (search_songs + lyrics() API)
├── fetch_lyrics_synced.py  # syncedlyrics fetcher - lrclib/netease/megalobiz/genius, no token required
├── summarise.py          # AI thematic summariser (Ollama or Claude)
├── app.py                # Flask web UI + REST API
├── templates/index.html  # Responsive single-page UI (Pico CSS)
├── static/app.js         # UI logic
├── static/app.css        # Styles
├── version.py            # Single source of truth for semver (imported by all modules)
├── tests/                # Local pytest suite (no Docker, no real tokens)
│   ├── conftest.py       # Shared fixtures (tmp_db, seeded_db)
│   ├── test_db.py
│   ├── test_fetch_lyrics.py
│   ├── test_summarise.py
│   ├── test_import_discogs.py
│   ├── test_enrich_discogs.py
│   └── test_app.py
├── requirements-dev.txt  # pytest + pytest-mock (dev only)
├── .vscode/launch.json   # VS Code run/debug configs with env var instructions
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Database Schema

```sql
albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at)

tracks (id, album_id, position, title, artists,
        lyrics, lyrics_fetched_at, lyrics_source,   -- "syncedlyrics"|"lyrics_ovh"|"genius" (found) | "not_found" | "error" | NULL (unprocessed)
        summary, theme_tags,                         -- JSON array e.g. '["longing","travel"]'
        ai_processed_at)
```

## Running tests locally

The test suite runs entirely without Docker, live APIs, or real tokens. All external services (Discogs, lyrics.ovh, Ollama, Claude) are mocked.

### Setup

**Option A - uv (recommended if you already have uv):**
```bash
uv venv
uv pip install -r requirements.txt -r requirements-dev.txt
.venv/Scripts/pytest tests/ -v      # Windows
# or
.venv/bin/pytest tests/ -v          # macOS/Linux
```

**Option B - standard pip:**
```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

### VS Code

Open `.vscode/launch.json` - it has three pre-configured launch targets:
- **pytest (all tests)** - runs the full suite; reads tokens from your OS environment or a `.env` file (tests don't need them, but the Flask app does)
- **pytest (current file)** - runs the currently open test file
- **Flask (local)** - starts the app locally; fill in your tokens in the `env` block

**To set tokens without touching shell profiles:**
1. Create a `.env` file in the project root (already in `.gitignore`):
   ```
   DISCOGS_TOKEN=your_token_here
   ANTHROPIC_API_KEY=sk-ant-...
   ```
2. Install the [DotENV](https://marketplace.visualstudio.com/items?itemName=mikestead.dotenv) extension - VS Code then reads these values automatically for the `${env:VAR}` references in `launch.json`.

---

## Quickstart (local)

```bash
pip install -r requirements.txt

# 1. Import your Discogs collection
python import_discogs.py --token YOUR_DISCOGS_TOKEN

# 2. Fetch lyrics (no token required)
python fetch_lyrics_synced.py

# 3. Summarise tracks that have lyrics (needs Ollama running locally)
python summarise.py

# 4. Open the UI
python app.py   # → http://localhost:5000
```

## Quickstart (Docker / Portainer)

```bash
cp .env.example .env   # fill in your tokens
docker compose up -d   # → http://localhost:5000
```

The database is stored in `./data/music.db` on the host - created automatically on first run and persisted across container rebuilds.

> **Migrating from a previous install?** If you have an existing `./music.db` file, move it before restarting:
> ```bash
> mkdir -p data && mv music.db data/music.db
> ```

Point Portainer at `docker-compose.yml` and stack it from there.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCOGS_TOKEN` | Yes (for sync) | Discogs user token - [get one here](https://www.discogs.com/settings/developers) |
| `ANTHROPIC_API_KEY` | Claude mode only | Anthropic API key |
| `OLLAMA_HOST` | No | Ollama URL (default `http://host.docker.internal:11434`) |
| `OLLAMA_MODEL` | No | Ollama model name (default `llama3`) |
| `DB_PATH` | No | SQLite path inside container (default `music.db`; Docker uses `/app/data/music.db`) |
| `TUNNEL_TOKEN` | Cloudflare only | Cloudflare Tunnel token |

## Script Reference

### `import_discogs.py`

Pulls your entire Discogs collection into `music.db`. Skips albums already present - safe to re-run after adding new records.

```bash
python import_discogs.py --token TOKEN [--db music.db]
# or: DISCOGS_TOKEN=... python import_discogs.py
```

### `fetch_lyrics_ovh.py`

Fetches lyrics from [lyrics.ovh](https://github.com/NTag/lyrics.ovh) for all tracks where `lyrics_fetched_at IS NULL`. No API token required. Commits after every batch - fully resumable if interrupted.

```bash
python fetch_lyrics_ovh.py [--batch 50] [--db music.db]

# Ad-hoc lookup - no DB required, prints lyrics to stdout:
python fetch_lyrics_ovh.py --artist "Pink Floyd" --title "Comfortably Numb"
```

### `summarise.py`

Generates a thematic summary and tag list for every track that has lyrics but no AI summary yet. Resumable.

```bash
# Ollama (default)
python summarise.py [--ollama-model llama3] [--ollama-host http://localhost:11434]

# Claude Haiku
ANTHROPIC_API_KEY=sk-ant-... python summarise.py --model-type claude

# Options
python summarise.py --batch 20 --db music.db
```

### `enrich_discogs.py`

Back-fills missing album fields (`artists_sort`, `year`, `styles`, `format`) for albums already in the DB. Queries each album's Discogs release page and writes any newly found data. Falls back to building the artist name from the `artists` list when `artists_sort` is absent - which covers a large number of releases where Discogs only populates individual artist objects, not the sort field.

```bash
python enrich_discogs.py --token TOKEN [--db music.db]
# or: DISCOGS_TOKEN=... python enrich_discogs.py
```

### `all-songs.py` (original)

Exports your Discogs collection to an Excel spreadsheet.

```bash
python all-songs.py --token TOKEN [--file tracks.xlsx]
```

## Web UI

Open `http://localhost:5000` after starting the app.

- **Search bar** - searches artist, album title, track title, lyrics, and summary simultaneously
- **Tag cloud** - click any tag to filter; click again to clear
- **Filter chips** - one-click filters above the track list: *Has lyrics*, *No lyrics*, *Tagged*; toggleable, compose with search/album/tag filters
- **Albums sidebar** - click to filter by album
- **Track cards** - show title, artist, tags, summary excerpt, and status chips (lyrics found/missing, tags, summarised)
- **Track modal** - click any card to see full lyrics and summary
- **Actions menu** - Sync Discogs, Fetch missing lyrics, Summarise - Ollama (local), Summarise - With Claude; live output streams into a scrollable banner so you can see exactly what's happening; a **Stop** button terminates the running job mid-flight, and a **Dismiss** button clears the banner when done
- **Album sort** - sort the albums sidebar independently (Artist / Album / Year)
- **Track sort** - sort the track listing independently (Artist / Album / Song)

## Cloudflare Tunnel (optional external access)

The `cloudflared` sidecar in `docker-compose.yml` is gated behind a Docker Compose profile so it's opt-in:

```bash
# Set TUNNEL_TOKEN in .env (from Cloudflare Zero Trust dashboard → Tunnels)
docker compose --profile tunnel up -d
```

Apply a Zero Trust access policy in the Cloudflare dashboard (e.g. email OTP) to restrict who can reach the UI externally.

## Container Logs (Portainer)

All background job output (Discogs sync, lyrics fetch, summarise) is teed to the container's stdout so it appears in Portainer's log view in real time. Log lines are prefixed with the job ID, e.g. `[lyrics] 2026-01-01T00:00:00Z [DEBUG] …`.

`fetch_lyrics_ovh.py` uses Python's `logging` module at DEBUG level, so you'll see:
- Per-track lyrics.ovh query attempts with artist/title
- HTTP error context for failed requests
- Batch commit summaries
