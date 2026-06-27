"""
fetch_singles.py - Search Discogs to find which album tracks were also released
                   as commercial singles, and record the b-side titles.

Only processes tracks on non-single albums (albums whose format does not contain
"Single" — those we already own as singles and are handled by the UI directly).

Resumable: skips tracks where singles_checked_at IS NOT NULL.

Multiple single releases for the same track are supported; each gets its own row
in track_singles.

Usage:
    python fetch_singles.py --token YOUR_DISCOGS_TOKEN
    python fetch_singles.py --token YOUR_DISCOGS_TOKEN --db music.db --batch 20
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from itertools import islice

import discogs_client

from db import init_db, get_connection, transaction
from version import __version__

_ARTIST_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


def clean_artist(artist: str) -> str:
    """Strip Discogs disambiguation suffixes like 'The Cure (2)'."""
    return _ARTIST_SUFFIX.sub("", artist).strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def title_in_release(release_title: str, track_title: str) -> bool:
    """True if track_title matches any segment of a release title.

    Handles common Discogs single title formats:
      - "Heroes / V-2 Schneider"  (A-side / B-side slash notation)
      - "David Bowie - Heroes"    (Artist - Title prefix)
    """
    needle = track_title.strip().lower()
    candidates = re.split(r"\s*/\s*|\s+[-–]\s+", release_title)
    return any(c.strip().lower() == needle for c in candidates) \
        or release_title.strip().lower() == needle


def reset_singles(db_path: str) -> None:
    """Clear singles_checked_at for tracks that have no singles recorded.

    Tracks that were marked checked but found nothing are almost certainly
    victims of the v0.9.0 slice bug — reset them so they get re-processed.
    """
    init_db(db_path)
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE tracks SET singles_checked_at = NULL
            WHERE id NOT IN (SELECT DISTINCT track_id FROM track_singles)
            """
        )
        cleared = conn.execute(
            "SELECT changes()"
        ).fetchone()[0]
    print(f"Cleared {cleared} tracks for re-checking.", flush=True)


def fetch_singles(token: str, db_path: str, batch_size: int) -> None:
    init_db(db_path)

    d = discogs_client.Client(f"MusicEnrich/{__version__}", user_token=token)
    try:
        me = d.identity()
    except Exception as ex:
        raise SystemExit(f"Discogs auth failed: {ex}")

    print(f"Authenticated as {me.name}. Searching for singles…", flush=True)

    conn = get_connection(db_path)
    total = conn.execute(
        """
        SELECT COUNT(*) FROM tracks t
        JOIN albums a ON a.discogs_id = t.album_id
        WHERE a.format NOT LIKE '%Single%'
          AND t.singles_checked_at IS NULL
        """
    ).fetchone()[0]
    conn.close()

    print(f"{total} tracks to check", flush=True)

    track_num = 0
    total_found = 0
    total_none = 0

    while True:
        conn = get_connection(db_path)
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.artists, a.artists_sort
            FROM tracks t
            JOIN albums a ON a.discogs_id = t.album_id
            WHERE a.format NOT LIKE '%Single%'
              AND t.singles_checked_at IS NULL
            ORDER BY t.id
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        conn.close()

        if not rows:
            break

        for row in rows:
            track_num += 1
            track_id = row["id"]
            title = row["title"]
            artist = clean_artist(row["artists"] or row["artists_sort"] or "")

            singles_found = 0
            try:
                results = d.search(title, type="release", format="Single", artist=artist)
                time.sleep(1.0)

                for result in islice(results, 5):
                    if not title_in_release(result.title, title):
                        continue
                    try:
                        release = d.release(result.id)
                        time.sleep(1.0)
                        tracklist = release.tracklist
                        bsides = [
                            tr.title for tr in tracklist
                            if tr.position and tr.position.upper().startswith("B")
                        ]
                        with transaction(db_path) as wconn:
                            wconn.execute(
                                """
                                INSERT INTO track_singles
                                    (track_id, discogs_release_id, single_title, bsides, year, fetched_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    track_id,
                                    release.id,
                                    release.title,
                                    json.dumps(bsides),
                                    release.year,
                                    now_iso(),
                                ),
                            )
                        singles_found += 1
                    except Exception as ex:
                        print(f"  ! Error fetching release {result.id}: {ex}", flush=True)

            except Exception as ex:
                print(f"  ! Search error for {artist} - {title}: {ex}", flush=True)

            with transaction(db_path) as wconn:
                wconn.execute(
                    "UPDATE tracks SET singles_checked_at = ? WHERE id = ?",
                    (now_iso(), track_id),
                )

            if singles_found:
                total_found += 1
                print(f"  [{track_num}/{total}] ✓ {artist} - {title} ({singles_found} single(s))", flush=True)
            else:
                total_none += 1
                print(f"  [{track_num}/{total}] – {artist} - {title}", flush=True)

    print(f"\nDone. Found singles for: {total_found}, none found: {total_none}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Discogs to find which album tracks were released as singles"
    )
    parser.add_argument("--token", default=os.environ.get("DISCOGS_TOKEN"),
                        help="Discogs user token (or set DISCOGS_TOKEN env var)")
    parser.add_argument("--db", default="music.db", help="SQLite database path")
    parser.add_argument("--batch", type=int, default=20,
                        help="Tracks per batch (default 20; Discogs rate limits apply)")
    parser.add_argument("--reset", action="store_true",
                        help="Clear singles_checked_at for tracks with no singles found, then exit")
    args = parser.parse_args()

    if args.reset:
        reset_singles(args.db)
        return

    if not args.token:
        raise SystemExit("Discogs token required: --token or DISCOGS_TOKEN env var")

    fetch_singles(args.token, args.db, args.batch)


if __name__ == "__main__":
    main()
