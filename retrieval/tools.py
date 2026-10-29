"""High-level Retrieval Tools for Agents & LLMs.

Exposes 4 core tools:
1. search_sections(query, book_slug, limit)
2. search_chunks(query, book_slug, section_id, content_type, limit)
3. read_section(book_slug, section_id, max_tokens)
4. get_toc(book_slug, parent_id)

Can be imported directly by agents or invoked via CLI:
  python -m retrieval.tools search-sections "adrenal cortex"
  python -m retrieval.tools search-chunks "cortisol" --section-id 917
  python -m retrieval.tools read-section --book Williams_Textbook_of_Endocrinology_15th_edition_2 --section-id 6
  python -m retrieval.tools get-toc --book anatomy-and-physiology-2e_-_WEB
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from retrieval.engine import get_engine
from retrieval.models import ChunkHit, SectionContent, SectionHit

_GLOBAL_ENGINE = None


def _engine():
    global _GLOBAL_ENGINE
    if _GLOBAL_ENGINE is None:
        _GLOBAL_ENGINE = get_engine()
    return _GLOBAL_ENGINE


# =============================================================================
# Core LLM Tools (Callable functions returning JSON-serializable dicts)
# =============================================================================

def search_sections(
    query: str,
    book_slug: str | None = None,
    allowed_books: list[str] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Search textbook outline headings, titles, and section paths.

    Use this tool to discover WHERE a topic is located in the textbook before
    reading chunks. Returns matching section IDs, heading paths, and page ranges.

    Args:
        query: Clinical topic or keyword (e.g., 'adrenal cortex', 'thyroiditis').
        book_slug: Optional single book identifier to restrict search.
        allowed_books: Optional list of book identifiers to restrict search scope.
        limit: Maximum number of section nodes to return (default 5).

    Returns:
        List of section records containing section_id, title, heading_path, page_start, page_end.
    """
    hits = _engine().search_sections(
        query=query,
        book_slug=book_slug,
        allowed_books=allowed_books,
        limit=limit,
    )
    return [h.to_dict() for h in hits]


