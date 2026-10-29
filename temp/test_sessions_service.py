"""Unit tests for ui/sessions.py session persistence and smart titling."""
import os
import sys
import tempfile
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ui.auth import init_auth_db, register_user, create_guest_user
from ui.sessions import (
    init_sessions_db,
    create_session,
    list_user_sessions,
    get_session,
    get_session_messages,
    save_message,
    update_session_title,
    delete_session,
    generate_smart_title,
)


def run_tests():
    print("=== Running ui/sessions.py Service Unit Tests ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = Path(tmpdir) / "test_sessions.db"

        # 1. Initialize schema
        init_auth_db(test_db)
        init_sessions_db(test_db)
        print("[OK] Database and tables initialized cleanly")

        # 2. Test Smart Auto-Titling
        t1 = generate_smart_title("What is the classic clinical triad of Diabetic Ketoacidosis?")
        assert "Diabetic Ketoacidosis" in t1 or "triad" in t1.lower(), f"Failed title: {t1}"
        assert not t1.lower().startswith("what is"), f"Question words not stripped: {t1}"

        t2 = generate_smart_title("How do you describe the femoral triangle anatomy?")
        assert "Femoral Triangle" in t2, f"Failed title: {t2}"
        assert not t2.lower().startswith("how do"), f"Interrogative not stripped: {t2}"

        t3 = generate_smart_title("   ")
        assert t3 == "New Consultation", f"Failed empty fallback: {t3}"
        print(f"[OK] Smart titling verified: '{t1}' | '{t2}'")

        # 3. Create users
        user1, _ = register_user("dr_house", "vicodin123", db_path=test_db)
        user2 = create_guest_user(db_path=test_db)
        assert user1 is not None and user2 is not None

        # 4. Create consultation sessions
        s1 = create_session(user1["id"], title="DKA Clinical Management", db_path=test_db)
        s2 = create_session(user1["id"], title="Adrenal Insufficiency", db_path=test_db)
        s3 = create_session(user2["id"], title="Guest Session 1", db_path=test_db)

        assert s1["id"].startswith("ses_")
        assert s1["title"] == "DKA Clinical Management"
        print("[OK] Sessions created successfully")

        # 5. List sessions and check sorting & message count
        sessions_u1 = list_user_sessions(user1["id"], db_path=test_db)
        assert len(sessions_u1) == 2, f"Expected 2 sessions, got {len(sessions_u1)}"
        assert sessions_u1[0]["title"] == "Adrenal Insufficiency" # Latest created first

        sessions_u2 = list_user_sessions(user2["id"], db_path=test_db)
        assert len(sessions_u2) == 1
        assert sessions_u2[0]["title"] == "Guest Session 1"
        print("[OK] Listing user sessions correctly isolated by user_id")

        # 6. User Isolation: User 2 cannot access User 1's session
        assert get_session(s1["id"], user1["id"], db_path=test_db) is not None
        assert get_session(s1["id"], user2["id"], db_path=test_db) is None
        print("[OK] User isolation verified for session metadata")

        # 7. Add messages with citations, tool calls, and follow-ups
        cit_sample = [{
            "citation_id": 1,
            "book_slug": "Williams_Endocrinology_15e",
            "section_id": 105,
            "heading_path": "Chapter 31 > Diabetic Ketoacidosis",
            "page_start": 623,
            "page_end": 624,
            "chunk_id": "chunk_dka_1",
            "short_citation": "[Williams, pp.623-624]"
        }]
        tool_sample = [{
            "tool": "search_chunks",
            "query": "DKA triad hyperglycaemia acidosis ketosis",
            "results_count": 3
        }]
        follow_sample = ["What are the electrolyte replacement guidelines?"]

        msg_u = save_message(s1["id"], "user", "What is the classic triad of DKA?", db_path=test_db)
        msg_a = save_message(
            s1["id"],
            "assistant",
            "The classic triad consists of hyperglycemia, ketosis, and metabolic acidosis [1].",
            citations=cit_sample,
            tool_calls=tool_sample,
            follow_ups=follow_sample,
            db_path=test_db,
        )

        assert msg_u["id"].startswith("msg_")
        assert msg_a["citations"][0]["citation_id"] == 1
        print("[OK] Messages saved successfully")

        # 8. Retrieve messages
        msgs = get_session_messages(s1["id"], user1["id"], db_path=test_db)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert len(msgs[1]["citations"]) == 1
        assert msgs[1]["citations"][0]["chunk_id"] == "chunk_dka_1"
        assert msgs[1]["follow_ups"] == follow_sample
        print("[OK] Messages and serialized citations retrieved intact")

        # User 2 cannot retrieve User 1's messages
        assert get_session_messages(s1["id"], user2["id"], db_path=test_db) is None
        print("[OK] User isolation verified for message history")

        # 9. Update Title
        renamed = update_session_title(s1["id"], user1["id"], "Diabetic Ketoacidosis Protocol", db_path=test_db)
        assert renamed is True
        s1_updated = get_session(s1["id"], user1["id"], db_path=test_db)
        assert s1_updated["title"] == "Diabetic Ketoacidosis Protocol"
        print("[OK] Session title updated")

        # 10. Cascade Delete
        deleted = delete_session(s1["id"], user1["id"], db_path=test_db)
        assert deleted is True
        assert get_session(s1["id"], user1["id"], db_path=test_db) is None
        assert get_session_messages(s1["id"], user1["id"], db_path=test_db) is None

        # Verify messages table is cleaned up via foreign key cascade
        with test_db_conn(test_db) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM consultation_messages WHERE session_id = ?", (s1["id"],))
            assert cur.fetchone()[0] == 0, "Messages were not cascade-deleted"
        print("[OK] Session deletion with cascade verified")

    print("\nALL 10 SESSION SERVICE TESTS PASSED SUCCESSFULLY!")


def test_db_conn(db_path):
    import sqlite3
    from contextlib import contextmanager
    @contextmanager
    def _c():
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
        finally:
            conn.close()
    return _c()


if __name__ == "__main__":
    run_tests()
