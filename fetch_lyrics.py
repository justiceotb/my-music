"""
fetch_lyrics.py — Fetch lyrics from Genius for all unprocessed tracks.

Usage:
    python fetch_lyrics.py --genius-token YOUR_TOKEN
    python fetch_lyrics.py --genius-token YOUR_TOKEN --batch 50 --db music.db

Resumable: only processes tracks where lyrics_fetched_at IS NULL.
Commits after each batch so progress is never lost.
"""
import argparse
import logging
import os
import time
from datetime import datetime, timezone

from version import __version__

import lyricsgenius

from db import init_db, get_connection

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_lyrics(genius_token: str, db_path: str, batch_size: int) -> None:
    init_db(db_path)

    token_preview = genius_token[:4] + "…" + genius_token[-4:] if len(genius_token) >= 8 else "***"
    log.debug("Genius token: %s (len=%d)", token_preview, len(genius_token))
    log.debug("Initialising lyricsgenius client (timeout=10s, skip_non_songs=True)")

    genius = lyricsgenius.Genius(
        genius_token,
        verbose=False,
        timeout=10,
        skip_non_songs=True,
        excluded_terms=["(Remix)", "(Live)"],
        remove_section_headers=True,
    )

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

            lyrics = None
            source = "not_found"

            log.debug("Querying Genius: track_id=%s artist=%r title=%r", track_id, artist, title)
            try:
                song = genius.search_song(title, artist)
                if song and song.lyrics:
                    lyrics = song.lyrics
                    source = "genius"
                    total_fetched += 1
                    log.info("  ✓ %s — %s (lyrics_len=%d)", artist, title, len(lyrics))
                    print(f"  ✓ {artist} — {title}")
                else:
                    total_not_found += 1
                    log.debug("  ✗ Not found: %s — %s (song=%r)", artist, title, song)
                    print(f"  ✗ Not found: {artist} — {title}")
            except Exception as ex:
                if "403" in str(ex):
                    log.error(
                        "403 Forbidden from Genius — rate-limited or bad token. "
                        "token_preview=%s track=%r %r | error: %s",
                        token_preview, artist, title, ex,
                    )
                    print(f"  ! 403 Forbidden — rate-limited or bad token. Stopping run (track left unprocessed): {artist} — {title}")
                    conn.commit()
                    conn.close()
                    log.info("Aborted. Found: %d, not found: %d, errors: %d", total_fetched, total_not_found, total_errors)
                    print(f"\nAborted. Found: {total_fetched}, not found: {total_not_found}, errors: {total_errors}")
                    return
                source = "error"
                total_errors += 1
                log.warning("  ! Error for %s — %s: %s", artist, title, ex, exc_info=True)
                print(f"  ! Error for {artist} — {title}: {ex}")

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
