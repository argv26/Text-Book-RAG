"""Citation registry and source verification."""
from __future__ import annotations

import re
from typing import Any

from context.models import CitationRecord


class CitationRegistry:
    """Tracks retrieved chunks and verifies citations in assistant answers."""

    def __init__(self, existing_citations: dict[str, CitationRecord] | None = None):
        self._citations: dict[str, CitationRecord] = dict(existing_citations) if existing_citations else {}
        self._counter = len(self._citations) + 1

    @property
    def citations(self) -> dict[str, CitationRecord]:
        return self._citations

    def register_chunk(self, chunk: dict[str, Any]) -> CitationRecord:
        """Registers a retrieved chunk dict and returns a CitationRecord."""
        chunk_id = chunk.get("chunk_id", "")
        if chunk_id in self._citations:
            return self._citations[chunk_id]

        rec = CitationRecord(
            citation_id=self._counter,
            book_slug=chunk.get("book_slug", ""),
            section_id=chunk.get("section_id", 0),
            heading_path=chunk.get("heading_path", ""),
            page_start=chunk.get("page_start", 0),
            page_end=chunk.get("page_end", 0),
            chunk_id=chunk_id,
        )
        self._citations[chunk_id] = rec
        self._counter += 1
        return rec

    def register_chunks(self, chunks: list[dict[str, Any]]) -> list[CitationRecord]:
        """Registers multiple chunks from a tool result."""
        return [self.register_chunk(c) for c in chunks if isinstance(c, dict) and "chunk_id" in c]

    def format_bibliography(self) -> str:
        """Formats all registered citations as a numbered bibliography."""
        if not self._citations:
            return ""
        lines = ["\n### References & Citations:"]
        for rec in sorted(self._citations.values(), key=lambda r: r.citation_id):
            book_title = rec.book_slug.replace("_", " ").replace("-", " ").title()
            page_str = f"p. {rec.page_start}" if rec.page_start == rec.page_end else f"pp. {rec.page_start}–{rec.page_end}"
            lines.append(f"[{rec.citation_id}] {book_title}: {rec.heading_path} ({page_str})")
        return "\n".join(lines)

    def verify_answer_citations(self, text: str) -> dict[str, Any]:
        """Checks if citation references in the text match registered citations."""
        # Find citations formatted as [Williams p. 623] or [1]
        numeric_refs = set(int(m) for m in re.findall(r"\[(\d+)\]", text))
        registered_ids = {r.citation_id for r in self._citations.values()}

        valid = numeric_refs.issubset(registered_ids)
        missing = numeric_refs - registered_ids

        return {
            "valid": valid,
            "cited_count": len(numeric_refs),
            "unregistered_citations": list(missing),
            "total_available": len(self._citations),
        }

