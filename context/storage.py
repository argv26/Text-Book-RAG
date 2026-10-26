"""Session storage backends for multi-turn conversation state."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any

from context.models import SessionState


class BaseSessionStore(ABC):
    """Abstract base class for session stores."""

    @abstractmethod
    def get_session(self, session_id: str) -> SessionState | None:
        pass

    @abstractmethod
    def save_session(self, session: SessionState) -> None:
        pass

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        pass


class InMemorySessionStore(BaseSessionStore):
    """Thread-safe in-memory session store suitable for CLI and development."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}

    def get_session(self, session_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)

    def save_session(self, session: SessionState) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

