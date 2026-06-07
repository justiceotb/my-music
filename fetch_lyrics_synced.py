"""
fetch_lyrics_synced.py - Fetch lyrics via syncedlyrics for all unprocessed tracks.

syncedlyrics queries multiple providers (Musixmatch, NetEase, lrclib, etc.) and
requires no API token. Results may be LRC-formatted (timestamped); timestamps are
stripped before storing so the DB holds plain text, consistent with other fetchers.

Usage:
    python fetch_lyrics_synced.py
    python fetch_lyrics_synced.py --batch 50 --db music.db

    # Ad-hoc lookup (no DB required):
    python fetch_lyrics_synced.py --artist "Biffy Clyro" --title "Mountains"

Resumable: only processes tracks where lyrics_fetched_at IS NULL.
Commits after each batch so progress is never lost.
"""
import argparse
import logging
import re
import time
from datetime import datetime, timezone

import syncedlyrics
import syncedlyrics.providers.base as _sl_base
import requests

# syncedlyrics hardcodes a 10s read timeout which regularly times out on lrclib.
# Patch TimeoutSession to use (5s connect, 30s read) before any provider is instantiated.
def _patched_timeout_request(self, method, url, **kwargs):
    kwargs.setdefault("timeout", (5, 30))
    return requests.Session.request(self, method, url, **kwargs)

_sl_base.TimeoutSession.request = _patched_timeout_request

from db import init_db, get_connection
from version import __version__

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

_LRC_TIMESTAMP = re.compile(r"^\[\d+:\d+\.\d+\]\s*")
_ARTIST_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


