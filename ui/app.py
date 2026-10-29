"""FastAPI application entrypoint for the Structure-Aware Textbook RAG Web UI."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ui.auth import init_auth_db
from ui.routers import auth, chat, content, sessions
from ui.sessions import init_sessions_db
import ui.dependencies as auth_deps

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

# Ensure session and auth databases exist
init_auth_db()
init_sessions_db()

# Expose orchestrator and rate limiter instances for compatibility
orchestrator = auth_deps.get_orchestrator()
rate_limiter = auth_deps.get_rate_limiter()

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

# Include Modular Routers
app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(content.router)

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
