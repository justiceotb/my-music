"""
summarise.py - Generate thematic summaries and tags for tracks that have lyrics.

Usage:
    python summarise.py                                  # Ollama + llama3 (default)
    python summarise.py --ollama-model mistral           # Different Ollama model
    python summarise.py --model-type claude              # Claude Haiku via Anthropic API
    python summarise.py --db music.db --batch 20

Resumable: only processes tracks where ai_processed_at IS NULL and lyrics IS NOT NULL.
Commits after each batch.

Output stored in DB as:
  summary    - 3-5 sentence thematic description
  theme_tags - JSON array of short tag strings e.g. ["longing", "travel"]
"""
import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone

from db import init_db, get_connection
from version import __version__

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("summarise")

SYSTEM_PROMPT = """You are a music analyst. Given a song's title, artist, album, and lyrics,
produce a JSON object with exactly two keys:
  "summary": a 3-5 sentence thematic analysis covering mood, imagery, and subject matter
  "theme_tags": a list of 3-8 short lowercase tag strings (e.g. "travel", "loss", "alcohol", "love")

Respond ONLY with valid JSON. No markdown fences, no extra text."""

USER_TEMPLATE = """Title: {title}
Artist: {artist}
Album: {album}

Lyrics:
{lyrics}"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def truncate_lyrics(lyrics: str, max_chars: int = 3000) -> str:
    """Keep lyrics under a reasonable token budget."""
    return lyrics[:max_chars] if len(lyrics) > max_chars else lyrics


def _parse_json_response(text: str) -> dict:
    """Parse JSON from AI response, stripping markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


