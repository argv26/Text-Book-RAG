"""High-level Context Manager for multi-turn sessions, token budgeting, and citations."""
from __future__ import annotations

import json
import time
from typing import Any

from context.citations import CitationRegistry
from context.compactor import estimate_tokens, prune_to_budget, virtualize_tool_outputs
from context.models import CitationRecord, Message, SessionState
from context.storage import BaseSessionStore, InMemorySessionStore


class ContextManager:
    """Manages multi-turn conversation context, dynamic token budgeting, and citation tracking."""

    def __init__(
        self,
        max_context_tokens: int = 8000,
        store: BaseSessionStore | None = None,
        virtualize_after_turns: int = 1,
    ):
        self.max_context_tokens = max_context_tokens
        self.store = store or InMemorySessionStore()
        self.virtualize_after_turns = virtualize_after_turns

    def get_or_create_session(self, session_id: str) -> SessionState:
        session = self.store.get_session(session_id)
        if session is None:
            session = SessionState(session_id=session_id)
            self.store.save_session(session)
        return session

    def add_user_message(self, session_id: str, content: str) -> Message:
        session = self.get_or_create_session(session_id)
        msg = Message(
            role="user",
            content=content.strip(),
            token_count=estimate_tokens(content),
            timestamp=time.time(),
        )
        session.messages.append(msg)
        session.updated_at = time.time()
        self.store.save_session(session)
        return msg

    def add_assistant_message(
        self,
        session_id: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> Message:
        session = self.get_or_create_session(session_id)
        msg = Message(
            role="assistant",
            content=content.strip() if content else "",
            tool_calls=tool_calls,
            token_count=estimate_tokens(content or ""),
            timestamp=time.time(),
        )
        session.messages.append(msg)
        session.updated_at = time.time()
        self.store.save_session(session)
        return msg

    def add_tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        name: str,
        result: Any,
        chunks_to_register: list[dict[str, Any]] | None = None,
    ) -> Message:
        session = self.get_or_create_session(session_id)

        # Convert result to JSON string if dict or list
        if isinstance(result, (dict, list)):
            content_str = json.dumps(result, ensure_ascii=False)
        else:
            content_str = str(result)

        # Register citations if chunks were returned
        registry = CitationRegistry(session.citations)
        citations: list[CitationRecord] = []
        if chunks_to_register:
            citations = registry.register_chunks(chunks_to_register)
        elif isinstance(result, list):
            # Auto-register if result is a list of chunk dicts
            citations = registry.register_chunks(result)
        elif isinstance(result, dict) and "chunks" in result:
            # e.g. read_section returns {'chunks': [...]}
            citations = registry.register_chunks(result["chunks"])

        session.citations = registry.citations

        msg = Message(
            role="tool",
            name=name,
            tool_call_id=tool_call_id,
            content=content_str,
            citations=citations,
            token_count=estimate_tokens(content_str),
            timestamp=time.time(),
        )
        session.messages.append(msg)
        session.updated_at = time.time()
        self.store.save_session(session)
        return msg

    def assemble_prompt(
        self,
        session_id: str,
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """Assembles the final list of messages formatted for LLM consumption.

        Applies:
        1. Tool output virtualization on older turns.
        2. Dynamic budget pruning to ensure total tokens <= max_context_tokens.
        """
        session = self.get_or_create_session(session_id)

        all_msgs = []
        if system_prompt:
            all_msgs.append(
                Message(
                    role="system",
                    content=system_prompt.strip(),
                    token_count=estimate_tokens(system_prompt),
                )
            )
        all_msgs.extend(session.messages)

        # 1. Virtualize past tool calls
        compacted = virtualize_tool_outputs(all_msgs, keep_last_turns=self.virtualize_after_turns)

        # 2. Prune to fit max token budget
        fitted = prune_to_budget(compacted, max_tokens=self.max_context_tokens)

        # Return standard dict format
        return [m.to_dict() for m in fitted]

    def get_session_citations(self, session_id: str) -> list[CitationRecord]:
        """Returns all citations registered in this session."""
        session = self.get_or_create_session(session_id)
        return sorted(session.citations.values(), key=lambda r: r.citation_id)

    def get_bibliography(self, session_id: str) -> str:
        """Returns a formatted bibliography of all sources retrieved in the session."""
        session = self.get_or_create_session(session_id)
        registry = CitationRegistry(session.citations)
        return registry.format_bibliography()

