"""Engine abstraction for textbook search and retrieval across PostgreSQL and SQLite."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from retrieval.models import ChunkHit, SectionContent, SectionHit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_SQLITE_PATH = ROOT / "textbook_rag.db"
DEFAULT_PG_URL = os.environ.get("DATABASE_URL")

try:
    import psycopg
    HAVE_PSYCOPG = True
except ImportError:
    HAVE_PSYCOPG = False


class BaseEngine(ABC):
    """Abstract base class for textbook retrieval engines."""

    @abstractmethod
    def search_sections(
        self,
        query: str,
        book_slug: str | None = None,
        allowed_books: list[str] | None = None,
        limit: int = 5,
    ) -> list[SectionHit]:
        pass

    @abstractmethod
    def search_chunks(
        self,
        query: str,
        book_slug: str | None = None,
        section_id: int | None = None,
        content_type: str | None = None,
        allowed_books: list[str] | None = None,
        limit: int = 5,
    ) -> list[ChunkHit]:
        pass

    @abstractmethod
    def read_section(self, book_slug: str, section_id: int, max_tokens: int = 2500) -> SectionContent | None:
        pass

    @abstractmethod
    def get_toc(self, book_slug: str, parent_id: int | None = None) -> list[SectionHit]:
        pass

    @abstractmethod
    def get_chunk(self, chunk_id: str) -> ChunkHit | None:
        pass

    @abstractmethod
    def list_books(self) -> list[dict[str, Any]]:
        pass


# =============================================================================
# SQLite Engine (Default local zero-setup)
# =============================================================================

class SqliteEngine(BaseEngine):
    def __init__(self, db_path: Path = DEFAULT_SQLITE_PATH):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found at {self.db_path}. Run ingestion/load_to_db.py first.")
        self._local = threading.local()
        init_conn = self._get_connection()
        init_conn.execute("PRAGMA journal_mode=WAL;")
        init_conn.execute("PRAGMA synchronous=NORMAL;")
        self._ensure_sections_fts(init_conn)

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=15.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn = conn
        return self._local.conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_connection()

    def _ensure_sections_fts(self, conn: sqlite3.Connection | None = None):
        """Ensures an FTS5 table exists for fast section heading queries."""
        c = conn or self.conn
        cur = c.cursor()
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "sections_fts" not in tables:
            cur.executescript(
                """
                CREATE VIRTUAL TABLE sections_fts USING fts5(
                    section_id UNINDEXED,
                    book_slug UNINDEXED,
                    title,
                    heading_path,
                    summary
                );
                INSERT INTO sections_fts (section_id, book_slug, title, heading_path, summary)
                SELECT id, book_slug, title, heading_path, coalesce(summary, '') FROM sections;
                """
            )
            c.commit()

    STOP_WORDS = {
        "what", "is", "are", "the", "a", "an", "of", "and", "in", "to", "for", "with",
        "on", "at", "by", "from", "how", "does", "do", "explain", "describe", "tell",
        "me", "about", "can", "you", "please", "specific", "each", "which", "give",
        "hi", "hello", "hey", "there"
    }

    def _get_medical_vocabulary(self) -> set[str]:
        if not hasattr(self, "_cached_vocab") or self._cached_vocab is None:
            cur = self.conn.cursor()
            paths = [r[0] for r in cur.execute("SELECT heading_path FROM sections").fetchall() if r[0]]
            vocab = set()
            for p in paths:
                for w in re.findall(r"\b[a-zA-Z]{4,}\b", p.lower()):
                    vocab.add(w)
            self._cached_vocab = vocab
        return self._cached_vocab

    def _correct_spelling(self, word: str) -> str:
        if len(word) < 4:
            return word
        vocab = self._get_medical_vocabulary()
        if word.lower() in vocab:
            return word
        import difflib
        matches = difflib.get_close_matches(word.lower(), vocab, n=1, cutoff=0.68)
        return matches[0] if matches else word

    def _clean_fts_query(self, query: str, operator: str = "AND") -> str:
        words = [w for w in re.findall(r"\b\w+\b", query) if w]
        filtered = [w for w in words if w.lower() not in self.STOP_WORDS and len(w) > 1]
        target = [self._correct_spelling(w) for w in (filtered if filtered else words)]
        return f" {operator} ".join(f'"{w}"' for w in target) if target else query

    def search_sections(
        self,
        query: str,
        book_slug: str | None = None,
        allowed_books: list[str] | None = None,
        limit: int = 5,
    ) -> list[SectionHit]:
        if allowed_books is not None and len(allowed_books) == 0:
            return []

        cur = self.conn.cursor()
        fts_q = self._clean_fts_query(query, operator="AND")

        sql = """
            SELECT
                s.id, s.book_slug, s.title, s.heading_path, s.level,
                s.pdf_start, s.pdf_end, s.is_leaf, s.use,
                bm25(sections_fts) AS rank
            FROM sections_fts
            JOIN sections s ON s.id = sections_fts.section_id AND s.book_slug = sections_fts.book_slug
            WHERE sections_fts MATCH ?
        """
        params: list[Any] = [fts_q]
        if book_slug:
            sql += " AND s.book_slug = ?"
            params.append(book_slug)
        if allowed_books:
            placeholders = ",".join("?" for _ in allowed_books)
            sql += f" AND s.book_slug IN ({placeholders})"
            params.extend(allowed_books)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        try:
            rows = cur.execute(sql, params).fetchall()
            if not rows:
                fts_or = self._clean_fts_query(query, operator="OR")
                if fts_or != fts_q:
                    rows = cur.execute(sql, [fts_or] + params[1:]).fetchall()
        except sqlite3.OperationalError:
            # Fallback to simple LIKE if FTS expression has syntax quirks
            sql_fallback = """
                SELECT id, book_slug, title, heading_path, level, pdf_start, pdf_end, is_leaf, use, 1.0 AS rank
                FROM sections
                WHERE heading_path LIKE ?
            """
            params_fallback: list[Any] = [f"%{query}%"]
            if book_slug:
                sql_fallback += " AND book_slug = ?"
                params_fallback.append(book_slug)
            if allowed_books:
                placeholders = ",".join("?" for _ in allowed_books)
                sql_fallback += f" AND book_slug IN ({placeholders})"
                params_fallback.extend(allowed_books)
            sql_fallback += " LIMIT ?"
            params_fallback.append(limit)
            rows = cur.execute(sql_fallback, params_fallback).fetchall()

        return [
            SectionHit(
                id=r[0],
                book_slug=r[1],
                title=r[2],
                heading_path=r[3],
                level=r[4],
                page_start=r[5],
                page_end=r[6],
                is_leaf=bool(r[7]),
                use=r[8],
                score=float(r[9]),
            )
            for r in rows
        ]

    def search_chunks(
        self,
        query: str,
        book_slug: str | None = None,
        section_id: int | None = None,
        content_type: str | None = None,
        allowed_books: list[str] | None = None,
        limit: int = 5,
    ) -> list[ChunkHit]:
        if allowed_books is not None and len(allowed_books) == 0:
            return []

        cur = self.conn.cursor()
        fts_q = self._clean_fts_query(query)

        sql = """
            SELECT
                c.chunk_id, c.book_slug, c.section_id, s.heading_path,
                c.content_type, c.sub_type, c.page_start, c.page_end,
                c.token_count, c.text, bm25(chunks_fts) AS rank,
                snippet(chunks_fts, 5, '<<', '>>', '...', 25) AS snippet
            FROM chunks_fts
            JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
            JOIN sections s ON s.book_slug = c.book_slug AND s.id = c.section_id
            WHERE chunks_fts MATCH ?
        """
        params: list[Any] = [fts_q]
        if book_slug:
            sql += " AND c.book_slug = ?"
            params.append(book_slug)
        if allowed_books:
            placeholders = ",".join("?" for _ in allowed_books)
            sql += f" AND c.book_slug IN ({placeholders})"
            params.extend(allowed_books)
        if section_id is not None:
            sql += " AND c.section_id = ?"
            params.append(section_id)
        if content_type:
            sql += " AND c.content_type = ?"
            params.append(content_type)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        try:
            rows = cur.execute(sql, params).fetchall()
            if not rows:
                fts_or = self._clean_fts_query(query, operator="OR")
                if fts_or != fts_q:
                    rows = cur.execute(sql, [fts_or] + params[1:]).fetchall()
        except sqlite3.OperationalError:
            # Fallback to text LIKE
            sql_fallback = """
                SELECT
                    c.chunk_id, c.book_slug, c.section_id, s.heading_path,
                    c.content_type, c.sub_type, c.page_start, c.page_end,
                    c.token_count, c.text, 1.0 AS rank, substr(c.text, 1, 150) AS snippet
                FROM chunks c
                JOIN sections s ON s.book_slug = c.book_slug AND s.id = c.section_id
                WHERE c.text LIKE ?
            """
            params_fallback: list[Any] = [f"%{query}%"]
            if book_slug:
                sql_fallback += " AND c.book_slug = ?"
                params_fallback.append(book_slug)
            if allowed_books:
                placeholders = ",".join("?" for _ in allowed_books)
                sql_fallback += f" AND c.book_slug IN ({placeholders})"
                params_fallback.extend(allowed_books)
            if section_id is not None:
                sql_fallback += " AND c.section_id = ?"
                params_fallback.append(section_id)
            if content_type:
                sql_fallback += " AND c.content_type = ?"
                params_fallback.append(content_type)
            sql_fallback += " LIMIT ?"
            params_fallback.append(limit)
            rows = cur.execute(sql_fallback, params_fallback).fetchall()

        return [
            ChunkHit(
                chunk_id=r[0],
                book_slug=r[1],
                section_id=r[2],
                heading_path=r[3],
                content_type=r[4],
                sub_type=r[5],
                page_start=r[6],
                page_end=r[7],
                token_count=r[8],
                text=r[9],
                score=float(r[10]),
                snippet=r[11],
            )
            for r in rows
        ]

    def read_section(self, book_slug: str, section_id: int, max_tokens: int = 2500) -> SectionContent | None:
        cur = self.conn.cursor()
        sec = cur.execute(
            """
            SELECT id, book_slug, title, heading_path, level, pdf_start, pdf_end
            FROM sections WHERE book_slug = ? AND id = ?;
            """,
            (book_slug, section_id),
        ).fetchone()

        if not sec:
            return None

        chunk_rows = cur.execute(
            """
            SELECT
                chunk_id, book_slug, section_id, ?, content_type, sub_type,
                page_start, page_end, token_count, text
            FROM chunks
            WHERE book_slug = ? AND section_id = ?
            ORDER BY page_start ASC, id ASC;
            """,
            (sec[3], book_slug, section_id),
        ).fetchall()

        chunks: list[ChunkHit] = []
        accumulated_tokens = 0
        truncated = False

        for r in chunk_rows:
            tok = r[8]
            if accumulated_tokens + tok > max_tokens and chunks:
                truncated = True
                break
            chunks.append(
                ChunkHit(
                    chunk_id=r[0],
                    book_slug=r[1],
                    section_id=r[2],
                    heading_path=r[3],
                    content_type=r[4],
                    sub_type=r[5],
                    page_start=r[6],
                    page_end=r[7],
                    token_count=tok,
                    text=r[9],
                )
            )
            accumulated_tokens += tok

        return SectionContent(
            section_id=sec[0],
            book_slug=sec[1],
            title=sec[2],
            heading_path=sec[3],
            level=sec[4],
            page_start=sec[5],
            page_end=sec[6],
            total_tokens=accumulated_tokens,
            chunks=chunks,
            truncated=truncated,
        )

    def get_toc(self, book_slug: str, parent_id: int | None = None) -> list[SectionHit]:
        cur = self.conn.cursor()
        if parent_id is None:
            # Return top-level nodes (roots or chapters)
            rows = cur.execute(
                """
                SELECT id, book_slug, title, heading_path, level, pdf_start, pdf_end, is_leaf, use
                FROM sections
                WHERE book_slug = ? AND (parent_id IS NULL OR level <= 2)
                ORDER BY pdf_start ASC, id ASC;
                """,
                (book_slug,),
            ).fetchall()
        else:
            rows = cur.execute(
                """
                SELECT id, book_slug, title, heading_path, level, pdf_start, pdf_end, is_leaf, use
                FROM sections
                WHERE book_slug = ? AND parent_id = ?
                ORDER BY pdf_start ASC, id ASC;
                """,
                (book_slug, parent_id),
            ).fetchall()

        return [
            SectionHit(
                id=r[0],
                book_slug=r[1],
                title=r[2],
                heading_path=r[3],
                level=r[4],
                page_start=r[5],
                page_end=r[6],
                is_leaf=bool(r[7]),
                use=r[8],
            )
            for r in rows
        ]

    def get_chunk(self, chunk_id: str) -> ChunkHit | None:
        cur = self.conn.cursor()
        r = cur.execute(
            """
            SELECT
                c.chunk_id, c.book_slug, c.section_id, s.heading_path,
                c.content_type, c.sub_type, c.page_start, c.page_end,
                c.token_count, c.text
            FROM chunks c
            JOIN sections s ON s.book_slug = c.book_slug AND s.id = c.section_id
            WHERE c.chunk_id = ?;
            """,
            (chunk_id,),
        ).fetchone()

        if not r:
            return None

        return ChunkHit(
            chunk_id=r[0],
            book_slug=r[1],
            section_id=r[2],
            heading_path=r[3],
            content_type=r[4],
            sub_type=r[5],
            page_start=r[6],
            page_end=r[7],
            token_count=r[8],
            text=r[9],
        )

    def list_books(self) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        rows = cur.execute("SELECT id, slug, title, total_pages FROM books ORDER BY id ASC;").fetchall()
        return [
            {"id": r[0], "slug": r[1], "title": r[2], "total_pages": r[3]}
            for r in rows
        ]


# =============================================================================
# PostgreSQL Engine (Production backend)
# =============================================================================

class PostgresEngine(BaseEngine):
    def __init__(self, db_url: str = DEFAULT_PG_URL):
        if not HAVE_PSYCOPG:
            raise ImportError("psycopg is not installed.")
        self.db_url = db_url
        self.conn = psycopg.connect(self.db_url, autocommit=True)

    def search_sections(
        self,
        query: str,
        book_slug: str | None = None,
        allowed_books: list[str] | None = None,
        limit: int = 5,
    ) -> list[SectionHit]:
        if allowed_books is not None and len(allowed_books) == 0:
            return []

        with self.conn.cursor() as cur:
            sql = """
                SELECT
                    id, book_slug, title, heading_path, level,
                    pdf_start, pdf_end, is_leaf, use,
                    ts_rank_cd(tsv, websearch_to_tsquery('english', %s)) AS rank
                FROM sections
                WHERE tsv @@ websearch_to_tsquery('english', %s)
            """
            params: list[Any] = [query, query]
            if book_slug:
                sql += " AND book_slug = %s"
                params.append(book_slug)
            if allowed_books:
                sql += " AND book_slug = ANY(%s)"
                params.append(allowed_books)
            sql += " ORDER BY rank DESC LIMIT %s;"
            params.append(limit)

            cur.execute(sql, params)
            return [
                SectionHit(
                    id=r[0],
                    book_slug=r[1],
                    title=r[2],
                    heading_path=r[3],
                    level=r[4],
                    page_start=r[5],
                    page_end=r[6],
                    is_leaf=bool(r[7]),
                    use=r[8],
                    score=float(r[9]),
                )
                for r in cur.fetchall()
            ]

    def search_chunks(
        self,
        query: str,
        book_slug: str | None = None,
        section_id: int | None = None,
        content_type: str | None = None,
        allowed_books: list[str] | None = None,
        limit: int = 5,
    ) -> list[ChunkHit]:
        if allowed_books is not None and len(allowed_books) == 0:
            return []

        with self.conn.cursor() as cur:
            sql = """
                SELECT
                    c.chunk_id, c.book_slug, c.section_id, s.heading_path,
                    c.content_type, c.sub_type, c.page_start, c.page_end,
                    c.token_count, c.text,
                    ts_rank_cd(c.tsv, websearch_to_tsquery('english', %s)) AS rank,
                    ts_headline('english', c.text, websearch_to_tsquery('english', %s), 'StartSel=<<, StopSel=>>, MaxWords=25') AS snippet
                FROM chunks c
                JOIN sections s ON s.book_slug = c.book_slug AND s.id = c.section_id
                WHERE c.tsv @@ websearch_to_tsquery('english', %s)
            """
            params: list[Any] = [query, query, query]
            if book_slug:
                sql += " AND c.book_slug = %s"
                params.append(book_slug)
            if allowed_books:
                sql += " AND c.book_slug = ANY(%s)"
                params.append(allowed_books)
            if section_id is not None:
                sql += " AND c.section_id = %s"
                params.append(section_id)
            if content_type:
                sql += " AND c.content_type = %s"
                params.append(content_type)
            sql += " ORDER BY rank DESC LIMIT %s;"
            params.append(limit)

            cur.execute(sql, params)
            return [
                ChunkHit(
                    chunk_id=r[0],
                    book_slug=r[1],
                    section_id=r[2],
                    heading_path=r[3],
                    content_type=r[4],
                    sub_type=r[5],
                    page_start=r[6],
                    page_end=r[7],
                    token_count=r[8],
                    text=r[9],
                    score=float(r[10]),
                    snippet=r[11],
                )
                for r in cur.fetchall()
            ]

    def read_section(self, book_slug: str, section_id: int, max_tokens: int = 2500) -> SectionContent | None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, book_slug, title, heading_path, level, pdf_start, pdf_end
                FROM sections WHERE book_slug = %s AND id = %s;
                """,
                (book_slug, section_id),
            )
            sec = cur.fetchone()
            if not sec:
                return None

            cur.execute(
                """
                SELECT
                    chunk_id, book_slug, section_id, %s, content_type, sub_type,
                    page_start, page_end, token_count, text
                FROM chunks
                WHERE book_slug = %s AND section_id = %s
                ORDER BY page_start ASC, id ASC;
                """,
                (sec[3], book_slug, section_id),
            )
            chunk_rows = cur.fetchall()

        chunks: list[ChunkHit] = []
        accumulated_tokens = 0
        truncated = False

        for r in chunk_rows:
            tok = r[8]
            if accumulated_tokens + tok > max_tokens and chunks:
                truncated = True
                break
            chunks.append(
                ChunkHit(
                    chunk_id=r[0],
                    book_slug=r[1],
                    section_id=r[2],
                    heading_path=r[3],
                    content_type=r[4],
                    sub_type=r[5],
                    page_start=r[6],
                    page_end=r[7],
                    token_count=tok,
                    text=r[9],
                )
            )
            accumulated_tokens += tok

        return SectionContent(
            section_id=sec[0],
            book_slug=sec[1],
            title=sec[2],
            heading_path=sec[3],
            level=sec[4],
            page_start=sec[5],
            page_end=sec[6],
            total_tokens=accumulated_tokens,
            chunks=chunks,
            truncated=truncated,
        )

    def get_toc(self, book_slug: str, parent_id: int | None = None) -> list[SectionHit]:
        with self.conn.cursor() as cur:
            if parent_id is None:
                cur.execute(
                    """
                    SELECT id, book_slug, title, heading_path, level, pdf_start, pdf_end, is_leaf, use
                    FROM sections
                    WHERE book_slug = %s AND (parent_id IS NULL OR level <= 2)
                    ORDER BY pdf_start ASC, id ASC;
                    """,
                    (book_slug,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, book_slug, title, heading_path, level, pdf_start, pdf_end, is_leaf, use
                    FROM sections
                    WHERE book_slug = %s AND parent_id = %s
                    ORDER BY pdf_start ASC, id ASC;
                    """,
                    (book_slug, parent_id),
                )
            rows = cur.fetchall()

        return [
            SectionHit(
                id=r[0],
                book_slug=r[1],
                title=r[2],
                heading_path=r[3],
                level=r[4],
                page_start=r[5],
                page_end=r[6],
                is_leaf=bool(r[7]),
                use=r[8],
            )
            for r in rows
        ]

    def get_chunk(self, chunk_id: str) -> ChunkHit | None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.chunk_id, c.book_slug, c.section_id, s.heading_path,
                    c.content_type, c.sub_type, c.page_start, c.page_end,
                    c.token_count, c.text
                FROM chunks c
                JOIN sections s ON s.book_slug = c.book_slug AND s.id = c.section_id
                WHERE c.chunk_id = %s;
                """,
                (chunk_id,),
            )
            r = cur.fetchone()

        if not r:
            return None

        return ChunkHit(
            chunk_id=r[0],
            book_slug=r[1],
            section_id=r[2],
            heading_path=r[3],
            content_type=r[4],
            sub_type=r[5],
            page_start=r[6],
            page_end=r[7],
            token_count=r[8],
            text=r[9],
        )

    def list_books(self) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        rows = cur.execute("SELECT id, slug, title, total_pages FROM books ORDER BY id ASC;").fetchall()
        return [
            {"id": r[0], "slug": r[1], "title": r[2], "total_pages": r[3]}
            for r in rows
        ]


# =============================================================================
# Factory
# =============================================================================

def get_engine(db_url: str | None = None) -> BaseEngine:
    """Returns PostgreSQL engine if explicitly configured via db_url or DATABASE_URL, otherwise SQLite engine."""
    target_url = db_url or DEFAULT_PG_URL
    if target_url and HAVE_PSYCOPG:
        try:
            return PostgresEngine(target_url)
        except Exception:
            pass
    return SqliteEngine(DEFAULT_SQLITE_PATH)

