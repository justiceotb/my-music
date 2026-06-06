"""
fetch_lyrics.py — Fetch lyrics from lyrics.ovh for all unprocessed tracks.

Usage:
    python fetch_lyrics.py
    python fetch_lyrics.py --batch 50 --db music.db

    # Ad-hoc lookup (no DB required):
    python fetch_lyrics.py --artist "Pink Floyd" --title "Comfortably Numb"

Resumable: only processes tracks where lyrics_fetched_at IS NULL.
Commits after each batch so progress is never lost.
"""
import argparse
import logging
import time
from datetime import datetime, timezone

import requests

from version import __version__
from db import init_db, get_connection

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

_API_BASE = "https://api.lyrics.ovh/v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lookup(artist: str, title: str):
    """Call lyrics.ovh and return (lyrics_text, source) or (None, 'not_found'/'error')."""
    url = f"{_API_BASE}/{requests.utils.quote(artist)}/{requests.utils.quote(title)}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            lyrics = data.get("lyrics", "").strip()
            if lyrics:
                return lyrics, "lyrics_ovh"
            return None, "not_found"
        if resp.status_code == 404:
            return None, "not_found"
        log.warning("Unexpected HTTP %s for %r — %r", resp.status_code, url, resp.text[:200])
        return None, "error"
    except Exception as ex:
        log.warning("Request error for %r — %r: %s", artist, title, ex)
        return None, "error"


def fetch_one(artist: str, title: str) -> None:
    log.debug("Ad-hoc query — artist=%r title=%r", artist, title)
    lyrics, source = _lookup(artist, title)
    if source == "lyrics_ovh":
        print(f"  ✓ {artist} — {title}\n")
        print(lyrics)
    else:
        print(f"  ✗ Not found: {artist} — {title}")


def fetch_lyrics(db_path: str, batch_size: int) -> None:
    init_db(db_path)
    conn = get_connection(db_path)

    total_fetched = 0
    total_not_found = 0
    total_errors = 0

    while True:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.artists, a.artists_sort, a.title as album_title
            FROM tracks t
            JOIN albums a ON a.discogs_id = t.album_id
            WHERE t.lyrics_fetched_at IS NULL
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()

        if not rows:
            log.debug("No unprocessed tracks remaining — exiting loop")
            break

        log.info("Processing batch of %d tracks…", len(rows))

        for row in rows:
            track_id = row["id"]
            title = row["title"]
            artist = (row["artists"] or row["artists_sort"] or "").strip()

            lyrics, source = _lookup(artist, title)

            if source == "lyrics_ovh":
                total_fetched += 1
                log.info("  ✓ %s — %s (lyrics_len=%d)", artist, title, len(lyrics))
                print(f"  ✓ {artist} — {title}")
            elif source == "not_found":
                total_not_found += 1
                log.debug("  ✗ Not found: %s — %s", artist, title)
                print(f"  ✗ Not found: {artist} — {title}")
            else:
                total_errors += 1
                log.warning("  ! Error for %s — %s", artist, title)
                print(f"  ! Error for {artist} — {title}")

            conn.execute(
                """
                UPDATE tracks
                SET lyrics = ?, lyrics_source = ?, lyrics_fetched_at = ?
                WHERE id = ?
                """,
                (lyrics, source, now_iso(), track_id),
            )

            time.sleep(0.5)

        conn.commit()
        log.info(
            "Batch committed. Running totals — found: %d, not found: %d, errors: %d",
            total_fetched, total_not_found, total_errors,
        )
        print(
            f"  Batch committed. Running totals — "
            f"found: {total_fetched}, not found: {total_not_found}, errors: {total_errors}"
        )

    conn.close()
    log.info("Done. Found: %d, not found: %d, errors: %d", total_fetched, total_not_found, total_errors)
    print(f"\nDone. Found: {total_fetched}, not found: {total_not_found}, errors: {total_errors}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch lyrics from lyrics.ovh for all unprocessed tracks"
    )
    parser.add_argument("--db", default="music.db", help="SQLite database path")
    parser.add_argument(
        "--batch", type=int, default=50, help="Tracks per commit batch (default 50)"
    )
    parser.add_argument("--artist", default=None, help="Artist name for ad-hoc lookup (requires --title)")
    parser.add_argument("--title", default=None, help="Song title for ad-hoc lookup (requires --artist)")
    args = parser.parse_args()

    if bool(args.artist) != bool(args.title):
        parser.error("--artist and --title must be used together")

    if args.artist and args.title:
        fetch_one(args.artist, args.title)
    else:
        fetch_lyrics(args.db, args.batch)


if __name__ == "__main__":
    main()