def clean_artist(artist: str) -> str:
    """Strip Discogs disambiguation suffixes like 'Alice Cooper (2)'."""
    return _ARTIST_SUFFIX.sub("", artist).strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def strip_lrc(text: str) -> str:
    """Remove LRC timestamp tags and return plain lyrics."""
    lines = [_LRC_TIMESTAMP.sub("", line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


def search(artist: str, title: str, providers: list[str] | None = None) -> str | None:
    """Return plain-text lyrics or None."""
    query = f"{artist} {title}"
    log.debug("syncedlyrics query: %r providers: %s", query, providers)
    try:
        result = syncedlyrics.search(query, plain_only=True, providers=providers)
    except Exception as ex:
        log.warning("syncedlyrics raised for %r: %s", query, ex)
        return None
    if not result:
        return None
    return strip_lrc(result)


def fetch_one_track(db_path: str, track_id: int, providers: list[str] | None = None) -> None:
    """Fetch and save lyrics for a single track by DB id."""
    init_db(db_path)
    conn = get_connection(db_path)
    row = conn.execute(
        """SELECT t.id, t.title, t.artists, a.artists_sort
           FROM tracks t JOIN albums a ON a.discogs_id = t.album_id
           WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if not row:
        print(f"Track {track_id} not found")
        conn.close()
        return

    title = row["title"]
    artist = clean_artist(row["artists"] or row["artists_sort"] or "")
    log.debug("Single-track fetch: track_id=%d artist=%r title=%r", track_id, artist, title)

    lyrics = search(artist, title, providers)
    source = "syncedlyrics" if lyrics else "not_found"

    conn.execute(
        "UPDATE tracks SET lyrics = ?, lyrics_source = ?, lyrics_fetched_at = ? WHERE id = ?",
        (lyrics, source, now_iso(), track_id),
    )
    conn.commit()
    conn.close()

    if lyrics:
        print(f"  ✓ {artist} - {title}")
    else:
        print(f"  ✗ Not found: {artist} - {title}")


def fetch_one(artist: str, title: str, providers: list[str] | None = None) -> None:
    log.debug("Ad-hoc query - artist=%r title=%r", artist, title)
    lyrics = search(artist, title, providers)
    if lyrics:
        print(f"  ✓ {artist} - {title}\n")
        print(lyrics)
    else:
        print(f"  ✗ Not found: {artist} - {title}")


def fetch_lyrics(
    db_path: str,
    batch_size: int,
    providers: list[str] | None = None,
    retry_all: bool = False,
    retry_failed: bool = False,
) -> None:
    init_db(db_path)
    conn = get_connection(db_path)

    total_fetched = 0
    total_not_found = 0
    total_errors = 0

    if retry_all:
        where_clause = ""
    elif retry_failed:
        where_clause = "WHERE t.lyrics_source IN ('not_found', 'error')"
    else:
        where_clause = "WHERE t.lyrics_fetched_at IS NULL"
    total_tracks = conn.execute(
        f"SELECT COUNT(*) FROM tracks t JOIN albums a ON a.discogs_id = t.album_id {where_clause}"
    ).fetchone()[0]
    print(f"{total_tracks} tracks to process", flush=True)
    track_num = 0

    while True:
        rows = conn.execute(
            f"""
            SELECT t.id, t.title, t.artists, a.artists_sort
            FROM tracks t
            JOIN albums a ON a.discogs_id = t.album_id
            {where_clause}
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()

        if not rows:
            break

        for row in rows:
            track_num += 1
            track_id = row["id"]
            title = row["title"]
            artist = clean_artist(row["artists"] or row["artists_sort"] or "")

            lyrics = None
            source = "not_found"

            try:
                lyrics = search(artist, title, providers)
                if lyrics:
                    source = "syncedlyrics"
                    total_fetched += 1
                    print(f"  [{track_num}/{total_tracks}] ✓ {artist} - {title}", flush=True)
                else:
                    total_not_found += 1
                    print(f"  [{track_num}/{total_tracks}] ✗ Not found: {artist} - {title}", flush=True)
            except Exception as ex:
                source = "error"
                total_errors += 1
                log.warning("Error for %s - %s: %s", artist, title, ex, exc_info=True)
                print(f"  [{track_num}/{total_tracks}] ! Error for {artist} - {title}: {ex}", flush=True)

            conn.execute(
                """
                UPDATE tracks
                SET lyrics = ?, lyrics_source = ?, lyrics_fetched_at = ?
                WHERE id = ?
                """,
                (lyrics, source, now_iso(), track_id),
            )
            conn.commit()

            time.sleep(0.5)

        print(
            f"  Batch done. Running totals - "
            f"found: {total_fetched}, not found: {total_not_found}, errors: {total_errors}",
            flush=True,
        )

    conn.close()
    print(
        f"\nDone. Found: {total_fetched}, not found: {total_not_found}, "
        f"errors: {total_errors}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch lyrics via syncedlyrics for all unprocessed tracks"
    )
    parser.add_argument("--db", default="music.db", help="SQLite database path")
    parser.add_argument(
        "--batch", type=int, default=50, help="Tracks per commit batch (default 50)"
    )
    parser.add_argument("--retry-all", action="store_true",
                        help="Re-fetch lyrics for all tracks, including those with lyrics")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-fetch lyrics only for tracks previously marked not_found or error")
    parser.add_argument("--artist", default=None, help="Artist name for ad-hoc lookup (requires --title)")
    parser.add_argument("--title", default=None, help="Song title for ad-hoc lookup (requires --artist)")
    parser.add_argument(
        "-p", "--providers",
        nargs="+",
        type=str.lower,
        default=["lrclib", "netease"],
        choices=["musixmatch", "lrclib", "netease"],
        help="Providers to search (default: lrclib, netease)",
    )
    parser.add_argument("--track-id", type=int, default=None,
                        help="Fetch lyrics for a single track by DB id and exit")
    args = parser.parse_args()

    if args.track_id is not None:
        fetch_one_track(args.db, args.track_id, args.providers)
    elif bool(args.artist) != bool(args.title):
        parser.error("--artist and --title must be used together")
    elif args.artist and args.title:
        fetch_one(args.artist, args.title, args.providers)
    else:
        fetch_lyrics(args.db, args.batch, args.providers, retry_all=args.retry_all, retry_failed=args.retry_failed)


if __name__ == "__main__":
    main()