def search_chunks(
    query: str,
    book_slug: str | None = None,
    section_id: int | None = None,
    content_type: str | None = None,
    allowed_books: list[str] | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Search granular textbook passages, tables, and callout boxes.

    Use this tool to find specific factual evidence, definitions, dosages, or
    tables. Can be scoped to a specific section_id found via search_sections.

    Args:
        query: Specific search terms (e.g., 'dexamethasone suppression test', 'Table 2.1').
        book_slug: Optional single book identifier.
        section_id: Optional section ID to restrict search within a single section.
        content_type: Optional filter ('prose', 'table', 'box', 'figure_caption').
        allowed_books: Optional list of book identifiers to restrict search scope.
        limit: Maximum number of chunks to return (default 5).

    Returns:
        List of chunk records with text snippets, page numbers, heading paths, and chunk IDs.
    """
    hits = _engine().search_chunks(
        query=query,
        book_slug=book_slug,
        section_id=section_id,
        content_type=content_type,
        allowed_books=allowed_books,
        limit=limit,
    )
    return [h.to_dict() for h in hits]


def search_figures(
    query: str,
    book_slug: str | None = None,
    allowed_books: list[str] | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Search textbook figure captions, plates, and diagrams.

    Args:
        query: Specific medical keywords (e.g. 'blood pressure', 'thyroid synthesis', 'femoral triangle').
        book_slug: Optional single book identifier to restrict search.
        allowed_books: Optional list of book identifiers to restrict search scope.
        limit: Maximum number of figures to return (default 4).

    Returns:
        List of figure caption chunks with printed page numbers and source details.
    """
    hits = _engine().search_chunks(
        query=query,
        book_slug=book_slug,
        content_type="figure_caption",
        allowed_books=allowed_books,
        limit=limit,
    )
    return [h.to_dict() for h in hits]


def read_section(
    book_slug: str,
    section_id: int,
    max_tokens: int = 2500,
) -> dict[str, Any]:
    """Read an entire section with its prose, tables, and figures in reading order.

    Use this tool when you need the complete clinical explanation or narrative
    flow of a subsection, rather than disconnected search chunks.

    Args:
        book_slug: Book identifier (e.g., 'Williams_Textbook_of_Endocrinology_15th_edition_2').
        section_id: Section ID to retrieve.
        max_tokens: Soft token cap to prevent context explosion (default 2500).

    Returns:
        Structured section object with assembled markdown text and page boundaries.
    """
    content = _engine().read_section(book_slug=book_slug, section_id=section_id, max_tokens=max_tokens)
    if not content:
        return {"error": f"Section {section_id} not found in {book_slug}"}
    d = content.to_dict()
    d["assembled_text"] = content.assemble_text()
    return d


def get_toc(
    book_slug: str,
    parent_id: int | None = None,
) -> list[dict[str, Any]]:
    """Browse the table of contents of a book or sub-headings of a chapter.

    Args:
        book_slug: Book identifier.
        parent_id: Optional parent section ID. If None, lists root chapters.

    Returns:
        List of child sections in reading order with page ranges.
    """
    hits = _engine().get_toc(book_slug=book_slug, parent_id=parent_id)
    return [h.to_dict() for h in hits]


def get_chunk(chunk_id: str) -> dict[str, Any]:
    """Retrieve an exact chunk by its ID (useful for inspecting cited tables or figures)."""
    hit = _engine().get_chunk(chunk_id)
    if not hit:
        return {"error": f"Chunk {chunk_id} not found"}
    return hit.to_dict()


def list_books() -> list[dict[str, Any]]:
    """List all indexed textbooks and metadata."""
    return _engine().list_books()


# =============================================================================
# CLI Interface
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Text-Book-RAG Retrieval Tools CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search-sections
    p_sec = subparsers.add_parser("search-sections", help="Search outline heading tree")
    p_sec.add_argument("query", type=str, help="Search query")
    p_sec.add_argument("--book", type=str, default=None, help="Book slug")
    p_sec.add_argument("--limit", type=int, default=5, help="Result limit")

    # search-chunks
    p_chk = subparsers.add_parser("search-chunks", help="Search text chunks and tables")
    p_chk.add_argument("query", type=str, help="Search query")
    p_chk.add_argument("--book", type=str, default=None, help="Book slug")
    p_chk.add_argument("--section-id", type=int, default=None, help="Filter by section ID")
    p_chk.add_argument("--content-type", type=str, default=None, help="Filter by content type")
    p_chk.add_argument("--limit", type=int, default=5, help="Result limit")

    # read-section
    p_read = subparsers.add_parser("read-section", help="Read full section in order")
    p_read.add_argument("--book", type=str, required=True, help="Book slug")
    p_read.add_argument("--section-id", type=int, required=True, help="Section ID")
    p_read.add_argument("--max-tokens", type=int, default=2500, help="Max tokens")

    # get-toc
    p_toc = subparsers.add_parser("get-toc", help="Get table of contents")
    p_toc.add_argument("--book", type=str, required=True, help="Book slug")
    p_toc.add_argument("--parent-id", type=int, default=None, help="Parent section ID")

    # get-chunk
    p_gc = subparsers.add_parser("get-chunk", help="Get specific chunk by ID")
    p_gc.add_argument("chunk_id", type=str, help="Chunk ID")

    args = parser.parse_args()

    if args.command == "search-sections":
        results = search_sections(args.query, book_slug=args.book, limit=args.limit)
        print(f"\nSections matching: '{args.query}' ({len(results)} matches):")
        print("=" * 80)
        for r in results:
            print(f"ID {r['section_id']} [L{r['level']}] (p.{r['page_start']}..{r['page_end']})")
            print(f"  Path: {r['heading_path']}")
            print("-" * 80)

    elif args.command == "search-chunks":
        results = search_chunks(
            query=args.query,
            book_slug=args.book,
            section_id=args.section_id,
            content_type=args.content_type,
            limit=args.limit,
        )
        print(f"\nChunks matching: '{args.query}' ({len(results)} matches):")
        print("=" * 80)
        for r in results:
            print(f"[{r['content_type'].upper()}] ID: {r['chunk_id']} | Pages: {r['page_start']}..{r['page_end']}")
            print(f"  Path: {r['heading_path']}")
            snippet = r.get("snippet", r["text"][:140]).replace("\n", " ")
            print(f"  Snippet: {snippet}")
            print("-" * 80)

    elif args.command == "read-section":
        sec = read_section(book_slug=args.book, section_id=args.section_id, max_tokens=args.max_tokens)
        if "error" in sec:
            print(sec["error"])
            sys.exit(1)
        print(f"\n{'='*80}")
        print(f"Section {sec['section_id']}: {sec['title']} (Tokens: {sec['total_tokens']})")
        print(f"Path: {sec['heading_path']} | Pages: {sec['page_start']}..{sec['page_end']}")
        print(f"{'='*80}\n")
        print(sec["assembled_text"])

    elif args.command == "get-toc":
        toc = get_toc(book_slug=args.book, parent_id=args.parent_id)
        print(f"\nTOC for {args.book} (parent={args.parent_id}): {len(toc)} sections:")
        print("=" * 80)
        for s in toc:
            print(f"ID {s['section_id']:<5} [L{s['level']}] p.{s['page_start']:<4}..{s['page_end']:<4}  {s['title']}")

    elif args.command == "get-chunk":
        chunk = get_chunk(args.chunk_id)
        print(json.dumps(chunk, indent=2))


if __name__ == "__main__":
    main()

