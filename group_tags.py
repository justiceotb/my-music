"""
group_tags.py - Group all distinct theme tags into AI-generated themes.

Usage:
    python group_tags.py                          # Ollama + llama3 (default)
    python group_tags.py --model-type claude      # Claude via Anthropic API
    python group_tags.py --db music.db --chunk 200

Reads all distinct tags from the tracks table and asks an AI to assign each
tag to one of ~20-30 broad theme categories. Results are stored in tag_themes.
Safe to re-run: existing assignments are replaced.
"""
import argparse
import json
import logging
import os

from db import init_db, get_connection
from version import __version__

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("group_tags")

SYSTEM_PROMPT = """You are a music tag categoriser.

Given a list of theme tags from a music collection, assign each tag to a broad
category. Choose 20-30 category names that best cover the full set. Good examples:
Mood, Instrumentation, Era, Genre, Subject Matter, Energy, Relationships, Nature,
Spirituality, Darkness, Nostalgia, Protest, Celebration, Introspection.

Respond ONLY with a JSON object where each key is a tag and each value is the
category name. No markdown fences, no extra text. Every tag in the input must
appear as a key in the output."""

USER_TEMPLATE = """Assign each of the following music tags to a broad category:

{tag_list}"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("Model returned an empty response")
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        log.debug("Raw response that failed to parse:\n%s", text)
        raise


def _collect_tags(db_path: str) -> list[str]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT theme_tags FROM tracks WHERE theme_tags IS NOT NULL AND theme_tags != ''"
    ).fetchall()
    conn.close()
    counts: dict[str, int] = {}
    for row in rows:
        try:
            for tag in json.loads(row["theme_tags"]):
                counts[tag] = counts.get(tag, 0) + 1
        except Exception:
            pass
    return sorted(counts.keys())


def _call_ollama(client, model: str, tags: list[str]) -> dict:
    tag_list = "\n".join(tags)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(tag_list=tag_list)},
        ],
        temperature=0.2,
    )
    return _parse_json_response(response.choices[0].message.content)


def _call_claude(client, model: str, tags: list[str]) -> dict:
    tag_list = "\n".join(tags)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(tag_list=tag_list)}],
    )
    return _parse_json_response(response.content[0].text)


def group_tags(
    db_path: str,
    model_type: str,
    ollama_model: str,
    ollama_host: str,
    claude_model: str,
    chunk_size: int,
) -> None:
    init_db(db_path)

    all_tags = _collect_tags(db_path)
    if not all_tags:
        print("No tags found in database.", flush=True)
        return

    print(f"{len(all_tags)} distinct tags to group", flush=True)

    if model_type == "ollama":
        from openai import OpenAI
        client = OpenAI(base_url=f"{ollama_host}/v1", api_key="ollama")
        print(f"Using Ollama model: {ollama_model} at {ollama_host}", flush=True)
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        print(f"Using Claude model: {claude_model}", flush=True)

    # Split into chunks if requested (useful for smaller-context Ollama models)
    if chunk_size and chunk_size > 0:
        chunks = [all_tags[i:i + chunk_size] for i in range(0, len(all_tags), chunk_size)]
    else:
        chunks = [all_tags]

    assignments: dict[str, str] = {}
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            print(f"  Chunk {i + 1}/{len(chunks)} ({len(chunk)} tags)…", flush=True)
        try:
            if model_type == "ollama":
                result = _call_ollama(client, ollama_model, chunk)
            else:
                result = _call_claude(client, claude_model, chunk)
            assignments.update(result)
            print(f"  Got {len(result)} assignments", flush=True)
        except Exception as ex:
            print(f"  ! Error on chunk {i + 1}: {ex}", flush=True)
            log.debug("Full traceback:", exc_info=True)

    if not assignments:
        print("No assignments returned — aborting.", flush=True)
        return

    # Upsert into tag_themes
    conn = get_connection(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO tag_themes (tag, theme) VALUES (?, ?)",
        list(assignments.items()),
    )
    conn.commit()

    themes = set(assignments.values())
    print(
        f"\nDone. {len(assignments)} tags assigned to {len(themes)} themes.",
        flush=True,
    )
    for theme in sorted(themes):
        count = sum(1 for v in assignments.values() if v == theme)
        print(f"  {theme}: {count} tags", flush=True)

    conn.close()


def main() -> None:
    default_db = os.environ.get("DB_PATH", "music.db")
    default_ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    default_ollama_model = os.environ.get("OLLAMA_MODEL", "llama3")
    default_claude_model = "claude-haiku-4-5-20251001"

    parser = argparse.ArgumentParser(description=f"Group theme tags — v{__version__}")
    parser.add_argument("--model-type", choices=["ollama", "claude"], default="ollama")
    parser.add_argument("--ollama-model", default=default_ollama_model)
    parser.add_argument("--ollama-host", default=default_ollama_host)
    parser.add_argument("--claude-model", default=default_claude_model)
    parser.add_argument("--db", default=default_db)
    parser.add_argument(
        "--chunk", type=int, default=200,
        help="Split tags into chunks of this size (0 = send all at once, default: 200)",
    )
    args = parser.parse_args()

    if args.model_type == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set", flush=True)
        raise SystemExit(1)

    group_tags(
        db_path=args.db,
        model_type=args.model_type,
        ollama_model=args.ollama_model,
        ollama_host=args.ollama_host,
        claude_model=args.claude_model,
        chunk_size=args.chunk,
    )


if __name__ == "__main__":
    main()
