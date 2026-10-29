"""Pydantic schemas for request and response models."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel


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

