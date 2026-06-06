# My Vinyl Collection

A local, searchable database of vinyl records enriched with lyrics and AI-generated thematic summaries. Built with Python, SQLite, Flask, and Docker.

## Features

- Imports your Discogs vinyl collection into a SQLite database (incremental — safe to re-run)
- Fetches lyrics from Genius for every track
- Generates 3–5 sentence thematic summaries and tag lists (e.g. `["longing", "travel", "alcohol"]`) using a local Ollama LLM or Claude
- Responsive web UI: search by title, lyrics content, or theme tag; browse by album; click any track for full lyrics and summary
- All background tasks (sync, lyrics, summarise) are triggerable from the UI

## Project Structure

```
my-music/
├── all-songs.py          # Original Discogs → Excel exporter (unchanged)
├── db.py                 # Shared DB helpers (schema, connection, transactions)
├── import_discogs.py     # Discogs → SQLite importer
├── fetch_lyrics.py       # LyricsGenius lyrics fetcher
├── summarise.py          # AI thematic summariser (Ollama or Claude)
├── app.py                # Flask web UI + REST API
├── templates/index.html  # Responsive single-page UI (Pico CSS)
├── static/app.js         # UI logic
├── static/app.css        # Styles
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Database Schema

```sql
albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at)

tracks (id, album_id, position, title, artists,
        lyrics, lyrics_fetched_at, lyrics_source,   -- "genius" | "not_found" | "error"
        summary, theme_tags,                         -- JSON array e.g. '["longing","travel"]'
        ai_processed_at)
```

## Quickstart (local)

```bash
pip install -r requirements.txt

# 1. Import your Discogs collection
python import_discogs.py --token YOUR_DISCOGS_TOKEN

# 2. Fetch lyrics (get a token at genius.com/api-clients → API Clients)
python fetch_lyrics.py --genius-token YOUR_GENIUS_TOKEN

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

Point Portainer at `docker-compose.yml` and stack it from there.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCOGS_TOKEN` | Yes (for sync) | Discogs user token — [get one here](https://www.discogs.com/settings/developers) |
| `GENIUS_TOKEN` | Yes (for lyrics) | Genius client access token — [get one here](https://genius.com/api-clients) |
| `ANTHROPIC_API_KEY` | Claude mode only | Anthropic API key |
| `OLLAMA_HOST` | No | Ollama URL (default `http://host.docker.internal:11434`) |
| `OLLAMA_MODEL` | No | Ollama model name (default `llama3`) |
| `DB_PATH` | No | SQLite path inside container (default `music.db`) |
| `TUNNEL_TOKEN` | Cloudflare only | Cloudflare Tunnel token |

## Script Reference

### `import_discogs.py`

Pulls your entire Discogs collection into `music.db`. Skips albums already present — safe to re-run after adding new records.

```bash
python import_discogs.py --token TOKEN [--db music.db]
# or: DISCOGS_TOKEN=... python import_discogs.py
```

### `fetch_lyrics.py`

Fetches lyrics from Genius for all tracks where `lyrics_fetched_at IS NULL`. Commits after every batch — fully resumable if interrupted.

```bash
python fetch_lyrics.py --genius-token TOKEN [--batch 50] [--db music.db]
# or: GENIUS_TOKEN=... python fetch_lyrics.py
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

### `all-songs.py` (original)

Exports your Discogs collection to an Excel spreadsheet.

```bash
python all-songs.py --token TOKEN [--file tracks.xlsx]
```

## Web UI

Open `http://localhost:5000` after starting the app.

- **Search bar** — searches track title, lyrics, summary, and theme tags simultaneously
- **Tag cloud** — click any tag to filter; click again to clear
- **Albums sidebar** — click to filter by album
- **Track cards** — show title, artist, tags, summary excerpt, and badges for lyrics/AI status
- **Track modal** — click any card to see full lyrics and summary
- **Actions menu** — Sync Discogs, Fetch missing lyrics, Summarise unprocessed (Ollama or Claude); progress is shown inline

## Cloudflare Tunnel (optional external access)

The `cloudflared` sidecar in `docker-compose.yml` is gated behind a Docker Compose profile so it's opt-in:

```bash
# Set TUNNEL_TOKEN in .env (from Cloudflare Zero Trust dashboard → Tunnels)
docker compose --profile tunnel up -d
```

Apply a Zero Trust access policy in the Cloudflare dashboard (e.g. email OTP) to restrict who can reach the UI externally.
