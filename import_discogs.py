"""
import_discogs.py — Pull vinyl collection from Discogs into music.db.

Usage:
    python import_discogs.py --token YOUR_DISCOGS_TOKEN
    python import_discogs.py --token YOUR_DISCOGS_TOKEN --db music.db

Incremental: albums already in the DB (by discogs_id) are skipped, so
running this again only adds new releases.
"""
import argparse
import os
from datetime import datetime, timezone

import discogs_client

from version import __version__

from db import init_db, transaction


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def import_collection(token: str, db_path: str) -> None:
    init_db(db_path)

    d = discogs_client.Client("MusicEnrich/1.0", user_token=token)
    try:
        me = d.identity()
    except Exception as ex:
        raise SystemExit(f"Discogs auth failed: {ex}")

    print(f"Authenticated as {me.name}. Fetching collection…")

    albums_added = 0
    albums_skipped = 0
    tracks_added = 0
    seen_release_ids: set[int] = set()

    # Load existing album IDs so we can skip efficiently
    from db import get_connection
    with get_connection(db_path) as conn:
        existing = {row[0] for row in conn.execute("SELECT discogs_id FROM albums")}

    for release in me.collection_folders[0].releases:
        rid = release.release.id

        # Skip duplicates within the same Discogs collection response
        if rid in seen_release_ids:
            continue
        seen_release_ids.add(rid)

        if rid in existing:
            albums_skipped += 1
            continue

        # Build format string
        discogs_format = ""
        for fmt in release.release.formats:
            name = fmt.get("name", "")
            descs = " ".join(fmt.get("descriptions", []))
            discogs_format = f"{discogs_format} {name} {descs}".strip()

        try:
            notes = release.notes[2]["value"]
        except Exception:
            notes = ""

        try:
            artists_sort = release.release.artists_sort
        except Exception:
            artists_sort = ""

        styles = " ".join(release.release.styles) if release.release.styles else ""

        with transaction(db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO albums
                    (discogs_id, title, year, artists_sort, styles, format, notes, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rid, release.release.title, release.release.year,
                 artists_sort, styles, discogs_format, notes, now_iso()),
            )

            for track in release.release.tracklist:
                track_artists = " ".join(a.name for a in track.artists).strip()
                conn.execute(
                    """
                    INSERT INTO tracks (album_id, position, title, artists)
                    VALUES (?, ?, ?, ?)
                    """,
                    (rid, track.position, track.title, track_artists or None),
                )
                tracks_added += 1

        albums_added += 1
        print(f"  + {release.release.title} ({release.release.year})")

    print(
        f"\nDone. Albums added: {albums_added}, skipped: {albums_skipped}, "
        f"tracks added: {tracks_added}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Discogs vinyl collection into music.db"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("DISCOGS_TOKEN"),
        required=not os.environ.get("DISCOGS_TOKEN"),
        help="Discogs API user token (or set DISCOGS_TOKEN env var)",
    )
    parser.add_argument("--db", default="music.db", help="SQLite database path")
    args = parser.parse_args()

    import_collection(args.token, args.db)


if __name__ == "__main__":
    main()
