"""
fetch_lyrics.py — Fetch lyrics from Genius for all unprocessed tracks.

Usage:
    python fetch_lyrics.py --genius-token YOUR_TOKEN
    python fetch_lyrics.py --genius-token YOUR_TOKEN --batch 50 --db music.db

Resumable: only processes tracks where lyrics_fetched_at IS NULL.
Commits after each batch so progress is never lost.
"""
import argparse
import os
import time
from datetime import datetime, timezone

from version import __version__

import lyricsgenius

from db import init_db, get_connection


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_lyrics(genius_token: str, db_path: str, batch_size: int) -> None:
    init_db(db_path)

    genius = lyricsgenius.Genius(
        genius_token,
        skip_non_songs=True,
        excluded_terms=["(Remix)", "(Live)"],
        verbose=False,
        remove_section_headers=True,
    )

    conn = get_connection(db_path)

    total_fetched = 0
    total_not_found = 0
    total_errors = 0

    while True:
        # Load next batch of unprocessed tracks
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
            break

        print(f"Processing batch of {len(rows)} tracks…")

        for row in rows:
            track_id = row["id"]
            title = row["title"]
            # Prefer track-level artist, fall back to album artist
            artist = (row["artists"] or row["artists_sort"] or "").strip()

            lyrics = None
            source = "not_found"

            try:
                song = genius.search_song(title, artist)
                if song and song.lyrics:
                    lyrics = song.lyrics
                    source = "genius"
                    total_fetched += 1
                    print(f"  ✓ {artist} — {title}")
                else:
                    total_not_found += 1
                    print(f"  ✗ Not found: {artist} — {title}")
            except Exception as ex:
                source = "error"
                total_errors += 1
                print(f"  ! Error for {artist} — {title}: {ex}")

            conn.execute(
                """
                UPDATE tracks
                SET lyrics = ?, lyrics_source = ?, lyrics_fetched_at = ?
                WHERE id = ?
                """,
                (lyrics, source, now_iso(), track_id),
            )

            # Polite delay to avoid hammering the Genius API
            time.sleep(0.5)

        conn.commit()
        print(
            f"  Batch committed. Running totals — "
            f"found: {total_fetched}, not found: {total_not_found}, errors: {total_errors}"
        )

    conn.close()
    print(
        f"\nDone. Found: {total_fetched}, not found: {total_not_found}, "
        f"errors: {total_errors}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch lyrics from Genius for all unprocessed tracks"
    )
    parser.add_argument(
        "--genius-token",
        default=os.environ.get("GENIUS_TOKEN"),
        required=not os.environ.get("GENIUS_TOKEN"),
        help="Genius API client access token (or set GENIUS_TOKEN env var)",
    )
    parser.add_argument("--db", default="music.db", help="SQLite database path")
    parser.add_argument(
        "--batch", type=int, default=50, help="Tracks per commit batch (default 50)"
    )
    args = parser.parse_args()

    fetch_lyrics(args.genius_token, args.db, args.batch)


if __name__ == "__main__":
    main()
