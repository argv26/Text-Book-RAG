"""End-to-end automated verification for Step 4 Typography & Citation Hardening."""
import os
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from ui.app import app
from ui.auth import init_auth_db
from ui.sessions import init_sessions_db
from agent.orchestrator import _sanitize_medical_typography, _sanitize_citations, _clean_chunk_prose


def test_typography_and_citations_pipeline():
    init_auth_db()
    init_sessions_db()
    client = TestClient(app)

    print("=== Step 4 Verification: Medical Typography & Citations ===")

    # 1. Test offline synthesizer / prompt output via /api/chat
    import uuid
    session_id = f"test_step4_{uuid.uuid4().hex[:8]}"
    res = client.post("/api/chat", json={
        "session_id": session_id,
        "message": "What is the biochemical diagnostic criteria for DKA and thyroid regulation?",
    })

    assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"
    data = res.json()
    reply = data.get("reply", "")
    citations = data.get("citations", [])

    print(f"[PASS] Chat turn succeeded. Reply length: {len(reply)} chars. Citations count: {len(citations)}")

    # 2. Check for LaTeX math leaks
    assert r"\text" not in reply, f"Raw LaTeX \\text found in reply: {reply}"
    assert "$T" not in reply, f"Raw hormone math delimiter found in reply: {reply}"
    assert r"\sim" not in reply, f"Raw LaTeX \\sim found in reply: {reply}"
    assert r"\ge" not in reply, f"Raw LaTeX \\ge found in reply: {reply}"
    print("[PASS] Verified: Zero raw LaTeX tags (\\text, $T, \\ge, \\sim) in chat output.")

    # 3. Check citation IDs in reply vs citations array
    import re
    pill_ids = [int(m) for m in re.findall(r"\[(\d+)\]", reply)]
    registered_ids = [c["citation_id"] for c in citations]

    for pid in pill_ids:
        assert pid in registered_ids, f"Out-of-range citation [{pid}] found in reply! Registered: {registered_ids}"
    print(f"[PASS] Verified: 100% of citation pills {pill_ids} map to cached citation records {registered_ids}.")

    # 4. Test /api/chunk de-hyphenation if chunks exist
    if citations:
        first_chunk_id = citations[0]["chunk_id"]
        c_res = client.get(f"/api/chunk/{first_chunk_id}")
        assert c_res.status_code == 200, f"Chunk lookup failed: {c_res.text}"
        chunk_data = c_res.json()
        assert "text" in chunk_data
        # Ensure no hyphenated newline wrap exists
        assert not re.search(r"\b\w+-\s*\n\s*\w+\b", chunk_data["text"]), "Hyphenated line break found in chunk text"
        print(f"[PASS] Verified: Chunk '{first_chunk_id}' fetched and de-hyphenated cleanly.")

    print("\n=======================================================")
    print("STEP 4 AUTOMATED VERIFICATION SUITE PASSED SUCCESSFULLY")
    print("=======================================================")


if __name__ == "__main__":
    test_typography_and_citations_pipeline()

