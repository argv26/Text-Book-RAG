"""Authentication service and user session management for Text-Book-RAG.

Manages SQLite storage for user credentials (app_users.db), salted PBKDF2
password hashing, 1-click ephemeral guest sessions, and token persistence.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DEFAULT_AUTH_DB_PATH = Path(__file__).resolve().parent.parent / "app_users.db"


def get_auth_db_path(custom_path: Path | str | None = None) -> Path:
    """Resolves auth database path from argument, environment, or default."""
    if custom_path:
        return Path(custom_path)
    env_path = os.environ.get("AUTH_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_AUTH_DB_PATH


@contextmanager
def _get_conn(db_path: Path | str | None = None):
    """Establishes an isolated SQLite connection with WAL journal mode and auto-close."""
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


def init_auth_db(db_path: Path | str | None = None) -> None:
    """Initializes users and auth_sessions tables if they do not exist."""
    with _get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                salt TEXT,
                is_guest INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users(LOWER(username));

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON auth_sessions(user_id);
            """
        )


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Generates a random 16-byte salt (if none given) and PBKDF2-HMAC-SHA256 hash."""
    if salt is None:
        salt = secrets.token_hex(16)
    pwd_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8")
    pwd_hash = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt_bytes, 100_000).hex()
    return salt, pwd_hash


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """Timing-attack-safe comparison of provided password against stored hash."""
    _, computed_hash = hash_password(password, salt)
    return hmac.compare_digest(computed_hash, expected_hash)


def register_user(
    username: str, password: str, db_path: Path | str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Registers a new standard user account.

    Returns:
        tuple (user_dict, None) on success, or (None, error_message) on failure.
    """
    clean_username = username.strip()
    if len(clean_username) < 3:
        return None, "Username must be at least 3 characters long."
    if len(clean_username) > 32:
        return None, "Username cannot exceed 32 characters."
    if not re.match(r"^[a-zA-Z0-9_.-]+$", clean_username):
        return None, "Username can only contain letters, numbers, dots, underscores, and dashes."
    if len(password) < 4:
        return None, "Password must be at least 4 characters long."

    init_auth_db(db_path)
    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (clean_username,))
        if cursor.fetchone():
            return None, "Username already taken. Please choose another."

        salt, pwd_hash = hash_password(password)
        user_id = f"usr_{secrets.token_hex(8)}"
        cursor.execute(
            """
            INSERT INTO users (id, username, password_hash, salt, is_guest)
            VALUES (?, ?, ?, ?, 0)
            """,
            (user_id, clean_username, pwd_hash, salt),
        )
        conn.commit()

    return {
        "id": user_id,
        "username": clean_username,
        "is_guest": False,
    }, None


def authenticate_user(
    username: str, password: str, db_path: Path | str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Authenticates user with username and password.

    Returns:
        tuple (user_dict, None) on success, or (None, error_message) on failure.
    """
    clean_username = username.strip()
    if not clean_username or not password:
        return None, "Invalid username or password."

    init_auth_db(db_path)
    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, username, password_hash, salt, is_guest
            FROM users
            WHERE LOWER(username) = LOWER(?)
            """,
            (clean_username,),
        )
        row = cursor.fetchone()
        if not row:
            return None, "Invalid username or password."

        user_id, stored_username, pwd_hash, salt, is_guest = row
        if is_guest or not pwd_hash or not salt:
            return None, "Invalid username or password."

        if not verify_password(password, salt, pwd_hash):
            return None, "Invalid username or password."

        return {
            "id": user_id,
            "username": stored_username,
            "is_guest": False,
        }, None


def create_guest_user(db_path: Path | str | None = None) -> dict[str, Any]:
    """Generates an ephemeral 1-click guest user without a password."""
    init_auth_db(db_path)
    guest_suffix = secrets.token_hex(3)
    user_id = f"guest_{secrets.token_hex(6)}"
    username = f"Guest_{guest_suffix}"

    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO users (id, username, password_hash, salt, is_guest)
            VALUES (?, ?, NULL, NULL, 1)
            """,
            (user_id, username),
        )
        conn.commit()

    return {
        "id": user_id,
        "username": username,
        "is_guest": True,
    }


def create_session(user_id: str, days: int = 30, db_path: Path | str | None = None) -> str:
    """Creates a 32-byte secure session token for a given user id."""
    init_auth_db(db_path)
    token = secrets.token_hex(32)
    expires_at = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    ).isoformat()

    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO auth_sessions (token, user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (token, user_id, expires_at),
        )
        conn.commit()

    return token


def get_user_by_token(token: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Resolves an active user dict from a session token, rejecting expired sessions."""
    if not token or not token.strip():
        return None

    init_auth_db(db_path)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.id, u.username, u.is_guest, s.expires_at
            FROM auth_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ?
            """,
            (token.strip(),),
        )
        row = cursor.fetchone()
        if not row:
            return None

        user_id, username, is_guest, expires_at = row
        if expires_at and expires_at < now_iso:
            # Session expired: clean up
            cursor.execute("DELETE FROM auth_sessions WHERE token = ?", (token.strip(),))
            conn.commit()
            return None

        return {
            "id": user_id,
            "username": username,
            "is_guest": bool(is_guest),
        }


def delete_session(token: str, db_path: Path | str | None = None) -> bool:
    """Deletes an active session token upon logout."""
    if not token:
        return False
    init_auth_db(db_path)
    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM auth_sessions WHERE token = ?", (token.strip(),))
        conn.commit()
        return cursor.rowcount > 0

