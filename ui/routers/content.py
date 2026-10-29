"""Content, textbooks, chunks, figures, and bibliography citation endpoints."""
from __future__ import annotations

import re
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from retrieval.tools import get_chunk, list_books
from ui.dependencies import get_orchestrator, resolve_or_create_user
from ui.diagrams import get_figure_image_path
from ui.sessions import get_session_messages

router = APIRouter(prefix="/api", tags=["content"])


@router.get("/books")
def api_list_books():
    """Retrieve all available indexed textbooks and page statistics."""
    try:
        books = list_books()
        return {"books": books}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chunk/{chunk_id}")
def api_get_chunk(chunk_id: str):
    """Fetch raw chunk content and metadata for the interactive Evidence Drawer."""
    data = get_chunk(chunk_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    if "text" in data and isinstance(data["text"], str):
        data["text"] = re.sub(r"(\b\w+)-\s*\n\s*(\w+\b)", r"\1\2", data["text"])
    return data


@router.get("/figure/{chunk_id}/image")
def api_get_figure_image(chunk_id: str):
    """Serve high-resolution textbook figure or diagram image."""
    img_path = get_figure_image_path(chunk_id)
    if not img_path or not img_path.exists():
        raise HTTPException(status_code=404, detail="Figure image not available for this chunk")
    return FileResponse(
        str(img_path),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/session/{session_id}/citations")
def api_get_session_citations(session_id: str, request: Request, response: Response):
    """Fetch all accumulated verified citations for a multi-turn conversation session."""
    user = resolve_or_create_user(request, response)
    orchestrator = get_orchestrator()
    session = orchestrator.context_manager.store.get_session(session_id)
    if session and session.citations:
        return {
            "session_id": session_id,
            "citations": [c.to_dict() for c in session.citations.values()]
        }

    # Fallback to database
    messages = get_session_messages(session_id, user["id"]) or []
    citations_map = {}
    for m in messages:
        for c in m.get("citations", []):
            citations_map[str(c["citation_id"])] = c

    return {
        "session_id": session_id,
        "citations": list(citations_map.values())
    }

