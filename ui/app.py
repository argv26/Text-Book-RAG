"""FastAPI Web Application for Text-Book-RAG Chat Interface."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Load .env file if present (no-op if python-dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.orchestrator import AgentOrchestrator
from retrieval.tools import get_chunk, list_books
from ui.rate_limiter import DailyRateLimiter

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

app = FastAPI(
    title="Structure-Aware Textbook RAG",
    description="Conversational medical textbook assistant with structure-aware retrieval and evidence drawer",
    version="1.0.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = AgentOrchestrator()
rate_limiter = DailyRateLimiter()


class ChatRequest(BaseModel):
    session_id: str
    message: str
    book_slug: str | None = None


class ChatResponse(BaseModel):
    reply: str
    citations: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    follow_ups: list[str]


@app.get("/api/quota")
def api_quota(request: Request, session_id: str | None = None):
    """Returns today's usage and remaining request quota for the client."""
    client_id = rate_limiter.get_client_id(request, session_id)
    used, remaining = rate_limiter.get_remaining(client_id)
    limit = int(os.environ.get("DAILY_REQUEST_LIMIT", "50"))
    return {"limit": limit, "used": used, "remaining": remaining}


@app.get("/api/books")
def api_list_books():
    """Retrieve all available indexed textbooks and page statistics."""
    try:
        books = list_books()
        return {"books": books}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat", response_model=ChatResponse)
def api_chat(req: ChatRequest, request: Request, response: Response):
    """Processes a conversational turn with structure-aware tool retrieval and citations."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    client_id = rate_limiter.get_client_id(request, req.session_id)
    allowed, used, remaining = rate_limiter.check_and_increment(client_id)
    limit = int(os.environ.get("DAILY_REQUEST_LIMIT", "50"))

    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Daily request limit reached ({limit}/{limit} queries used today). Quota resets at 00:00 UTC.",
            headers={"Retry-After": "86400", "X-RateLimit-Remaining": "0"},
        )

    try:
        res = orchestrator.run_turn(
            session_id=req.session_id,
            user_message=req.message,
            book_slug=req.book_slug,
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error executing agent turn: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error executing agent turn: {e}")


@app.get("/api/chunk/{chunk_id}")
def api_get_chunk(chunk_id: str):
    """Fetch raw chunk content and metadata for the interactive Evidence Drawer."""
    data = get_chunk(chunk_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@app.get("/api/session/{session_id}/citations")
def api_get_session_citations(session_id: str):
    """Fetch all accumulated verified citations for a multi-turn conversation session."""
    session = orchestrator.context_manager.get_or_create_session(session_id)
    return {
        "session_id": session_id,
        "citations": [c.to_dict() for c in session.citations.values()]
    }


# Mount static files
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    """Serves the main chat application single-page interface."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_file))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting Textbook RAG Web UI on http://127.0.0.1:{port}")
    uvicorn.run("ui.app:app", host="127.0.0.1", port=port, reload=False)

