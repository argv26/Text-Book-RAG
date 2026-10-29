"""Integration tests for Session History & Chat Persistence API endpoints in ui/app.py."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient


def run_tests():
    print("=== Running Session API Integration Tests ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_sessions_api.db"
        os.environ["AUTH_DB_PATH"] = str(test_db)

        # Import app after setting AUTH_DB_PATH
        from ui.app import app
        from ui.auth import init_auth_db
        from ui.sessions import init_sessions_db, get_session_messages

        init_auth_db(test_db)
        init_sessions_db(test_db)

        client = TestClient(app)

        # 1. GET /api/sessions unauthenticated -> auto-creates guest and sets cookie
        res = client.get("/api/sessions")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
        assert len(data["sessions"]) == 0
        assert "auth_session" in client.cookies
        print("[OK] /api/sessions auto-creates guest session and sets cookie")

        # 2. POST /api/sessions -> creates new session
        res = client.post("/api/sessions", json={"title": "Custom Consultation"})
        assert res.status_code == 200
        data = res.json()
        assert "session" in data
        sess = data["session"]
        sid = sess["id"]
        assert sess["title"] == "Custom Consultation"
        print("[OK] POST /api/sessions created consultation successfully")

        # 3. GET /api/sessions -> now contains 1 session
        res = client.get("/api/sessions")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["id"] == sid
        print("[OK] GET /api/sessions lists created consultation")

        # 4. GET /api/sessions/{session_id} -> returns session and empty messages
        res = client.get(f"/api/sessions/{sid}")
        assert res.status_code == 200
        data = res.json()
        assert data["session"]["id"] == sid
        assert data["messages"] == []
        assert data["citations"] == []
        print("[OK] GET /api/sessions/{id} returns session details")

        # 5. PATCH /api/sessions/{session_id} -> updates title
        res = client.patch(f"/api/sessions/{sid}", json={"title": "Updated Consultation Title"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "updated"
        assert data["session"]["title"] == "Updated Consultation Title"
        print("[OK] PATCH /api/sessions/{id} updates title")

        # 6. Test /api/chat auto-persists session, generates title, and stores dialogue
        # Mock orchestrator.run_turn so we don't need external LLM API
        mock_response = {
            "reply": "Diabetic Ketoacidosis is characterized by the triad of hyperglycemia, acidosis, and ketosis [1].",
            "citations": [{
                "citation_id": 1,
                "book_slug": "Williams_Endocrinology_15e",
                "section_id": 42,
                "heading_path": "Chapter 31 > DKA",
                "page_start": 623,
                "page_end": 624,
                "chunk_id": "chunk_mock_1",
                "short_citation": "[Williams, p.623]"
            }],
            "tool_calls": [{"tool": "search_chunks", "query": "DKA triad"}],
            "follow_ups": ["What is the initial fluid replacement rate?"]
        }

        with patch("ui.app.orchestrator.run_turn", return_value=mock_response):
            chat_sid = "test_chat_session_456"
            chat_res = client.post("/api/chat", json={
                "session_id": chat_sid,
                "message": "What is the classic clinical triad of Diabetic Ketoacidosis?"
            })
            assert chat_res.status_code == 200
            assert "[1]" in chat_res.json()["reply"]

            # Verify session was auto-created in database and auto-titled
            sess_res = client.get(f"/api/sessions/{chat_sid}")
            assert sess_res.status_code == 200
            sess_data = sess_res.json()
            title = sess_data["session"]["title"]
            assert "Diabetic Ketoacidosis" in title or "triad" in title.lower()
            print(f"[OK] /api/chat auto-created and titled session: '{title}'")

            # Verify messages were saved in database
            assert len(sess_data["messages"]) == 2
            assert sess_data["messages"][0]["role"] == "user"
            assert "What is the classic clinical triad" in sess_data["messages"][0]["content"]
            assert sess_data["messages"][1]["role"] == "assistant"
            assert len(sess_data["messages"][1]["citations"]) == 1
            assert sess_data["messages"][1]["citations"][0]["chunk_id"] == "chunk_mock_1"
            assert len(sess_data["citations"]) == 1
            print("[OK] /api/chat turns, citations, and follow-ups saved to database")

        # 7. GET /api/session/{session_id}/citations -> retrieves citations from DB
        res = client.get(f"/api/session/{chat_sid}/citations")
        assert res.status_code == 200
        cit_data = res.json()
        assert len(cit_data["citations"]) == 1
        assert cit_data["citations"][0]["chunk_id"] == "chunk_mock_1"
        print("[OK] /api/session/{id}/citations returns persisted citations")

        # 8. DELETE /api/sessions/{session_id} -> removes session and cascade messages
        res = client.delete(f"/api/sessions/{chat_sid}")
        assert res.status_code == 200
        assert res.json()["status"] == "deleted"

        # Verify 404 on subsequent get
        res_after = client.get(f"/api/sessions/{chat_sid}")
        assert res_after.status_code == 404
        print("[OK] DELETE /api/sessions/{id} deleted consultation cleanly")

    print("\nALL SESSION API INTEGRATION TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run_tests()

