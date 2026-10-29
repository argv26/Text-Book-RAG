"""Authentication routes for login, signup, guest access, and token management."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ui.auth import (
    authenticate_user,
    create_guest_user,
    create_session,
    delete_session,
    register_user,
)
from ui.dependencies import resolve_current_user
from ui.schemas import AuthRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/signup", status_code=201)
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


@router.post("/login")
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


@router.post("/guest")
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


@router.get("/me")
def api_auth_me(request: Request):
    """Returns currently authenticated user, or null if unauthenticated."""
    user = resolve_current_user(request)
    return {"user": user}


@router.post("/logout")
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

