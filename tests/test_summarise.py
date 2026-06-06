"""
Tests for summarise.py.

Ollama (OpenAI SDK) and Claude (Anthropic SDK) are fully mocked.
No real tokens or running LLM needed.
"""
import json
from unittest.mock import MagicMock, patch

from db import get_connection
from summarise import summarise


def _seed_track_with_lyrics(db_path, track_id=1):
    conn = get_connection(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO albums "
        "(discogs_id, title, year, artists_sort, styles, format, notes, imported_at) "
        "VALUES (1, 'Abbey Road', 1969, 'Beatles, The', 'Rock', 'Vinyl LP', '', '2024-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO tracks (id, album_id, position, title, artists, lyrics, lyrics_fetched_at, lyrics_source) "
        "VALUES (?, 1, 'A1', 'Something', 'The Beatles', 'In the way she moves…', "
        "'2024-01-01T00:00:00+00:00', 'genius')",
        (track_id,),
    )
    conn.commit()
    conn.close()


def _get_track(db_path, track_id=1):
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    conn.close()
    return row


_GOOD_RESPONSE = json.dumps({"summary": "A love song.", "theme_tags": ["love", "longing"]})


# ── Ollama mode ───────────────────────────────────────────────────────────────

@patch("summarise.time.sleep")
@patch("openai.OpenAI")
def test_ollama_mode_saves_summary(MockOpenAI, mock_sleep, tmp_db):
    _seed_track_with_lyrics(tmp_db)

    mock_choice = MagicMock()
    mock_choice.message.content = _GOOD_RESPONSE
    mock_client = MockOpenAI.return_value
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    summarise(
        db_path=tmp_db,
        batch_size=20,
        model_type="ollama",
        ollama_model="llama3",
        ollama_host="http://localhost:11434",
        claude_model="claude-haiku-4-5-20251001",
    )

    row = _get_track(tmp_db)
    assert row["summary"] == "A love song."
    assert json.loads(row["theme_tags"]) == ["love", "longing"]
    assert row["ai_processed_at"] is not None


# ── Claude mode ───────────────────────────────────────────────────────────────

@patch("summarise.time.sleep")
@patch("anthropic.Anthropic")
def test_claude_mode_saves_summary(MockAnthropic, mock_sleep, tmp_db):
    _seed_track_with_lyrics(tmp_db)

    mock_content = MagicMock()
    mock_content.text = _GOOD_RESPONSE
    mock_client = MockAnthropic.return_value
    mock_client.messages.create.return_value = MagicMock(content=[mock_content])

    summarise(
        db_path=tmp_db,
        batch_size=20,
        model_type="claude",
        ollama_model="llama3",
        ollama_host="http://localhost:11434",
        claude_model="claude-haiku-4-5-20251001",
    )

    row = _get_track(tmp_db)
    assert row["summary"] == "A love song."
    assert json.loads(row["theme_tags"]) == ["love", "longing"]
    assert row["ai_processed_at"] is not None


# ── malformed JSON marks track and doesn't retry ──────────────────────────────

@patch("summarise.time.sleep")
@patch("openai.OpenAI")
def test_malformed_json_marks_track(MockOpenAI, mock_sleep, tmp_db):
    _seed_track_with_lyrics(tmp_db)

    mock_choice = MagicMock()
    mock_choice.message.content = "this is not json at all"
    mock_client = MockOpenAI.return_value
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    summarise(
        db_path=tmp_db,
        batch_size=20,
        model_type="ollama",
        ollama_model="llama3",
        ollama_host="http://localhost:11434",
        claude_model="claude-haiku-4-5-20251001",
    )

    row = _get_track(tmp_db)
    # ai_processed_at must be set so the track isn't retried endlessly
    assert row["ai_processed_at"] is not None
    # But no real summary was saved
    assert not row["summary"]


# ── skips already-processed tracks ───────────────────────────────────────────

@patch("summarise.time.sleep")
@patch("openai.OpenAI")
def test_skips_already_processed(MockOpenAI, mock_sleep, seeded_db):
    """The 'So What' track in seeded_db already has ai_processed_at set — should not be touched."""
    mock_client = MockOpenAI.return_value

    summarise(
        db_path=seeded_db,
        batch_size=20,
        model_type="ollama",
        ollama_model="llama3",
        ollama_host="http://localhost:11434",
        claude_model="claude-haiku-4-5-20251001",
    )

    # 'Something' has lyrics but no summary → will be processed
    # 'So What' already processed → must not be called again
    # Only the 'Something' track calls the API — call count = 1
    assert mock_client.chat.completions.create.call_count == 1
