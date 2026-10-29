"""PostgreSQL retrieval engine implementation for production environments."""
from __future__ import annotations

import os
from typing import Any

from retrieval.base import BaseEngine
from retrieval.models import ChunkHit, SectionContent, SectionHit

DEFAULT_PG_URL = os.environ.get("DATABASE_URL")

try:
    import psycopg
    HAVE_PSYCOPG = True
except ImportError:
    HAVE_PSYCOPG = False


class PostgresEngine(BaseEngine):
    def __init__(self, db_url: str = DEFAULT_PG_URL):
        if not HAVE_PSYCOPG:
            raise ImportError("psycopg is not installed. Install psycopg[binary] to use PostgresEngine.")
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

