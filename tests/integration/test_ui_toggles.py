"""Automated verification test for Sub-Step 3.3: Interactive Sidebar Library Toggles UI."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from ui.app import app


def test_html_elements():
    print("=================================================================", flush=True)
    print("Testing HTML Structure in ui/static/index.html", flush=True)
    print("=================================================================", flush=True)

    html_path = ROOT / "ui" / "static" / "index.html"
    assert html_path.exists(), "index.html does not exist"
    content = html_path.read_text(encoding="utf-8")

    # Assert new scope manager elements exist
    assert 'id="btn-select-all-books"' in content, "Missing #btn-select-all-books"
    assert 'id="scope-warning-toast"' in content, "Missing #scope-warning-toast"
    assert 'id="scope-warning-text"' in content, "Missing #scope-warning-text"
    assert 'id="library-list"' in content, "Missing #library-list"
    assert 'id="active-book-indicator"' in content, "Missing #active-book-indicator"
    assert 'book-select' not in content, "Deprecated book-select still present in HTML"

    print("[PASS] HTML contains all required Textbook Scope Manager DOM elements.", flush=True)


def test_js_logic_structure():
    print("=================================================================", flush=True)
    print("Testing JS Logic Structure in ui/static/app.js", flush=True)
    print("=================================================================", flush=True)

    js_path = ROOT / "ui" / "static" / "app.js"
    assert js_path.exists(), "app.js does not exist"
    content = js_path.read_text(encoding="utf-8")

    assert "textbook_rag_active_books" in content, "Missing localStorage key in app.js"
    assert "activeBooksSet" in content, "Missing activeBooksSet in app.js"
    assert "handleToggleBook" in content, "Missing handleToggleBook function"
    assert "showScopeWarning" in content, "Missing showScopeWarning function"
    assert "updateScopeIndicator" in content, "Missing updateScopeIndicator function"
    assert "active_books: Array.from(activeBooksSet)" in content, "Missing active_books in submitQuery payload"
    assert "role=\"switch\"" in content, "Missing role='switch' for accessibility"

    # Validate JavaScript Syntax if node is available
    import shutil
    import subprocess
    node_bin = shutil.which("node")
    if node_bin:
        res = subprocess.run([node_bin, "--check", str(js_path)], capture_output=True, text=True)
        assert res.returncode == 0, f"app.js syntax error: {res.stderr}"

    print("[PASS] app.js contains all necessary scope toggle, persistence, and accessibility logic.", flush=True)


def test_api_books():
    print("=================================================================", flush=True)
    print("Testing /api/books endpoint", flush=True)
    print("=================================================================", flush=True)

    client = TestClient(app)
    resp = client.get("/api/books")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "books" in data, "No 'books' key in /api/books response"
    books = data["books"]
    assert len(books) >= 2, f"Expected at least 2 books, found {len(books)}"
    print(f"[INFO] /api/books returned {len(books)} books:")
    for b in books:
        print(f"  - {b['title']} ({b['slug']}, {b['total_pages']} pp)", flush=True)
        assert "slug" in b
        assert "title" in b
        assert "total_pages" in b

    print("[PASS] /api/books provides complete catalogue schema for UI toggles.", flush=True)

    print("\nALL UI TOGGLE AUTOMATED CHECKS PASSED SUCCESSFULLY!", flush=True)


if __name__ == "__main__":
    test_html_elements()
    test_js_logic_structure()
    test_api_books()

