# CLAUDE.md - my-music

## Project overview

A personal vinyl collection app. Imports a Discogs collection into SQLite, fetches lyrics via syncedlyrics, generates AI thematic summaries via Ollama or Claude, and serves a Flask web UI. Runs in Docker / Portainer.

## Key files

| File | Purpose |
|---|---|
| `app.py` | Flask web UI + REST API. Spawns background jobs as subprocesses and tees their output to container stdout. |
| `db.py` | Shared SQLite helpers - schema init, connection factory. |
| `import_discogs.py` | Discogs → SQLite importer (incremental, safe to re-run). |
| `enrich_discogs.py` | Back-fills missing album fields from Discogs release pages. |
| `fetch_lyrics_synced.py` | Active lyrics fetcher. Uses syncedlyrics (lrclib, NetEase providers). Resumable - only touches rows where `lyrics_fetched_at IS NULL`. No API token required. |
| `fetch_lyrics_ovh.py` | **Archived** - lyrics.ovh fetcher. Do not read or modify unless explicitly asked. |
| `fetch_lyrics_genius.py` | **Archived** - Genius-based lyrics fetcher. Do not read or modify unless explicitly asked. |
| `summarise.py` | AI thematic summariser - Ollama (default) or Claude. Produces 3–5 sentence summary + JSON tag list per track. |
| `version.py` | Single source of truth for semver. Imported by all modules. **Must be bumped with every code change.** |
| `all-songs.py` | Original Discogs → Excel exporter (legacy, kept unchanged). |

## Database schema

```sql
albums (discogs_id, title, year, artists_sort, styles, format, notes, imported_at)

tracks (id, album_id, position, title, artists,
        lyrics, lyrics_fetched_at, lyrics_source,  -- "lyrics_ovh" | "not_found" | "error"
        summary, theme_tags,                        -- JSON array e.g. '["longing","travel"]'
        ai_processed_at)
```

## Development workflow

- **All changes go via feature branch + PR - never push directly to main.**
- **Bump `version.py` with every code change, no exceptions.**
- **Keep `README.md` current with every code change.**

```bash
git checkout -b feature/my-change
# make changes, bump version.py, update README.md
git push -u origin feature/my-change
gh pr create ...
```

## Running locally

```bash
pip install -r requirements.txt
python import_discogs.py --token YOUR_DISCOGS_TOKEN
python fetch_lyrics.py
python summarise.py
python app.py   # → http://localhost:5000
```

## Running in Docker / Portainer

```bash
cp .env.example .env   # fill in tokens
docker compose up -d   # → http://localhost:5000
```

Database persists at `./data/music.db` on the host.

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DISCOGS_TOKEN` | Sync | - | Discogs user token |
| `ANTHROPIC_API_KEY` | Claude mode | - | |
| `OLLAMA_HOST` | No | `http://host.docker.internal:11434` | |
| `OLLAMA_MODEL` | No | `llama3` | |
| `DB_PATH` | No | `music.db` / `/app/data/music.db` in Docker | |
| `TUNNEL_TOKEN` | Cloudflare tunnel | - | Optional external access via Cloudflare Zero Trust |

## Background jobs (app.py)

Jobs run as subprocesses. `_run_job` in `app.py` captures stdout+stderr and:
1. Buffers it in `_jobs[job_id]["output"]` for the UI poll endpoint.
2. Tees each line to `sys.stdout` (prefixed `[job_id]`) so it appears in Portainer container logs.

## Logging

`fetch_lyrics.py` uses `logging.basicConfig(level=DEBUG)` writing to stderr. Log lines include:
- Per-track lyrics.ovh query attempts (artist, title)
- HTTP error details for failed requests
- Batch commit summaries

## Cloudflare Tunnel (optional)

Gated behind a Compose profile - opt-in only:

```bash
docker compose --profile tunnel up -d
```
