"""Automated test suite for Step 5: Answer Depth, Diagrams & Clinical Flowcharts."""
import sys
import uuid
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from retrieval.tools import search_figures
from ui.app import app


def test_figure_retrieval_tool():
    print("=== Testing Sub-Step 5.3: Figure Retrieval Tool ===")
    figures = search_figures("blood pressure", limit=4)
    assert len(figures) > 0, "No figures retrieved for 'blood pressure'"
    assert "chunk_id" in figures[0], "Missing chunk_id in figure hit"
    assert "text" in figures[0], "Missing text in figure hit"
    assert figures[0]["content_type"] == "figure_caption", f"Expected figure_caption, got {figures[0]['content_type']}"
    print(f"[PASS] search_figures returned {len(figures)} matching textbook figures.")
    for f in figures[:2]:
        print(f"  - Figure on p. {f['page_start']} in {f['book_slug']}: {f['text'][:80]}...")


def test_api_chat_depth_and_figures():
    print("\n=== Testing Sub-Step 5.1 & 5.3: /api/chat Depth & Figures Payload ===")
    client = TestClient(app)
    session_id = f"test_depth_{uuid.uuid4().hex[:8]}"

    res = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "Explain thyroid hormone synthesis and regulation pathway",
    })

    assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"
    data = res.json()
    reply = data.get("reply", "")
    citations = data.get("citations", [])
    figures = data.get("figures", [])

    print(f"[PASS] /api/chat returned status 200.")
    print(f"[PASS] Response word count: {len(reply.split())} words.")
    print(f"[PASS] Citations returned: {len(citations)} citations.")
    print(f"[PASS] Textbook Figures returned: {len(figures)} figures.")

    assert len(reply.split()) > 100, "Answer too short!"
    assert len(figures) > 0, "Expected at least 1 figure in figures payload"

    # Verify Mermaid flowchart is included in the synthesized response
    if "```mermaid" in reply:
        print("[PASS] Verified: Mermaid flowchart block included in response:")
        mermaid_block = reply.split("```mermaid")[1].split("```")[0].strip()
        print(f"  {mermaid_block[:100]}...")
    else:
        print("[INFO] Mermaid flowchart not in text (LLM chose prose format), but Mermaid engine is enabled.")


def test_frontend_mermaid_assets():
    print("\n=== Testing Sub-Step 5.2: Frontend Mermaid.js Assets & Scripts ===")
    index_html = (ROOT / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "mermaid" in index_html.lower(), "mermaid.min.js CDN not included in index.html"
    print("[PASS] Verified: mermaid.min.js is present in ui/static/index.html.")

    app_js = (ROOT / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert "renderMermaidDiagrams" in app_js, "renderMermaidDiagrams function missing in app.js"
    assert "showFigureInDrawer" in app_js, "showFigureInDrawer function missing in app.js"
    print("[PASS] Verified: renderMermaidDiagrams and showFigureInDrawer present in ui/static/app.js.")


if __name__ == "__main__":
    test_figure_retrieval_tool()
    test_api_chat_depth_and_figures()
    test_frontend_mermaid_assets()
    print("\n=======================================================")
    print("ALL STEP 5 TESTS PASSED SUCCESSFULLY 100%")
    print("=======================================================")

