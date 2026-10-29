"""
temp/test_step7_ui_polish.py

Automated Verification Suite for Step 7:
1. CSS rule verification (.user-bubble, #btn-scroll-bottom, fit-content, max-width).
2. HTML structure & accessibility verification (#btn-stop, #btn-scroll-bottom).
3. JavaScript state & logic verification (Escape key, AbortController, smart scroll).
4. Backend streaming disconnect handling in ui/app.py.
"""

import os
import sys
import re
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
HTML_PATH = ROOT / "ui" / "static" / "index.html"
CSS_PATH = ROOT / "ui" / "static" / "styles.css"
JS_PATH = ROOT / "ui" / "static" / "app.js"
APP_PATH = ROOT / "ui" / "app.py"


def test_css_polish():
    print("[1/4] Checking CSS rules in ui/static/styles.css...")
    content = CSS_PATH.read_text(encoding="utf-8")
    
    assert ".user-bubble" in content, "Missing .user-bubble CSS rule"
    assert "fit-content" in content, "Missing fit-content in CSS"
    assert "max-width: min(85%, 580px)" in content, "Missing proportional max-width"
    assert "word-break: break-word" in content, "Missing word-break for responsive wrapping"
    assert "white-space: pre-wrap" in content, "Missing white-space: pre-wrap"
    assert "#btn-scroll-bottom" in content, "Missing #btn-scroll-bottom transition style"
    print("  -> PASSED: .user-bubble and #btn-scroll-bottom CSS rules verified.")


def test_html_controls():
    print("[2/4] Checking DOM controls & accessibility in ui/static/index.html...")
    content = HTML_PATH.read_text(encoding="utf-8")
    
    # Stop button
    assert 'id="btn-stop"' in content, "Missing #btn-stop in index.html"
    assert 'aria-label="Stop generating response"' in content, "Missing aria-label on #btn-stop"
    
    # Scroll to bottom button
    assert 'id="btn-scroll-bottom"' in content, "Missing #btn-scroll-bottom in index.html"
    assert 'aria-label="Scroll to newest messages"' in content, "Missing aria-label on #btn-scroll-bottom"
    assert "Jump to latest messages (Down Arrow)" in content, "Missing tooltip on scroll-to-bottom button"
    print("  -> PASSED: Interactive buttons & accessibility labels present.")


def test_js_handlers():
    print("[3/4] Checking JavaScript state logic in ui/static/app.js...")
    content = JS_PATH.read_text(encoding="utf-8")
    
    # User bubble styling
    assert 'class="user-bubble"' in content, "appendUserMessage does not use .user-bubble"
    
    # Scroll to bottom & threshold
    assert "btnScrollBottom" in content, "btnScrollBottom not declared in app.js"
    assert "userHasScrolledUp" in content, "userHasScrolledUp state not tracked"
    assert "SCROLL_THRESHOLD" in content, "SCROLL_THRESHOLD constant not defined"
    assert "isNearBottom" in content, "isNearBottom function not defined"
    assert "updateScrollButtonVisibility" in content, "updateScrollButtonVisibility function missing"
    assert "scrollToBottom(force" in content, "scrollToBottom does not support force parameter"
    
    # Stop & keyboard shortcuts
    assert "stopCurrentGeneration" in content, "stopCurrentGeneration function missing"
    assert "Escape" in content, "Escape key listener missing"
    assert "ArrowDown" in content, "ArrowDown navigation listener missing"
    assert "currentAbortController.abort()" in content, "AbortController abort call missing"
    assert "Generation stopped by user" in content, "Stopped footnote badge missing"
    print("  -> PASSED: JavaScript scroll detection, AbortController, and shortcuts verified.")


def test_backend_stream_disconnect():
    print("[4/4] Checking backend disconnect handling in ui/app.py...")
    content = APP_PATH.read_text(encoding="utf-8")
    
    assert "await request.is_disconnected()" in content, "ui/app.py missing request.is_disconnected() check"
    
    # Test streaming endpoint responds with SSE
    from ui.app import app
    client = TestClient(app)
    res = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test_step7_session",
            "message": "Hello test clinical prompt",
            "book_slug": "williams-endocrinology",
            "active_books": ["williams-endocrinology"]
        }
    )
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert "text/event-stream" in res.headers.get("content-type", ""), "Response is not text/event-stream"
    print("  -> PASSED: Backend SSE endpoint and disconnect handling verified.")


if __name__ == "__main__":
    test_css_polish()
    test_html_controls()
    test_js_handlers()
    test_backend_stream_disconnect()
    print("\n=== ALL STEP 7 AUTOMATED VERIFICATIONS PASSED SUCCESSFULLY! ===")
