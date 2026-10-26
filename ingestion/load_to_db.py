"""Step 7: Database Loader and Full-Text Search.

Supports:
1. PostgreSQL with pgvector (via psycopg and schema.sql).
2. SQLite with FTS5 (zero-setup local fallback).

Loads:
- ingestion/output/<book>/sections.json
- ingestion/output/<book>/chunks.jsonl

Usage:
  python ingestion/load_to_db.py                      # Load both books
  python ingestion/load_to_db.py --book <slug>        # Load single book
  python ingestion/load_to_db.py --search "homeostasis" # Search loaded chunks
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUTPUT_DIR = HERE / "output"
SCHEMA_SQL = HERE / "schema.sql"
DEFAULT_SQLITE_PATH = ROOT / "textbook_rag.db"
DEFAULT_PG_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/textbook_rag")

try:
    import psycopg
    from psycopg import sql
    HAVE_PSYCOPG = True
except ImportError:
    HAVE_PSYCOPG = False


# =============================================================================
# PostgreSQL Backend
# =============================================================================

class PostgresBackend:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.conn = None

    def connect(self) -> bool:
        if not HAVE_PSYCOPG:
            return False
        try:
            self.conn = psycopg.connect(self.db_url, autocommit=False)
            return True
        except Exception:
            return False

    def init_schema(self):
        ddl = SCHEMA_SQL.read_text(encoding="utf-8")
        with self.conn.cursor() as cur:
            cur.execute(ddl)
        self.conn.commit()

    def load_book(self, book_slug: str, sections: list[dict], chunks: list[dict]):
        title = book_slug.replace("_", " ").replace("-", " ").title()
        total_pages = max((c.get("page_end", 0) for c in chunks), default=1)

        with self.conn.cursor() as cur:
            # 1. Book record
            cur.execute(
                """
                INSERT INTO books (slug, title, total_pages)
                VALUES (%s, %s, %s)
                ON CONFLICT (slug) DO UPDATE
                SET total_pages = EXCLUDED.total_pages, title = EXCLUDED.title
                RETURNING id;
                """,
                (book_slug, title, total_pages),
            )

            # 2. Sections
            sec_rows = [
                (
                    s["id"],
                    book_slug,
                    s.get("parent_id"),
                    s["title"],
                    s["level"],
                    s.get("kind"),
                    s.get("use", "teaching"),
                    s["pdf_start"],
                    s["pdf_end"],
                    s.get("heading_path", s["title"]),
                    s.get("is_leaf", True),
                    s.get("summary"),
                    s.get("keywords", []),
                )
                for s in sections
            ]

            cur.executemany(
                """
                INSERT INTO sections (
                    id, book_slug, parent_id, title, level, kind, use,
                    pdf_start, pdf_end, heading_path, is_leaf, summary, keywords
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (book_slug, id) DO UPDATE SET
                    title = EXCLUDED.title,
                    heading_path = EXCLUDED.heading_path,
                    is_leaf = EXCLUDED.is_leaf,
                    summary = EXCLUDED.summary,
                    keywords = EXCLUDED.keywords;
                """,
                sec_rows,
            )

            # 3. Chunks (batch in chunks of 1000)
            chunk_rows = [
                (
                    c["chunk_id"],
                    book_slug,
                    c["section_id"],
                    c["content_type"],
                    c.get("sub_type"),
                    c["page_start"],
                    c["page_end"],
                    c["token_count"],
                    c["text"],
                    c.get("embed_text", c["text"]),
                    c["content_hash"],
                )
                for c in chunks
            ]

            batch_size = 1000
            for i in range(0, len(chunk_rows), batch_size):
                batch = chunk_rows[i : i + batch_size]
                cur.executemany(
                    """
                    INSERT INTO chunks (
                        chunk_id, book_slug, section_id, content_type, sub_type,
                        page_start, page_end, token_count, text, embed_text, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (content_hash) DO NOTHING;
                    """,
                    batch,
                )

        self.conn.commit()

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.chunk_id,
                    c.book_slug,
                    c.section_id,
                    c.content_type,
                    c.sub_type,
                    c.page_start,
                    c.page_end,
                    s.heading_path,
                    ts_rank_cd(c.tsv, websearch_to_tsquery('english', %s)) AS rank,
                    ts_headline('english', c.text, websearch_to_tsquery('english', %s), 'StartSel=<<, StopSel=>>, MaxWords=35') AS snippet
                FROM chunks c
                JOIN sections s ON s.book_slug = c.book_slug AND s.id = c.section_id
                WHERE c.tsv @@ websearch_to_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s;
                """,
                (query, query, query, limit),
            )
            cols = ["chunk_id", "book_slug", "section_id", "content_type", "sub_type", "page_start", "page_end", "heading_path", "rank", "snippet"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def count(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM books;")
            nb = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM sections;")
            ns = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM chunks;")
            nc = cur.fetchone()[0]
            return {"books": nb, "sections": ns, "chunks": nc}


# =============================================================================
# SQLite Backend (Zero-setup local fallback)
# =============================================================================

class SqliteBackend:
    def __init__(self, db_path: Path = DEFAULT_SQLITE_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")

    def init_schema(self):
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                total_pages INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sections (
                id INTEGER NOT NULL,
                book_slug TEXT NOT NULL,
                parent_id INTEGER,
                title TEXT NOT NULL,
                level INTEGER NOT NULL,
                kind TEXT,
                use TEXT DEFAULT 'teaching',
                pdf_start INTEGER NOT NULL,
                pdf_end INTEGER NOT NULL,
                heading_path TEXT NOT NULL,
                is_leaf BOOLEAN DEFAULT 1,
                summary TEXT,
                keywords TEXT,
                PRIMARY KEY (book_slug, id),
                FOREIGN KEY (book_slug) REFERENCES books(slug) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT UNIQUE NOT NULL,
                book_slug TEXT NOT NULL,
                section_id INTEGER NOT NULL,
                content_type TEXT NOT NULL,
                sub_type TEXT,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                token_count INTEGER NOT NULL,
                text TEXT NOT NULL,
                embed_text TEXT NOT NULL,
                content_hash TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_slug, section_id) REFERENCES sections(book_slug, id) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                book_slug UNINDEXED,
                section_id UNINDEXED,
                heading_path,
                content_type UNINDEXED,
                text
            );
            """
        )
        self.conn.commit()

    def load_book(self, book_slug: str, sections: list[dict], chunks: list[dict]):
        title = book_slug.replace("_", " ").replace("-", " ").title()
        total_pages = max((c.get("page_end", 0) for c in chunks), default=1)

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO books (slug, title, total_pages)
            VALUES (?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET total_pages=excluded.total_pages;
            """,
            (book_slug, title, total_pages),
        )

        sec_rows = [
            (
                s["id"],
                book_slug,
                s.get("parent_id"),
                s["title"],
                s["level"],
                s.get("kind"),
                s.get("use", "teaching"),
                s["pdf_start"],
                s["pdf_end"],
                s.get("heading_path", s["title"]),
                1 if s.get("is_leaf", True) else 0,
                s.get("summary"),
                json.dumps(s.get("keywords", [])),
            )
            for s in sections
        ]

        cur.executemany(
            """
            INSERT INTO sections (
                id, book_slug, parent_id, title, level, kind, use,
                pdf_start, pdf_end, heading_path, is_leaf, summary, keywords
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_slug, id) DO UPDATE SET
                title=excluded.title,
                heading_path=excluded.heading_path,
                is_leaf=excluded.is_leaf;
            """,
            sec_rows,
        )

        chunk_rows = [
            (
                c["chunk_id"],
                book_slug,
                c["section_id"],
                c["content_type"],
                c.get("sub_type"),
                c["page_start"],
                c["page_end"],
                c["token_count"],
                c["text"],
                c.get("embed_text", c["text"]),
                c["content_hash"],
            )
            for c in chunks
        ]

        cur.execute("DELETE FROM chunks_fts WHERE book_slug = ?;", (book_slug,))
        cur.executemany(
            """
            INSERT OR IGNORE INTO chunks (
                chunk_id, book_slug, section_id, content_type, sub_type,
                page_start, page_end, token_count, text, embed_text, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            chunk_rows,
        )

        sec_map = {s["id"]: s.get("heading_path", s["title"]) for s in sections}
        fts_rows = [
            (
                c["chunk_id"],
                book_slug,
                c["section_id"],
                sec_map.get(c["section_id"], ""),
                c["content_type"],
                c.get("embed_text", c["text"]),
            )
            for c in chunks
        ]

        cur.executemany(
            """
            INSERT INTO chunks_fts (
                chunk_id, book_slug, section_id, heading_path, content_type, text
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            fts_rows,
        )
        self.conn.commit()

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        cur = self.conn.cursor()
        # Clean query for FTS5 (split terms and wrap with quotes)
        terms = [f'"{w}"' for w in query.replace('"', "").split() if w]
        fts_query = " AND ".join(terms) if terms else query

        cur.execute(
            """
            SELECT
                c.chunk_id,
                c.book_slug,
                c.section_id,
                c.content_type,
                c.sub_type,
                c.page_start,
                c.page_end,
                s.heading_path,
                bm25(chunks_fts) AS rank,
                snippet(chunks_fts, 5, '<<', '>>', '...', 30) AS snippet
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            JOIN sections s ON s.book_slug = c.book_slug AND s.id = c.section_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?;
            """,
            (fts_query, limit),
        )
        cols = ["chunk_id", "book_slug", "section_id", "content_type", "sub_type", "page_start", "page_end", "heading_path", "rank", "snippet"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def count(self) -> dict[str, int]:
        cur = self.conn.cursor()
        nb = cur.execute("SELECT count(*) FROM books;").fetchone()[0]
        ns = cur.execute("SELECT count(*) FROM sections;").fetchone()[0]
        nc = cur.execute("SELECT count(*) FROM chunks;").fetchone()[0]
        return {"books": nb, "sections": ns, "chunks": nc}


# =============================================================================
# Loader Orchestration
# =============================================================================

def get_backend(preferred_target: str, db_url: str) -> tuple[Any, str]:
    if preferred_target in ("postgres", "auto"):
        pg = PostgresBackend(db_url)
        if pg.connect():
            print("Connected to PostgreSQL backend.")
            return pg, "postgres"
        elif preferred_target == "postgres":
            print(f"Error: Could not connect to PostgreSQL at {db_url}")
            sys.exit(1)

    print(f"Using SQLite FTS5 backend ({DEFAULT_SQLITE_PATH.name}).")
    sqlite = SqliteBackend(DEFAULT_SQLITE_PATH)
    return sqlite, "sqlite"


def load_book_dir(backend, book_dir: Path):
    book_slug = book_dir.name
    print(f"\nLoading book into database: {book_slug}")
    start = time.time()

    sec_file = book_dir / "sections.json"
    chunk_file = book_dir / "chunks.jsonl"

    if not sec_file.exists() or not chunk_file.exists():
        print(f"  Skipping {book_slug}: missing sections.json or chunks.jsonl")
        return

    sections = json.loads(sec_file.read_text(encoding="utf-8"))
    chunks = [json.loads(line) for line in chunk_file.open(encoding="utf-8")]

    backend.load_book(book_slug, sections, chunks)
    elapsed = time.time() - start
    print(f"  Loaded {len(sections)} sections and {len(chunks)} chunks in {elapsed:.2f}s")


def print_search_results(results: list[dict[str, Any]], query: str):
    print(f"\nSearch results for: '{query}' ({len(results)} matches):")
    print("=" * 80)
    for i, r in enumerate(results, start=1):
        print(f"#{i} [{r['content_type'].upper()}] (score: {round(float(r['rank']), 4)})")
        print(f"   Heading: {r['heading_path']}")
        print(f"   Book: {r['book_slug']} | Pages: {r['page_start']}..{r['page_end']} | ID: {r['chunk_id']}")
        snippet = r.get("snippet", "").replace("\n", " ")
        print(f"   Snippet: {snippet}")
        print("-" * 80)


def main():
    parser = argparse.ArgumentParser(description="Database loader and FTS verification.")
    parser.add_argument("--target", choices=["auto", "postgres", "sqlite"], default="auto", help="Target database backend")
    parser.add_argument("--db-url", type=str, default=DEFAULT_PG_URL, help="PostgreSQL connection URL")
    parser.add_argument("--book", type=str, help="Book folder to load")
    parser.add_argument("--search", type=str, help="Search query to test full-text search")
    args = parser.parse_args()

    backend, btype = get_backend(args.target, args.db_url)
    backend.init_schema()

    if args.search:
        results = backend.search(args.search, limit=5)
        print_search_results(results, args.search)
        return

    # Load books
    start_total = time.time()
    if args.book:
        target_dir = OUTPUT_DIR / args.book
        load_book_dir(backend, target_dir)
    else:
        for bdir in sorted(OUTPUT_DIR.iterdir()):
            if bdir.is_dir():
                load_book_dir(backend, bdir)

    counts = backend.count()
    total_time = time.time() - start_total
    print("\nDatabase load complete!")
    print(f"  Backend: {btype}")
    print(f"  Total Books: {counts['books']}, Sections: {counts['sections']}, Chunks: {counts['chunks']}")
    print(f"  Total Time: {total_time:.2f}s")

    # Run verification sample searches automatically
    print("\nRunning automated search verification...")
    res1 = backend.search("homeostasis", limit=2)
    print_search_results(res1, "homeostasis")

    res2 = backend.search("thyrotropin", limit=2)
    print_search_results(res2, "thyrotropin")


if __name__ == "__main__":
    main()
