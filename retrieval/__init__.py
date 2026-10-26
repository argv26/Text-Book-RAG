"""Text-Book-RAG Retrieval Package."""
from retrieval.engine import BaseEngine, PostgresEngine, SqliteEngine, get_engine
from retrieval.models import ChunkHit, SectionContent, SectionHit
from retrieval.tools import (
    get_chunk,
    get_toc,
    read_section,
    search_chunks,
    search_sections,
)

__all__ = [
    "search_sections",
    "search_chunks",
    "read_section",
    "get_toc",
    "get_chunk",
    "SectionHit",
    "ChunkHit",
    "SectionContent",
    "BaseEngine",
    "PostgresEngine",
    "SqliteEngine",
    "get_engine",
]

