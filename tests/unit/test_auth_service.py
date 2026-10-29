"""Unit test verification for ui/auth.py authentication service."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.auth import (
    authenticate_user,
    create_guest_user,
    create_session,
    delete_session,
    get_user_by_token,
    init_auth_db,
    register_user,
)


def test_auth_service():
    temp_dir = Path(tempfile.mkdtemp())
    test_db = temp_dir / "test_users.db"
    print(f"Running auth service tests against {test_db}...")

    try:
        # 1. DB Init
        init_auth_db(test_db)
        assert test_db.exists(), "DB file was not created"
        print("PASS: init_auth_db created database file.")

        # 2. User Registration
        user1, err = register_user("dr_house", "vicodin123", db_path=test_db)
        assert err is None, f"Registration failed: {err}"
        assert user1 is not None
        assert user1["username"] == "dr_house"
        assert user1["is_guest"] is False
        assert user1["id"].startswith("usr_")
        print("PASS: register_user successfully created user.")

        # 3. Duplicate User Rejection (Case-Insensitive)
        user_dup, err_dup = register_user("DR_HOUSE", "anotherpass", db_path=test_db)
        assert user_dup is None
        assert "already taken" in err_dup.lower(), f"Expected duplicate error, got {err_dup}"
        print(f"PASS: Duplicate username correctly rejected: '{err_dup}'")

        # 4. Short username / password validation
        _, err_short_u = register_user("ab", "pass123", db_path=test_db)
        assert err_short_u is not None and "3 characters" in err_short_u
        _, err_short_p = register_user("doctor", "12", db_path=test_db)
        assert err_short_p is not None and "4 characters" in err_short_p
        print("PASS: Input length constraints properly enforced.")

        # 5. User Authentication
        auth_ok, auth_err = authenticate_user("dr_house", "vicodin123", db_path=test_db)
        assert auth_err is None
        assert auth_ok["username"] == "dr_house"
        print("PASS: authenticate_user succeeded with correct password.")

        auth_bad, auth_err2 = authenticate_user("dr_house", "wrongpass", db_path=test_db)
        assert auth_bad is None
        assert "invalid" in auth_err2.lower()
        print("PASS: authenticate_user rejected incorrect password.")

        # 6. Guest User Creation
        guest = create_guest_user(db_path=test_db)
        assert guest["is_guest"] is True
        assert guest["id"].startswith("guest_")
        assert guest["username"].startswith("Guest_")
        print(f"PASS: create_guest_user created guest '{guest['username']}'.")

        # 7. Session Creation and Resolution
        token = create_session(user1["id"], days=7, db_path=test_db)
        assert len(token) == 64, "Token should be 32-byte hex (64 chars)"
        resolved_user = get_user_by_token(token, db_path=test_db)
        assert resolved_user is not None
        assert resolved_user["id"] == user1["id"]
        assert resolved_user["username"] == "dr_house"
        print("PASS: create_session and get_user_by_token resolved correct user.")

        # 8. Guest Session
        guest_token = create_session(guest["id"], days=1, db_path=test_db)
        resolved_guest = get_user_by_token(guest_token, db_path=test_db)
        assert resolved_guest is not None
        assert resolved_guest["is_guest"] is True
        print("PASS: Guest session verified.")

        # 9. Session Deletion (Logout)
        deleted = delete_session(token, db_path=test_db)
        assert deleted is True
        assert get_user_by_token(token, db_path=test_db) is None
        print("PASS: delete_session invalidated session token.")

        print("\nALL AUTH SERVICE TESTS PASSED SUCCESSFULLY!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_tests()
