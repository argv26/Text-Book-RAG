"""Parallel retrieval executor for concurrent chunks, sections, and figures querying."""
from __future__ import annotations

import concurrent.futures
import re
from typing import Any

from retrieval.tools import search_chunks, search_figures, search_sections


def _execute_parallel_retrieval(
    query: str,
    book_slug: str | None = None,
    allowed_books: list[str] | None = None,
    chunk_limit: int = 6,
    section_limit: int = 2,
    figure_limit: int = 4,
) -> dict[str, Any]:
    """Executes chunk search, section search, and figure search concurrently in a thread pool."""
    clean_query = re.sub(r"[^\w\s-]", " ", query).strip()
    c_args: dict[str, Any] = {"query": clean_query, "limit": chunk_limit}
    s_args: dict[str, Any] = {"query": clean_query, "limit": section_limit}
    f_args: dict[str, Any] = {"query": clean_query, "limit": figure_limit}

    if book_slug:
        c_args["book_slug"] = book_slug
        s_args["book_slug"] = book_slug
        f_args["book_slug"] = book_slug
    if allowed_books is not None:
        c_args["allowed_books"] = allowed_books
        s_args["allowed_books"] = allowed_books
        f_args["allowed_books"] = allowed_books

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_chunks = executor.submit(search_chunks, **c_args)
        f_sections = executor.submit(search_sections, **s_args)
        f_figures = executor.submit(search_figures, **f_args)

        chunks_res = f_chunks.result()
        sections_res = f_sections.result()
        try:
            figures_res = f_figures.result()
        except Exception as e:
            print(f"[ParallelRetrieval] Parallel figure search error: {e}")
            figures_res = []

    return {
        "clean_query": clean_query,
        "chunks": chunks_res,
        "sections": sections_res,
        "figures": figures_res,
    }


# Public alias
execute_parallel_retrieval = _execute_parallel_retrieval

