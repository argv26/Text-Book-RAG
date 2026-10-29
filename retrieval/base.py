"""Abstract base class for textbook search and retrieval engines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from retrieval.models import ChunkHit, SectionContent, SectionHit


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

