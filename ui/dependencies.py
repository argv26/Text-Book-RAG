"""Authentication and service dependencies for FastAPI endpoints."""
from __future__ import annotations

from typing import Any
from fastapi import Request, Response

from agent.orchestrator import AgentOrchestrator
from ui.auth import (
    create_guest_user,
    create_session,
    get_user_by_token,
)
from ui.rate_limiter import DailyRateLimiter

_orchestrator = AgentOrchestrator()
_rate_limiter = DailyRateLimiter()


def get_orchestrator() -> AgentOrchestrator:
    return _orchestrator


def get_rate_limiter() -> DailyRateLimiter:
    return _rate_limiter


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

