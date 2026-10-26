"""Data models for conversation context, session state, and citations."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CitationRecord:
    """Represents a verified textbook source citation."""
    citation_id: int
    book_slug: str
    section_id: int
    heading_path: str
    page_start: int
    page_end: int
    chunk_id: str

    @property
    def short_citation(self) -> str:
        """Formatted short inline reference, e.g. [Williams 15e, p.623]."""
        book_short = "Williams" if "Williams" in self.book_slug else "A&P 2e"
        if self.page_start == self.page_end:
            return f"[{book_short}, p.{self.page_start}]"
        return f"[{book_short}, pp.{self.page_start}-{self.page_end}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "book_slug": self.book_slug,
            "section_id": self.section_id,
            "heading_path": self.heading_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chunk_id": self.chunk_id,
            "short_citation": self.short_citation,
        }


@dataclass
class Message:
    """Represents a single message turn in the conversation."""
    role: str                       # 'system' | 'user' | 'assistant' | 'tool'
    content: str
    name: str | None = None          # tool name for tool role
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None  # id for tool response
    citations: list[CitationRecord] = field(default_factory=list)
    token_count: int = 0
    is_virtualized: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class SessionState:
    """Maintains state for a multi-turn conversation session."""
    session_id: str
    messages: list[Message] = field(default_factory=list)
    citations: dict[str, CitationRecord] = field(default_factory=dict)  # chunk_id -> CitationRecord
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