def call_ollama(client, model: str, title: str, artist: str, album: str, lyrics: str) -> dict:
    prompt = USER_TEMPLATE.format(
        title=title, artist=artist, album=album, lyrics=truncate_lyrics(lyrics)
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return _parse_json_response(response.choices[0].message.content)


def call_claude(client, model: str, title: str, artist: str, album: str, lyrics: str) -> dict:
    prompt = USER_TEMPLATE.format(
        title=title, artist=artist, album=album, lyrics=truncate_lyrics(lyrics)
    )
    response = client.messages.create(
        model=model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json_response(response.content[0].text)


def summarise_one_track(
    db_path: str,
    track_id: int,
    model_type: str,
    ollama_model: str,
    ollama_host: str,
    claude_model: str,
) -> None:
    """Summarise a single track by DB id."""
    init_db(db_path)

    if model_type == "ollama":
        from openai import OpenAI
        client = OpenAI(base_url=f"{ollama_host}/v1", api_key="ollama")
        print(f"Using Ollama model: {ollama_model} at {ollama_host}")
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        print(f"Using Claude model: {claude_model}")

    conn = get_connection(db_path)
    row = conn.execute(
        """SELECT t.id, t.title, t.artists, a.artists_sort, a.title as album_title, t.lyrics
           FROM tracks t JOIN albums a ON a.discogs_id = t.album_id
           WHERE t.id = ? AND t.lyrics IS NOT NULL""",
        (track_id,),
    ).fetchone()

    if not row:
        print(f"Track {track_id} not found or has no lyrics")
        conn.close()
        return

    title = row["title"]
    artist = (row["artists"] or row["artists_sort"] or "").strip()
    album = row["album_title"]
    lyrics = row["lyrics"]

    try:
        log.debug("Calling %s for track %d: %s - %s", model_type, track_id, artist, title)
        if model_type == "ollama":
            result = call_ollama(client, ollama_model, title, artist, album, lyrics)
        else:
            result = call_claude(client, claude_model, title, artist, album, lyrics)

        summary = result.get("summary", "")
        theme_tags = json.dumps(result.get("theme_tags", []))
        conn.execute(
            "UPDATE tracks SET summary = ?, theme_tags = ?, ai_processed_at = ? WHERE id = ?",
            (summary, theme_tags, now_iso(), track_id),
        )
        conn.commit()
        print(f"  ✓ {artist} - {title}: {result.get('theme_tags', [])}", flush=True)
    except Exception as ex:
        print(f"  ! Error for {artist} - {title}: {ex}", flush=True)
        log.debug("Full traceback:", exc_info=True)
    finally:
        conn.close()


def summarise(
    db_path: str,
    batch_size: int,
    model_type: str,
    ollama_model: str,
    ollama_host: str,
    claude_model: str,
) -> None:
    init_db(db_path)

    if model_type == "ollama":
        from openai import OpenAI
        client = OpenAI(base_url=f"{ollama_host}/v1", api_key="ollama")
        print(f"Using Ollama model: {ollama_model} at {ollama_host}")
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        print(f"Using Claude model: {claude_model}")

    conn = get_connection(db_path)
    total_ok = 0
    total_err = 0

    while True:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.artists, a.artists_sort, a.title as album_title, t.lyrics
            FROM tracks t
            JOIN albums a ON a.discogs_id = t.album_id
            WHERE t.lyrics IS NOT NULL
              AND t.ai_processed_at IS NULL
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()

        if not rows:
            break

        print(f"Processing batch of {len(rows)} tracks…", flush=True)

        for row in rows:
            track_id = row["id"]
            title = row["title"]
            artist = (row["artists"] or row["artists_sort"] or "").strip()
            album = row["album_title"]
            lyrics = row["lyrics"]

            try:
                log.debug("Calling %s for track %d: %s - %s", model_type, track_id, artist, title)
                if model_type == "ollama":
                    result = call_ollama(client, ollama_model, title, artist, album, lyrics)
                else:
                    result = call_claude(client, claude_model, title, artist, album, lyrics)

                summary = result.get("summary", "")
                theme_tags = json.dumps(result.get("theme_tags", []))
                log.debug("Response for track %d: summary=%s tags=%s", track_id, summary, theme_tags)

                conn.execute(
                    """
                    UPDATE tracks
                    SET summary = ?, theme_tags = ?, ai_processed_at = ?
                    WHERE id = ?
                    """,
                    (summary, theme_tags, now_iso(), track_id),
                )
                conn.commit()
                total_ok += 1
                print(f"  ✓ {artist} - {title}: {result.get('theme_tags', [])}", flush=True)

            except Exception as ex:
                total_err += 1
                print(f"  ! Error for {artist} - {title}: {ex}", flush=True)
                log.debug("Full traceback:", exc_info=True)

            # Polite delay between API calls
            time.sleep(0.3)

    conn.close()
    print(f"\nDone. Summarised: {total_ok}, errors: {total_err}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate thematic summaries and tags for tracks with lyrics"
    )
    parser.add_argument(
        "--model-type",
        choices=["ollama", "claude"],
        default="ollama",
        help="LLM backend to use (default: ollama)",
    )
    parser.add_argument(
        "--ollama-model",
        default=os.environ.get("OLLAMA_MODEL", "llama3"),
        help="Ollama model name (default: llama3)",
    )
    parser.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        help="Ollama server URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--claude-model",
        default="claude-haiku-4-5-20251001",
        help="Anthropic model ID (default: claude-haiku-4-5-20251001)",
    )
    parser.add_argument("--db", default="music.db", help="SQLite database path")
    parser.add_argument(
        "--batch", type=int, default=20, help="Tracks per commit batch (default 20)"
    )
    parser.add_argument("--track-id", type=int, default=None,
                        help="Summarise a single track by DB id and exit")
    args = parser.parse_args()

    if args.model_type == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY env var is required for Claude mode")

    if args.track_id is not None:
        summarise_one_track(
            db_path=args.db,
            track_id=args.track_id,
            model_type=args.model_type,
            ollama_model=args.ollama_model,
            ollama_host=args.ollama_host,
            claude_model=args.claude_model,
        )
        return

    summarise(
        db_path=args.db,
        batch_size=args.batch,
        model_type=args.model_type,
        ollama_model=args.ollama_model,
        ollama_host=args.ollama_host,
        claude_model=args.claude_model,
    )


if __name__ == "__main__":
    main()
