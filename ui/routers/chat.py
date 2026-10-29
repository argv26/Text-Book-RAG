"""Chat conversation and streaming SSE endpoints."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from context.models import CitationRecord
from ui.dependencies import get_orchestrator, get_rate_limiter, resolve_current_user, resolve_or_create_user
from ui.schemas import ChatRequest, ChatResponse
from ui.sessions import (
    create_session as create_consultation_session,
    generate_smart_title,
    get_session as get_consultation_session,
    get_session_messages,
    save_message as save_consultation_message,
    update_session_title,
)

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/quota")
def api_quota(request: Request, session_id: str | None = None):
    """Returns today's usage and remaining request quota for the client."""
    rate_limiter = get_rate_limiter()
    user = resolve_current_user(request)
    if user:
        client_id = f"user:{user['id']}"
    else:
        client_id = rate_limiter.get_client_id(request, session_id)

    used, remaining = rate_limiter.get_remaining(client_id)
    limit = int(os.environ.get("DAILY_REQUEST_LIMIT", "50"))
    return {"limit": limit, "used": used, "remaining": remaining}


@router.post("/chat", response_model=ChatResponse)
def api_chat(req: ChatRequest, request: Request, response: Response):
    """Processes a conversational turn with structure-aware tool retrieval and citations."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    user = resolve_or_create_user(request, response)
    client_id = f"user:{user['id']}"

    rate_limiter = get_rate_limiter()
    orchestrator = get_orchestrator()

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


@router.post("/chat/stream")
def api_chat_stream(req: ChatRequest, request: Request, response: Response):
    """Processes a conversational turn with progressive Server-Sent Events (SSE) streaming."""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    user = resolve_or_create_user(request, response)
    client_id = f"user:{user['id']}"

    rate_limiter = get_rate_limiter()
    orchestrator = get_orchestrator()

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

