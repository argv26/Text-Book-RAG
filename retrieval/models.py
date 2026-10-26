"""Data models for textbook search and retrieval tools."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SectionHit:
    """Represents a section node in the textbook outline hierarchy."""
    id: int
    book_slug: str
    title: str
    heading_path: str
    level: int
    page_start: int
    page_end: int
    is_leaf: bool
    use: str = "teaching"
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.id,
            "book_slug": self.book_slug,
            "title": self.title,
            "heading_path": self.heading_path,
            "level": self.level,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "is_leaf": self.is_leaf,
            "use": self.use,
            "score": round(self.score, 4) if self.score is not None else None,
        }


@dataclass
class ChunkHit:
    """Represents a retrieved passage, table, or callout box chunk."""
    chunk_id: str
    book_slug: str
    section_id: int
    heading_path: str
    content_type: str        # 'prose' | 'table' | 'box' | 'figure_caption'
    sub_type: str | None     # e.g. 'KEY POINTS', 'LEARNING OBJECTIVES'
    page_start: int
    page_end: int
    token_count: int
    text: str
    score: float | None = None
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "book_slug": self.book_slug,
            "section_id": self.section_id,
            "heading_path": self.heading_path,
            "content_type": self.content_type,
            "sub_type": self.sub_type,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "token_count": self.token_count,
            "text": self.text,
            "score": round(self.score, 4) if self.score is not None else None,
            "snippet": self.snippet,
        }


@dataclass
class SectionContent:
    """Represents an entire section with its constituent chunks in reading order."""
    section_id: int
    book_slug: str
    title: str
    heading_path: str
    level: int
    page_start: int
    page_end: int
    total_tokens: int
    chunks: list[ChunkHit] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "book_slug": self.book_slug,
            "title": self.title,
            "heading_path": self.heading_path,
            "level": self.level,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "total_tokens": self.total_tokens,
            "chunks_count": len(self.chunks),
            "truncated": self.truncated,
            "chunks": [c.to_dict() for c in self.chunks],
        }

    def assemble_text(self) -> str:
        """Assembles a continuous formatted text document of the section."""
        lines = [f"# {self.heading_path} (Pages {self.page_start}..{self.page_end})\n"]
        for c in self.chunks:
            if c.content_type == "box":
                lines.append(f"\n[CALLOUT BOX: {c.sub_type or 'BOX'}]\n{c.text}\n")
            elif c.content_type == "table":
                lines.append(f"\n[TABLE]\n{c.text}\n")
            elif c.content_type == "figure_caption":
                lines.append(f"\n[FIGURE CAPTION]\n{c.text}\n")
            else:
                lines.append(f"\n{c.text}\n")
        return "\n".join(lines)

