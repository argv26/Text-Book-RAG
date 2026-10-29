"""End-to-end integration test verifying multi-turn consultation switching and context hydration."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient


def run_tests():
    print("=== Running Session E2E Multi-Turn Switching Tests ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_sessions_e2e.db"
        os.environ["AUTH_DB_PATH"] = str(test_db)

        from ui.app import app, orchestrator
        from ui.auth import init_auth_db, register_user, create_session
        from ui.sessions import init_sessions_db

        init_auth_db(test_db)
        init_sessions_db(test_db)

        # Register doctor
        user, _ = register_user("dr_govind", "pass1234", db_path=test_db)
        token = create_session(user["id"], db_path=test_db)

        client = TestClient(app)
        client.cookies.set("auth_session", token)

        # Mock orchestrator turn 1
        resp_turn1 = {
            "reply": "Diabetic Ketoacidosis involves hyperglycemia, acidosis, and ketosis [1].",
            "citations": [{
                "citation_id": 1,
                "book_slug": "Williams_Endocrinology_15e",
                "section_id": 10,
                "heading_path": "DKA",
                "page_start": 623,
                "page_end": 624,
                "chunk_id": "chunk_dka",
                "short_citation": "[Williams, p.623]"
            }],
            "tool_calls": [],
            "follow_ups": []
        }

        # Mock orchestrator turn 2 (different topic)
        resp_turn2 = {
            "reply": "The femoral triangle contains the femoral nerve, artery, vein, and canal [2].",
            "citations": [{
                "citation_id": 2,
                "book_slug": "Anatomy_and_Physiology_2e",
                "section_id": 25,
                "heading_path": "Lower Limb Anatomy",
                "page_start": 412,
                "page_end": 413,
                "chunk_id": "chunk_femoral",
                "short_citation": "[A&P 2e, p.412]"
            }],
            "tool_calls": [],
            "follow_ups": []
        }

        # Step 1: User asks about DKA in session_1
        sid1 = "session_dka_1"
        with patch.object(orchestrator, "run_turn", return_value=resp_turn1):
            r = client.post("/api/chat", json={
                "session_id": sid1,
                "message": "What is the classic triad of Diabetic Ketoacidosis?"
            })
            assert r.status_code == 200

        # Step 2: User starts a new consultation for Femoral Triangle in session_2
        sid2 = "session_femoral_2"
        with patch.object(orchestrator, "run_turn", return_value=resp_turn2):
            r = client.post("/api/chat", json={
                "session_id": sid2,
                "message": "What are the contents of the femoral triangle?"
            })
            assert r.status_code == 200

        # Step 3: Check that sidebar lists both consultations
        res_list = client.get("/api/sessions")
        assert res_list.status_code == 200
        sessions = res_list.json()["sessions"]
        assert len(sessions) == 2
        # Latest updated is session_femoral_2
        assert sessions[0]["id"] == sid2
        assert sessions[1]["id"] == sid1
        print("[OK] Both consultations present and sorted in updated_at DESC order")

        # Step 4: Simulate restarting backend / clearing orchestrator memory
        orchestrator.context_manager.store.delete_session(sid1)
        assert orchestrator.context_manager.store.get_session(sid1) is None

        # Step 5: User switches back to session_1 (DKA) and asks a follow-up question
        resp_turn1_followup = {
            "reply": "Fluid replacement in DKA typically begins with normal saline at 1000 mL/hr [3].",
            "citations": [{
                "citation_id": 3,
                "book_slug": "Williams_Endocrinology_15e",
                "section_id": 11,
                "heading_path": "DKA Protocol",
                "page_start": 625,
                "page_end": 626,
                "chunk_id": "chunk_saline",
                "short_citation": "[Williams, p.625]"
            }],
            "tool_calls": [],
            "follow_ups": []
        }

        with patch.object(orchestrator, "run_turn", return_value=resp_turn1_followup) as mock_turn:
            r = client.post("/api/chat", json={
                "session_id": sid1,
                "message": "What is the initial fluid replacement rate?"
            })
            assert r.status_code == 200

        # Verify that memory was re-hydrated from database before the turn was run
        loaded_sess = orchestrator.context_manager.get_or_create_session(sid1)
        assert len(loaded_sess.messages) >= 2 # previous turn (user+asst) hydrated into memory
        assert loaded_sess.messages[0].role == "user"
        assert loaded_sess.messages[1].role == "assistant"
        print("[OK] Memory context hydration successfully restored multi-turn dialogue from database")

        # Step 6: Verify full consultation dialogue in GET /api/sessions/{session_id}
        res_dka = client.get(f"/api/sessions/{sid1}")
        assert res_dka.status_code == 200
        dka_data = res_dka.json()
        assert len(dka_data["messages"]) == 4 # 2 user questions + 2 assistant answers
        assert len(dka_data["citations"]) == 2 # citation 1 and citation 3
        print("[OK] Full dialogue and accumulated citations retrieved intact")

    print("\nALL SESSION E2E SWITCHING TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_tests()
