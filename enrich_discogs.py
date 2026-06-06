"""
enrich_discogs.py — Back-fill missing album fields from Discogs.

Finds albums in the local DB that have empty or null fields (artists_sort,
year, styles, format) and re-fetches them from Discogs to fill in the gaps.

Usage:
    python enrich_discogs.py --token YOUR_DISCOGS_TOKEN
    python enrich_discogs.py --token YOUR_DISCOGS_TOKEN --db music.db
"""
import argparse
import os
import time
from datetime import datetime, timezone

import discogs_client

from db import init_db, get_connection, transaction
from version import __version__


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enrich_collection(token: str, db_path: str) -> None:
    init_db(db_path)

    d = discogs_client.Client(f"MusicEnrich/{__version__}", user_token=token)
    try:
        me = d.identity()
    except Exception as ex:
        raise SystemExit(f"Discogs auth failed: {ex}")

    print(f"Authenticated as {me.name}. Checking DB for incomplete albums…", flush=True)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT discogs_id, title, year, artists_sort, styles, format
            FROM albums
            WHERE artists_sort IS NULL OR artists_sort = ''
               OR year IS NULL OR year = 0
               OR styles IS NULL OR styles = ''
            ORDER BY title
            """
        ).fetchall()

    if not rows:
        print("All albums already have complete data. Nothing to do.")
        return

    print(f"Found {len(rows)} album(s) with missing fields. Fetching from Discogs…\n", flush=True)

    updated = 0
    errors = 0

    for row in rows:
        discogs_id = row["discogs_id"]
        title = row["title"]
        print(f"  Checking: {title} (id={discogs_id})", flush=True)

        time.sleep(1.0)  # Discogs rate limit

        try:
            release = d.release(discogs_id)

            artists_sort = ""
            try:
                artists_sort = release.artists_sort or ""
            except Exception:
                pass

            if not artists_sort:
                try:
                    names = [a.name for a in release.artists if a.name]
                    artists_sort = " / ".join(names)
                except Exception:
                    pass

            year = None
            try:
                year = release.year or None
            except Exception:
                pass

            styles = ""
            try:
                styles = " ".join(release.styles) if release.styles else ""
            except Exception:
                pass

            fmt = ""
            try:
                for f in release.formats:
                    name = f.get("name", "")
                    descs = " ".join(f.get("descriptions", []))
                    fmt = f"{fmt} {name} {descs}".strip()
            except Exception:
                pass

            with transaction(db_path) as conn:
                conn.execute(
                    """
                    UPDATE albums
                    SET artists_sort = COALESCE(NULLIF(artists_sort, ''), ?),
                        year         = COALESCE(NULLIF(year, 0), ?),
                        styles       = COALESCE(NULLIF(styles, ''), ?),
                        format       = COALESCE(NULLIF(format, ''), ?)
                    WHERE discogs_id = ?
                    """,
                    (artists_sort or None, year, styles or None, fmt or None, discogs_id),
                )

            fields = []
            if artists_sort and not row["artists_sort"]:
                fields.append(f"artist='{artists_sort}'")
            if year and not row["year"]:
                fields.append(f"year={year}")
            if styles and not row["styles"]:
                fields.append(f"styles='{styles}'")

            if fields:
                print(f"    ✓ Updated: {', '.join(fields)}", flush=True)
            else:
                print(f"    – No new data found on Discogs", flush=True)

            updated += 1
        except Exception as ex:
            print(f"    [error] {ex}", flush=True)
            errors += 1

    print(f"\nDone. Updated: {updated}, errors: {errors}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back-fill missing album fields from Discogs"
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("DISCOGS_TOKEN"),
        required=not os.environ.get("DISCOGS_TOKEN"),
        help="Discogs API user token (or set DISCOGS_TOKEN env var)",
    )
    parser.add_argument("--db", default="music.db", help="SQLite database path")
    args = parser.parse_args()

    enrich_collection(args.token, args.db)


if __name__ == "__main__":
    main()
