"""Session and Consultation History persistence service for Text-Book-RAG.

Manages SQLite storage for consultation sessions and message turns in app_users.db,
enabling multi-turn medical consultation history, session switching, and smart auto-titling.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ui.auth import get_auth_db_path


@contextmanager
def _get_conn(db_path: Path | str | None = None):
    """Establishes an isolated SQLite connection with WAL mode, foreign keys, and auto-close."""
    target_path = get_auth_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path), timeout=10.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_sessions_db(db_path: Path | str | None = None) -> None:
    """Initializes consultation_sessions and consultation_messages tables and indices."""
    with _get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS consultation_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT 'New Consultation',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON consultation_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON consultation_sessions(updated_at DESC);

            CREATE TABLE IF NOT EXISTS consultation_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES consultation_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                citations_json TEXT,
                tool_calls_json TEXT,
                follow_ups_json TEXT,
                figures_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_id ON consultation_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_messages_created_at ON consultation_messages(created_at ASC);
            """
        )
        try:
            conn.execute("ALTER TABLE consultation_messages ADD COLUMN figures_json TEXT;")
        except Exception:
            pass


def generate_smart_title(query: str) -> str:
    """Generates a concise clinical title (max ~50 chars) from a medical question."""
    cleaned = query.strip()
    if not cleaned:
        return "New Consultation"

    # Remove common conversational and interrogative prefixes
    patterns = [
        r"^(can you\s+)?(please\s+)?(explain|describe|tell me about)\s+(the\s+)?(classic\s+|main\s+)?",
        r"^(how do you|how can you|how to|how does|how do|how is|how are)\s+(the\s+)?(describe\s+|explain\s+)?(the\s+)?",
        r"^(what is|what are|what was|what were)\s+(the\s+)?(classic\s+|main\s+)?",
        r"^(why does|why do|why is|why are)\s+(the\s+)?",
        r"^(what causes|what's)\s+(the\s+)?",
        r"^(give me|show me)\s+(the\s+)?",
        r"^(you describe|you explain)\s+(the\s+)?",
    ]
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE).strip()

    # Strip question marks, trailing punctuation, quotes
    cleaned = re.sub(r"[?!.,;:\'\"]+$", "", cleaned).strip()

    if not cleaned:
        cleaned = query.strip()[:45]

    # Truncate cleanly at word boundaries if exceeding 50 chars
    if len(cleaned) > 50:
        truncated = cleaned[:47]
        last_space = truncated.rfind(" ")
        if last_space > 20:
            candidate = truncated[:last_space] + "..."
        else:
            candidate = truncated + "..."
    else:
        candidate = cleaned

    # Capitalize title
    title = candidate.strip().title() if candidate else "New Consultation"
    return title


def create_session(
    user_id: str,
    title: str = "New Consultation",
    session_id: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Creates a new consultation session for the given user."""
    sid = session_id or f"ses_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO consultation_sessions (id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_id = excluded.user_id,
                title = CASE WHEN consultation_sessions.title = 'New Consultation' THEN excluded.title ELSE consultation_sessions.title END,
                updated_at = excluded.updated_at
            """,
            (sid, user_id, title.strip() or "New Consultation", now, now),
        )

    return {
        "id": sid,
        "user_id": user_id,
        "title": title.strip() or "New Consultation",
        "created_at": now,
        "updated_at": now,
    }


def list_user_sessions(
    user_id: str, limit: int = 50, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    """Returns all consultation sessions for a user sorted chronologically (latest first)."""
    with _get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) as message_count
            FROM consultation_sessions s
            LEFT JOIN consultation_messages m ON s.id = m.session_id
            WHERE s.user_id = ?
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r["id"],
            "user_id": r["user_id"],
            "title": r["title"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "message_count": r["message_count"],
        }
        for r in rows
    ]


def get_session(
    session_id: str, user_id: str, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    """Retrieves session metadata if owned by user_id."""
    with _get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, user_id, title, created_at, updated_at
            FROM consultation_sessions
            WHERE id = ? AND user_id = ?
            """,
            (session_id, user_id),
        )
        row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_session_messages(
    session_id: str, user_id: str, db_path: Path | str | None = None
) -> list[dict[str, Any]] | None:
    """Retrieves all chronologically ordered messages and citations for an authorized session."""
    session = get_session(session_id, user_id, db_path=db_path)
    if not session:
        return None

    with _get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, session_id, role, content, citations_json, tool_calls_json, follow_ups_json, figures_json, created_at
            FROM consultation_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        rows = cur.fetchall()

    messages = []
    for r in rows:
        citations = json.loads(r["citations_json"]) if r["citations_json"] else []
        tool_calls = json.loads(r["tool_calls_json"]) if r["tool_calls_json"] else []
        follow_ups = json.loads(r["follow_ups_json"]) if r["follow_ups_json"] else []
        figures = json.loads(r["figures_json"]) if ("figures_json" in r.keys() and r["figures_json"]) else []
        messages.append(
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "role": r["role"],
                "content": r["content"],
                "citations": citations,
                "tool_calls": tool_calls,
                "follow_ups": follow_ups,
                "figures": figures,
                "created_at": r["created_at"],
            }
        )
    return messages


def save_message(
    session_id: str,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    follow_ups: list[str] | None = None,
    figures: list[dict[str, Any]] | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Appends a new turn message to the session and updates session updated_at."""
    mid = f"msg_{uuid.uuid4().hex[:12]}"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cit_json = json.dumps(citations) if citations else None
    tools_json = json.dumps(tool_calls) if tool_calls else None
    follow_json = json.dumps(follow_ups) if follow_ups else None
    fig_json = json.dumps(figures) if figures else None

    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO consultation_messages (id, session_id, role, content, citations_json, tool_calls_json, follow_ups_json, figures_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mid, session_id, role, content, cit_json, tools_json, follow_json, fig_json, now),
        )
        conn.execute(
            """
            UPDATE consultation_sessions
            SET updated_at = ?
            WHERE id = ?
            """,
            (now, session_id),
        )

    return {
        "id": mid,
        "session_id": session_id,
        "role": role,
        "content": content,
        "citations": citations or [],
        "tool_calls": tool_calls or [],
        "follow_ups": follow_ups or [],
        "created_at": now,
    }


def update_session_title(
    session_id: str, user_id: str, new_title: str, db_path: Path | str | None = None
) -> bool:
    """Updates the title of a user-owned consultation."""
    clean_title = new_title.strip()
    if not clean_title:
        return False

    with _get_conn(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE consultation_sessions
            SET title = ?
            WHERE id = ? AND user_id = ?
            """,
            (clean_title[:100], session_id, user_id),
        )
        return cur.rowcount > 0


def delete_session(
    session_id: str, user_id: str, db_path: Path | str | None = None
) -> bool:
    """Deletes a consultation and its cascade messages."""
    with _get_conn(db_path) as conn:
        cur = conn.execute(
            """
            DELETE FROM consultation_sessions
            WHERE id = ? AND user_id = ?
            """,
            (session_id, user_id),
        )
        return cur.rowcount > 0
