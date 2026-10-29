"""Engine factory and unified entrypoint for search and retrieval across backends."""
from __future__ import annotations

from pathlib import Path

from retrieval.base import BaseEngine
from retrieval.models import ChunkHit, SectionContent, SectionHit
from retrieval.postgres import DEFAULT_PG_URL, HAVE_PSYCOPG, PostgresEngine
from retrieval.sqlite import DEFAULT_SQLITE_PATH, SqliteEngine

__all__ = [
    "BaseEngine",
    "SqliteEngine",
    "PostgresEngine",
    "get_engine",
    "ChunkHit",
    "SectionHit",
    "SectionContent",
]


def get_engine(db_url: str | None = None) -> BaseEngine:
    """Returns PostgreSQL engine if explicitly configured via db_url or DATABASE_URL, otherwise SQLite engine."""
    target_url = db_url or DEFAULT_PG_URL
    if target_url and HAVE_PSYCOPG:
        try:
            return PostgresEngine(target_url)
        except Exception:
            pass
    return SqliteEngine(DEFAULT_SQLITE_PATH)
