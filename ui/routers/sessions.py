"""Consultation sessions and chat history endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ui.dependencies import get_orchestrator, resolve_current_user, resolve_or_create_user
from ui.schemas import CreateSessionRequest, UpdateSessionRequest
from ui.sessions import (
    create_session as create_consultation_session,
    delete_session as delete_consultation_session,
    get_session as get_consultation_session,
    get_session_messages,
    list_user_sessions,
    update_session_title,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
def api_list_sessions(request: Request, response: Response):
    """Lists past consultation sessions for the active user/guest."""
    user = resolve_or_create_user(request, response)
    sessions = list_user_sessions(user["id"])
    return {"sessions": sessions}


@router.post("")
def api_create_session(payload: CreateSessionRequest, request: Request, response: Response):
    """Creates a new consultation session for the active user/guest."""
    user = resolve_or_create_user(request, response)
    sess = create_consultation_session(user_id=user["id"], title=payload.title)
    return {"session": sess}


@router.get("/{session_id}")
def api_get_session(session_id: str, request: Request, response: Response):
    """Retrieves metadata, message history, and citations for a consultation session."""
    user = resolve_or_create_user(request, response)
    sess = get_consultation_session(session_id, user["id"])
    if not sess:
        raise HTTPException(status_code=404, detail="Consultation session not found")

    messages = get_session_messages(session_id, user["id"]) or []
    citations_map = {}
    for m in messages:
        for c in m.get("citations", []):
            citations_map[str(c["citation_id"])] = c

    return {
        "session": sess,
        "messages": messages,
        "citations": list(citations_map.values()),
    }


@router.patch("/{session_id}")
def api_update_session_title(session_id: str, payload: UpdateSessionRequest, request: Request):
    """Updates consultation title."""
    user = resolve_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    success = update_session_title(session_id, user["id"], payload.title)
    if not success:
        raise HTTPException(status_code=404, detail="Consultation session not found")
    sess = get_consultation_session(session_id, user["id"])
    return {"status": "updated", "session": sess}


@router.delete("/{session_id}")
def api_delete_session(session_id: str, request: Request):
    """Deletes consultation session and cascades to messages."""
    user = resolve_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    success = delete_consultation_session(session_id, user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Consultation session not found")
    orchestrator = get_orchestrator()
    orchestrator.context_manager.store.delete_session(session_id)
    return {"status": "deleted"}

