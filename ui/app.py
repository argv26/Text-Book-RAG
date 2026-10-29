"""FastAPI Web Application for Text-Book-RAG Chat Interface."""
from __future__ import annotations

import asyncio
import json
import os
import re
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
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.orchestrator import AgentOrchestrator
from context.models import CitationRecord
from retrieval.tools import get_chunk, list_books
from ui.auth import (
    authenticate_user,
    create_guest_user,
    create_session,
    delete_session,
    get_user_by_token,
    register_user,
)
from ui.rate_limiter import DailyRateLimiter
from ui.diagrams import get_figure_image_path
from ui.sessions import (
    create_session as create_consultation_session,
    delete_session as delete_consultation_session,
    generate_smart_title,
    get_session as get_consultation_session,
    get_session_messages,
    init_sessions_db,
    list_user_sessions,
    save_message as save_consultation_message,
    update_session_title,
)

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

# Ensure session and auth databases exist
init_sessions_db()

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


def resolve_current_user(request: Request) -> dict[str, Any] | None:
    """Extracts and verifies authenticated user from cookie or Authorization Bearer header."""
    token = request.cookies.get("auth_session")
    if not token:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr[7:].strip()
    if token:
        return get_user_by_token(token)
    return None


def resolve_or_create_user(request: Request, response: Response) -> dict[str, Any]:
    """Resolves authenticated user; if unauthenticated, transparently creates guest user and sets cookie."""
    user = resolve_current_user(request)
    if user:
        return user

    guest_user = create_guest_user()
    token = create_session(guest_user["id"])
    response.set_cookie(
        key="auth_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    return guest_user


class AuthRequest(BaseModel):
    username: str
    password: str


class CreateSessionRequest(BaseModel):
    title: str = "New Consultation"


class UpdateSessionRequest(BaseModel):
    title: str


class ChatRequest(BaseModel):
    session_id: str
    message: str
    book_slug: str | None = None
    active_books: list[str] | None = None


class ChatResponse(BaseModel):
    reply: str
    citations: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    follow_ups: list[str]
    figures: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Authentication Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/signup", status_code=201)
def api_auth_signup(payload: AuthRequest, response: Response):
    """Registers a new standard user and sets session cookie."""
    user, err = register_user(payload.username, payload.password)
    if err:
        if "already taken" in err.lower():
            raise HTTPException(status_code=409, detail=err)
        raise HTTPException(status_code=400, detail=err)

    token = create_session(user["id"])
    response.set_cookie(
        key="auth_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    return {"user": user, "token": token}


@app.post("/api/auth/login")
def api_auth_login(payload: AuthRequest, response: Response):
    """Authenticates existing user credentials and sets session cookie."""
    user, err = authenticate_user(payload.username, payload.password)
    if err or not user:
        raise HTTPException(status_code=401, detail=err or "Invalid username or password.")

    token = create_session(user["id"])
    response.set_cookie(
        key="auth_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    return {"user": user, "token": token}


@app.post("/api/auth/guest")
def api_auth_guest(response: Response):
    """Creates a frictionless 1-click ephemeral guest user and session cookie."""
    user = create_guest_user()
    token = create_session(user["id"])
    response.set_cookie(
        key="auth_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        path="/",
    )
    return {"user": user, "token": token}


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    """Returns currently authenticated user, or null if unauthenticated."""
    user = resolve_current_user(request)
    return {"user": user}


@app.post("/api/auth/logout")
def api_auth_logout(request: Request, response: Response):
    """Invalidates active session token and clears the authentication cookie."""
    token = request.cookies.get("auth_session")
    if not token:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr[7:].strip()
    if token:
        delete_session(token)
    response.delete_cookie(key="auth_session", path="/")
    return {"status": "logged_out"}


# ---------------------------------------------------------------------------
# Consultation Sessions & History Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
def api_list_sessions(request: Request, response: Response):
    """Lists past consultation sessions for the active user/guest."""
    user = resolve_or_create_user(request, response)
    sessions = list_user_sessions(user["id"])
    return {"sessions": sessions}


@app.post("/api/sessions")
def api_create_session(payload: CreateSessionRequest, request: Request, response: Response):
    """Creates a new consultation session for the active user/guest."""
    user = resolve_or_create_user(request, response)
    sess = create_consultation_session(user_id=user["id"], title=payload.title)
    return {"session": sess}


@app.get("/api/sessions/{session_id}")
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


@app.patch("/api/sessions/{session_id}")
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


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str, request: Request):
    """Deletes consultation session and cascades to messages."""
    user = resolve_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    success = delete_consultation_session(session_id, user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Consultation session not found")
    orchestrator.context_manager.store.delete_session(session_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Application & Chat Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/quota")
def api_quota(request: Request, session_id: str | None = None):
    """Returns today's usage and remaining request quota for the client."""
    user = resolve_current_user(request)
    if user:
        client_id = f"user:{user['id']}"
    else:
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

    user = resolve_or_create_user(request, response)
    client_id = f"user:{user['id']}"

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

    # 1. Ensure consultation session exists in database
    db_session = get_consultation_session(req.session_id, user["id"])
    if not db_session:
        title = generate_smart_title(req.message)
        create_consultation_session(user_id=user["id"], title=title, session_id=req.session_id)
    elif db_session["title"] == "New Consultation":
        new_title = generate_smart_title(req.message)
        update_session_title(req.session_id, user["id"], new_title)

    # 2. Context Hydration: If session in memory is empty, restore previous messages from database
    active_sess = orchestrator.context_manager.get_or_create_session(req.session_id)
    if not active_sess.messages:
        db_messages = get_session_messages(req.session_id, user["id"]) or []
        for m in db_messages:
            if m["role"] == "user":
                orchestrator.context_manager.add_user_message(req.session_id, m["content"])
            elif m["role"] == "assistant":
                orchestrator.context_manager.add_assistant_message(
                    req.session_id,
                    content=m["content"],
                    tool_calls=m.get("tool_calls"),
                )
                for c in m.get("citations", []):
                    rec = CitationRecord(
                        citation_id=c["citation_id"],
                        book_slug=c["book_slug"],
                        section_id=c["section_id"],
                        heading_path=c["heading_path"],
                        page_start=c["page_start"],
                        page_end=c["page_end"],
                        chunk_id=c["chunk_id"],
                    )
                    active_sess.citations[rec.chunk_id] = rec

    # 3. Save User Message to database
    save_consultation_message(req.session_id, "user", req.message)

    try:
        res = orchestrator.run_turn(
            session_id=req.session_id,
            user_message=req.message,
            book_slug=req.book_slug,
            active_books=req.active_books,
        )
        # 4. Save Assistant Response and Citations to database
        save_consultation_message(
            req.session_id,
            "assistant",
            res["reply"],
            citations=res.get("citations"),
            tool_calls=res.get("tool_calls"),
            follow_ups=res.get("follow_ups"),
            figures=res.get("figures"),
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error executing agent turn: {e}")


@app.post("/api/chat/stream")
def api_chat_stream(req: ChatRequest, request: Request, response: Response):
    """Processes a conversational turn with progressive Server-Sent Events (SSE) streaming."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    user = resolve_or_create_user(request, response)
    client_id = f"user:{user['id']}"

    allowed, used, remaining = rate_limiter.check_and_increment(client_id)
    limit = int(os.environ.get("DAILY_REQUEST_LIMIT", "50"))

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Daily request limit reached ({limit}/{limit} queries used today). Quota resets at 00:00 UTC.",
            headers={"Retry-After": "86400", "X-RateLimit-Remaining": "0"},
        )

    # 1. Ensure consultation session exists in database
    db_session = get_consultation_session(req.session_id, user["id"])
    if not db_session:
        title = generate_smart_title(req.message)
        create_consultation_session(user_id=user["id"], title=title, session_id=req.session_id)
    elif db_session["title"] == "New Consultation":
        new_title = generate_smart_title(req.message)
        update_session_title(req.session_id, user["id"], new_title)

    # 2. Context Hydration: If session in memory is empty, restore previous messages from database
    active_sess = orchestrator.context_manager.get_or_create_session(req.session_id)
    if not active_sess.messages:
        db_messages = get_session_messages(req.session_id, user["id"]) or []
        for m in db_messages:
            if m["role"] == "user":
                orchestrator.context_manager.add_user_message(req.session_id, m["content"])
            elif m["role"] == "assistant":
                orchestrator.context_manager.add_assistant_message(
                    req.session_id,
                    content=m["content"],
                    tool_calls=m.get("tool_calls"),
                )
                for c in m.get("citations", []):
                    rec = CitationRecord(
                        citation_id=c["citation_id"],
                        book_slug=c["book_slug"],
                        section_id=c["section_id"],
                        heading_path=c["heading_path"],
                        page_start=c["page_start"],
                        page_end=c["page_end"],
                        chunk_id=c["chunk_id"],
                    )
                    active_sess.citations[rec.chunk_id] = rec

    # 3. Save User Message to database
    save_consultation_message(req.session_id, "user", req.message)

    async def event_stream():
        accumulated_tokens = []
        final_citations = []
        final_tools = []
        final_follow_ups = []
        final_figures = []
        final_reply_from_done = ""

        try:
            for item in orchestrator.stream_turn(
                session_id=req.session_id,
                user_message=req.message,
                book_slug=req.book_slug,
                active_books=req.active_books,
            ):
                if await request.is_disconnected():
                    break

                event_name = item.get("event", "message")
                data_obj = item.get("data", {})

                if event_name == "token":
                    accumulated_tokens.append(data_obj.get("delta", ""))
                elif event_name == "tools":
                    final_tools = data_obj
                elif event_name == "citations":
                    final_citations = data_obj
                elif event_name == "figures":
                    final_figures = data_obj
                elif event_name == "follow_ups":
                    final_follow_ups = data_obj
                elif event_name == "done":
                    final_reply_from_done = data_obj.get("reply", "")

                yield f"event: {event_name}\ndata: {json.dumps(data_obj)}\n\n"
                await asyncio.sleep(0)

            # 4. Save Assistant Response and Citations to database upon completion
            full_reply = final_reply_from_done or "".join(accumulated_tokens)
            if full_reply.strip():
                save_consultation_message(
                    req.session_id,
                    "assistant",
                    full_reply,
                    citations=final_citations,
                    tool_calls=final_tools,
                    follow_ups=final_follow_ups,
                    figures=final_figures,
                )
        except Exception as e:
            err_payload = json.dumps({"error": str(e)})
            yield f"event: error\ndata: {err_payload}\n\n"

    stream_res = StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(remaining),
        },
    )
    for header, val in response.raw_headers:
        if header.lower() == b"set-cookie":
            stream_res.raw_headers.append((header, val))
    return stream_res


@app.get("/api/chunk/{chunk_id}")
def api_get_chunk(chunk_id: str):
    """Fetch raw chunk content and metadata for the interactive Evidence Drawer."""
    data = get_chunk(chunk_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    if "text" in data and isinstance(data["text"], str):
        data["text"] = re.sub(r"(\b\w+)-\s*\n\s*(\w+\b)", r"\1\2", data["text"])
    return data


@app.get("/api/figure/{chunk_id}/image")
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


@app.get("/api/session/{session_id}/citations")
def api_get_session_citations(session_id: str, request: Request, response: Response):
    """Fetch all accumulated verified citations for a multi-turn conversation session."""
    user = resolve_or_create_user(request, response)
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

