"""Verification of Frontend UI components and static assets for Step 2."""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
from ui.app import app


def test_ui():
    print("=== Running Session Frontend UI Verification ===")
    client = TestClient(app)

    # 1. Verify GET / serves index.html with 200 OK
    res = client.get("/")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    html = res.text

    # 2. Check essential Step 2 UI elements in HTML
    assert 'id="sessions-list"' in html, "Missing #sessions-list in index.html"
    assert 'id="sessions-count-badge"' in html, "Missing #sessions-count-badge in index.html"
    assert 'id="sessions-empty"' in html, "Missing #sessions-empty in index.html"
    assert 'id="btn-new-chat"' in html, "Missing #btn-new-chat in index.html"
    assert 'Recent Consultations' in html, "Missing Recent Consultations section header"
    assert 'Target Textbook Scope' in html, "Missing Target Textbook Scope section"
    assert 'Indexed Medical Volumes' in html, "Missing Indexed Medical Volumes accordion"
    print("[OK] All required Sidebar UI elements are present in index.html")

    # 3. Verify static/app.js is served and contains Step 2 functions
    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    js = res_js.text
    assert "fetchSessions" in js, "Missing fetchSessions in app.js"
    assert "renderSessionsList" in js, "Missing renderSessionsList in app.js"
    assert "loadSession" in js, "Missing loadSession in app.js"
    assert "deleteConsultation" in js, "Missing deleteConsultation in app.js"
    print("[OK] app.js contains all consultation history and session switching handlers")

    # 4. Verify static/styles.css is served
    res_css = client.get("/static/styles.css")
    assert res_css.status_code == 200
    print("[OK] styles.css served with 200 OK")

    print("\nALL UI STATIC & HTML CHECKS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    test_ui()

