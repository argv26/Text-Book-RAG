"""Context & Session Management Package for Text-Book-RAG."""
from context.citations import CitationRegistry
from context.compactor import estimate_tokens, prune_to_budget, virtualize_tool_outputs
from context.manager import ContextManager
from context.models import CitationRecord, Message, SessionState
from context.storage import BaseSessionStore, InMemorySessionStore

__all__ = [
    "ContextManager",
    "Message",
    "SessionState",
    "CitationRecord",
    "CitationRegistry",
    "BaseSessionStore",
    "InMemorySessionStore",
    "estimate_tokens",
    "virtualize_tool_outputs",
    "prune_to_budget",
]

