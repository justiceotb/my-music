# My Music Meaning — v0.9.1

A local, searchable database of vinyl records enriched with lyrics and AI-generated thematic summaries. Built with Python, SQLite, Flask, and Docker.

## Features

- Imports your Discogs vinyl collection into a SQLite database (incremental - safe to re-run)
- Fetches lyrics via syncedlyrics (lrclib, netease) for every track — no API token required; lrclib timeout increased to 30s
- Generates 3–5 sentence thematic summaries and tag lists (e.g. `["longing", "travel", "alcohol"]`) using a local Ollama LLM or Claude
- Responsive web UI: search by artist, album, title, lyrics, or theme tag; click any track for full lyrics and summary; one-click Summarise button in track detail modal
- Mobile-friendly sidebar: collapses behind a "Filters ▾" toggle on small screens so song results are immediately visible; sidebar toggle hidden on desktop
- Sidebar with tabbed Tags / Albums panels — switch between the tag cloud and album list without scrolling; tags panel shows total unique tag count and supports sort by count (desc/asc) or alphabetically
- Tag themes: AI groups ~1300 individual tags into ~20–30 broad themes (Mood, Instrumentation, Era, etc.); a dropdown above the tag cloud filters to tags in a selected theme
- Tag Review & Merge tool in Debug view: asks the local AI (Ollama or Claude) to identify near-duplicate tags (plurals, synonyms, spelling variants) and lets you merge them in one click
- Filter chips (Has lyrics, No lyrics, Tagged, Owned singles, Released as single) with a Reset filters button to clear all active selections
- Singles tracking: album tracks on owned singles display A-side/B-side badges; `fetch_singles.py` searches Discogs to find which album tracks were also commercially released as singles and records the b-sides
- Per-track "Fetch Lyrics" and "Summarise" buttons in the track detail modal
- Song list shows current page and total pages with First/Last/±10 jump buttons
- All background tasks (sync, lyrics, summarise) are triggerable from the UI; Discogs artist disambiguation suffixes (e.g. "Alice Cooper (2)") are stripped before lyric searches
- Debug & Maintenance page with database health stats and per-script run buttons

## Project Structure

```
my-music/
├── db.py                 # Shared DB helpers (schema, connection, transactions)
├── import_discogs.py     # Discogs → SQLite importer
├── fetch_lyrics_synced.py  # syncedlyrics fetcher - lrclib/netease, no token required
├── fetch_singles.py      # Discogs single-release finder — records b-sides for album tracks
├── summarise.py          # AI thematic summariser (Ollama or Claude)
├── group_tags.py         # AI tag grouper — assigns tags to broad themes
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

tag_themes (tag, theme)  -- maps each tag to a broad AI-generated theme category

track_singles (id, track_id, discogs_release_id, single_title, bsides, year, fetched_at)
              -- one row per single release found for an album track; bsides is a JSON array
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

This stack uses an external Docker network named `proxy`. If that network does not already exist, create it before starting the stack:

```bash
docker network create proxy
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
| `TUNNEL_TOKEN` | Cloudflare only | Cloudflare Tunnel token; only used if the optional `cloudflared` service is enabled in `docker-compose.yml` |

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

Generates a thematic summary, tag list, and casual one-liner for every track that has lyrics but no AI summary yet. Resumable. Commits each track immediately after write (not end-of-batch), and strips markdown fences from model responses that ignore the JSON-only instruction.

Each track gets three AI-generated fields:
- `summary` — 3–5 sentence thematic analysis
- `theme_tags` — list of short tag strings
- `summary_casual` — one sentence how an 18-year-old would describe the song to a friend

```bash
# Ollama (default)
python summarise.py [--ollama-model llama3] [--ollama-host http://localhost:11434]

# Claude Haiku
ANTHROPIC_API_KEY=sk-ant-... python summarise.py --model-type claude

# Options
python summarise.py --batch 20 --db music.db

# Backfill casual line for tracks that already have a summary
python summarise.py --backfill-casual
```

### `group_tags.py`

Groups all distinct theme tags into broad AI-generated categories (Mood, Instrumentation, Era, etc.) and stores the mapping in `tag_themes`. Run after `summarise.py` has processed your tracks. Safe to re-run — existing assignments are replaced.

```bash
# Ollama (default)
python group_tags.py

# Claude Haiku
ANTHROPIC_API_KEY=sk-ant-... python group_tags.py --model-type claude

# Options
python group_tags.py --db music.db --chunk 200  # chunk for smaller-context models
```

The theme dropdown in the sidebar Tags panel becomes active once this has run. Selecting a theme narrows the tag cloud to only that theme's tags; clicking a tag still filters tracks normally.

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
- **Tag cloud** - click any tag to filter; click again to clear. A **Theme** dropdown above the cloud filters visible tags to a selected theme (populated after running `group_tags.py`)
- **Filter chips** - one-click filters above the track list: *Has lyrics*, *No lyrics*, *Tagged*; toggleable, compose with search/album/tag filters
- **Albums sidebar** - click to filter by album
- **Track cards** - show title, artist, tags, summary excerpt, and status chips (lyrics found/missing, tags, summarised)
- **Track modal** - click any card to see full lyrics and summary
- **Actions menu** - Sync Discogs, three Fetch lyrics modes (new / previously failed / all), Summarise, Backfill casual lines; live output streams into a scrollable banner; a **Stop** button terminates mid-flight
- **Debug ⚙ button** (header, left of Actions) - opens the Debug & Maintenance page: live DB health stats, "Reset stuck summaries", **Download DB backup** button, and Run buttons for all background scripts
- **Album sort** - sort the albums sidebar independently (Artist / Album / Year)
- **Track sort** - sort the track listing independently (Artist / Album / Song)

## Cloudflare Tunnel (optional external access)

The `cloudflared` service is currently commented out in `docker-compose.yml`. To enable a Cloudflare Tunnel, uncomment that service block, set `TUNNEL_TOKEN` in your `.env` file, and redeploy:

```bash
# .env
TUNNEL_TOKEN=your_token_here

docker compose up -d
```

Because the tunnel is opt-in, external access is disabled by default. Apply a Zero Trust access policy in the Cloudflare dashboard (e.g. email OTP) to restrict who can reach the UI externally.

## Container Logs (Portainer)

All background job output (Discogs sync, lyrics fetch, summarise) is teed to the container's stdout so it appears in Portainer's log view in real time. Log lines are prefixed with the job ID, e.g. `[lyrics] 2026-01-01T00:00:00Z [DEBUG] …`.
