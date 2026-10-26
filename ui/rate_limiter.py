"""Daily rate limiter for Text-Book-RAG with SQLite persistence."""
from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path
from typing import Tuple

from fastapi import Request

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "textbook_rag.db"


class DailyRateLimiter:
    """Tracks daily query usage per client IP/session using SQLite for persistence across restarts."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, default_limit: int = 50):
        self.db_path = db_path
        self.default_limit = int(os.environ.get("DAILY_REQUEST_LIMIT", str(default_limit)))
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_usage (
                    client_id TEXT NOT NULL,
                    day TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (client_id, day)
                );
                """
            )

    @staticmethod
    def today_str() -> str:
        """Returns current date in UTC as YYYY-MM-DD."""
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def get_client_id(request: Request, session_id: str | None = None) -> str:
        """Extracts client identifier prioritizing X-Forwarded-For, then remote host, then session_id."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # First IP in comma-separated list is client IP
            client_ip = forwarded.split(",")[0].strip()
            if client_ip:
                return client_ip

        if request.client and request.client.host:
            return request.client.host

        return session_id or "anonymous_client"

    def check_and_increment(self, client_id: str) -> Tuple[bool, int, int]:
        """Checks if client is within daily quota and increments counter if allowed.

        Returns:
            (allowed: bool, current_usage: int, remaining: int)
        """
        limit = int(os.environ.get("DAILY_REQUEST_LIMIT", str(self.default_limit)))
        # 0 or negative limit means unlimited
        if limit <= 0:
            return True, 0, 999999

        today = self.today_str()

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT count FROM daily_usage WHERE client_id = ? AND day = ?;",
                (client_id, today),
            )
            row = cur.fetchone()
            current_count = row[0] if row else 0

            if current_count >= limit:
                return False, current_count, 0

            new_count = current_count + 1
            cur.execute(
                """
                INSERT INTO daily_usage (client_id, day, count)
                VALUES (?, ?, ?)
                ON CONFLICT(client_id, day) DO UPDATE SET count = ?;
                """,
                (client_id, today, new_count, new_count),
            )
            remaining = max(0, limit - new_count)
            return True, new_count, remaining

    def get_remaining(self, client_id: str) -> Tuple[int, int]:
        """Gets (current_count, remaining) for a client without incrementing."""
        limit = int(os.environ.get("DAILY_REQUEST_LIMIT", str(self.default_limit)))
        if limit <= 0:
            return 0, 999999

        today = self.today_str()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT count FROM daily_usage WHERE client_id = ? AND day = ?;",
                (client_id, today),
            )
            row = cur.fetchone()
            current_count = row[0] if row else 0
            remaining = max(0, limit - current_count)
            return current_count, remaining
