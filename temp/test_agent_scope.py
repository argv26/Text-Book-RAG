"""Automated integration test for Sub-Step 3.2: Agent Orchestrator & API Integration."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.orchestrator import AgentOrchestrator
from fastapi.testclient import TestClient
from ui.app import app

ENDOCRINOLOGY = "Williams_Textbook_of_Endocrinology_15th_edition_2"
ANATOMY = "anatomy-and-physiology-2e_-_WEB"


def test_agent_orchestrator_scope():
    print("=================================================================", flush=True)
    print("Testing Agent Orchestrator Scope Enforcement", flush=True)
    print("=================================================================", flush=True)

    orchestrator = AgentOrchestrator()
    # Test offline synthesizer explicitly first for deterministic verification
    res_endo = orchestrator._run_offline_synthesizer_turn(
        session_id="test_turn_endo",
        user_message="Explain thyroid hormone regulation and anatomy.",
        active_books=[ENDOCRINOLOGY],
    )
    citations_endo = res_endo.get("citations", [])
    print(f"[INFO] Endocrinology-only query returned {len(citations_endo)} citations.", flush=True)
    for c in citations_endo:
        assert c["book_slug"] == ENDOCRINOLOGY, f"Leaked book: {c['book_slug']} when restricted to {ENDOCRINOLOGY}"
    print("[PASS] Agent turn with Endocrinology-only scope returns 0 Anatomy citations.", flush=True)

    # Test 2: Restrict agent turn to Anatomy only
    res_anat = orchestrator._run_offline_synthesizer_turn(
        session_id="test_turn_anat",
        user_message="Explain thyroid hormone regulation and anatomy.",
        active_books=[ANATOMY],
    )
    citations_anat = res_anat.get("citations", [])
    print(f"[INFO] Anatomy-only query returned {len(citations_anat)} citations.", flush=True)
    for c in citations_anat:
        assert c["book_slug"] == ANATOMY, f"Leaked book: {c['book_slug']} when restricted to {ANATOMY}"
    print("[PASS] Agent turn with Anatomy-only scope returns 0 Endocrinology citations.", flush=True)


def test_api_chat_scope():
    print("=================================================================", flush=True)
    print("Testing /api/chat Scope Enforcement via HTTP TestClient", flush=True)
    print("=================================================================", flush=True)

    from ui.app import orchestrator
    # Temporarily set API keys to None for fast, deterministic unit test execution of /api/chat
    saved_key = orchestrator.gemini_api_key
    orchestrator.gemini_api_key = None

    import uuid
    s_endo = f"api_test_endo_{uuid.uuid4().hex[:6]}"
    s_anat = f"api_test_anat_{uuid.uuid4().hex[:6]}"

    try:
        client = TestClient(app)

        # Call /api/chat with active_books = [ENDOCRINOLOGY]
        payload = {
            "session_id": s_endo,
            "message": "What is the function of the adrenal cortex?",
            "active_books": [ENDOCRINOLOGY],
        }
        resp = client.post("/api/chat", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        citations = data.get("citations", [])
        print(f"[INFO] /api/chat returned {len(citations)} citations for Endocrinology scope.", flush=True)
        for c in citations:
            assert c["book_slug"] == ENDOCRINOLOGY, f"Leaked citation from {c['book_slug']}"
        print("[PASS] /api/chat correctly enforced Endocrinology active_books filter.", flush=True)

        # Call /api/chat with active_books = [ANATOMY]
        payload_anat = {
            "session_id": s_anat,
            "message": "What are the boundaries of the femoral triangle?",
            "active_books": [ANATOMY],
        }
        resp_anat = client.post("/api/chat", json=payload_anat)
        assert resp_anat.status_code == 200, f"Expected 200, got {resp_anat.status_code}: {resp_anat.text}"
        data_anat = resp_anat.json()
        citations_anat = data_anat.get("citations", [])
        print(f"[INFO] /api/chat returned {len(citations_anat)} citations for Anatomy scope.", flush=True)
        for c in citations_anat:
            assert c["book_slug"] == ANATOMY, f"Leaked citation from {c['book_slug']}"
        print("[PASS] /api/chat correctly enforced Anatomy active_books filter.", flush=True)
    finally:
        orchestrator.gemini_api_key = saved_key

    print("\nALL AGENT & API SCOPE INTEGRATION CHECKS PASSED SUCCESSFULLY!", flush=True)


if __name__ == "__main__":
    test_agent_orchestrator_scope()
    test_api_chat_scope()
