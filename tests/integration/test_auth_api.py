"""API integration tests for FastAPI authentication endpoints in ui/app.py."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Setup temporary auth DB
temp_dir = Path(tempfile.mkdtemp())
test_auth_db = temp_dir / "test_api_users.db"
os.environ["AUTH_DB_PATH"] = str(test_auth_db)

from fastapi.testclient import TestClient
from ui.app import app

client = TestClient(app)


def test_auth_api():
    print(f"Running Auth API integration tests against {test_auth_db}...")
    try:
        # 1. Check /api/auth/me when unauthenticated
        res = client.get("/api/auth/me")
        assert res.status_code == 200, res.text
        assert res.json() == {"user": None}
        print("PASS: /api/auth/me returns null when unauthenticated.")

        # 2. Register user /api/auth/signup
        res = client.post("/api/auth/signup", json={"username": "dr_watson", "password": "sherlock123"})
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["user"]["username"] == "dr_watson"
        assert data["user"]["is_guest"] is False
        assert "token" in data
        assert "auth_session" in res.cookies
        print("PASS: /api/auth/signup registered new user and set cookie.")

        # 3. Check /api/auth/me after signup
        res = client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json()["user"]["username"] == "dr_watson"
        print("PASS: /api/auth/me correctly identifies user from cookie.")

        # 4. Duplicate registration -> 409 Conflict
        res_dup = client.post("/api/auth/signup", json={"username": "DR_WATSON", "password": "differentpass"})
        assert res_dup.status_code == 409, f"Expected 409, got {res_dup.status_code}"
        assert "already taken" in res_dup.json()["detail"].lower()
        print(f"PASS: Duplicate registration rejected with 409: '{res_dup.json()['detail']}'")

        # 5. Logout
        res_logout = client.post("/api/auth/logout")
        assert res_logout.status_code == 200
        res_me = client.get("/api/auth/me")
        assert res_me.json() == {"user": None}
        print("PASS: /api/auth/logout clears active session.")

        # 6. Login with wrong password -> 401 Unauthorized
        res_bad = client.post("/api/auth/login", json={"username": "dr_watson", "password": "wrongpassword"})
        assert res_bad.status_code == 401
        print("PASS: /api/auth/login rejects incorrect credentials with 401.")

        # 7. Login with correct password -> 200 OK
        res_login = client.post("/api/auth/login", json={"username": "dr_watson", "password": "sherlock123"})
        assert res_login.status_code == 200
        assert res_login.json()["user"]["username"] == "dr_watson"
        print("PASS: /api/auth/login succeeded with 200.")

        # 8. Quota endpoint reflects user
        res_quota = client.get("/api/quota")
        assert res_quota.status_code == 200
        quota_data = res_quota.json()
        assert quota_data["limit"] == 50
        assert quota_data["remaining"] == 50
        print("PASS: /api/quota runs smoothly for authenticated user.")

        # 9. 1-Click Guest Creation -> 200 OK
        # Clear client cookies first
        client.cookies.clear()
        res_guest = client.post("/api/auth/guest")
        assert res_guest.status_code == 200
        guest_data = res_guest.json()
        assert guest_data["user"]["is_guest"] is True
        assert guest_data["user"]["username"].startswith("Guest_")
        assert "token" in guest_data
        print(f"PASS: /api/auth/guest created guest session '{guest_data['user']['username']}'.")

        # Verify /api/auth/me as guest
        res_me_guest = client.get("/api/auth/me")
        assert res_me_guest.status_code == 200
        assert res_me_guest.json()["user"]["is_guest"] is True
        print("PASS: /api/auth/me identifies guest session.")

        print("\nALL AUTH API INTEGRATION TESTS PASSED SUCCESSFULLY!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_api_tests()

