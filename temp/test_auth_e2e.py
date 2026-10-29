"""End-to-end verification of static index serving, auth elements in HTML, and auth workflow."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

temp_dir = Path(tempfile.mkdtemp())
test_auth_db = temp_dir / "e2e_users.db"
os.environ["AUTH_DB_PATH"] = str(test_auth_db)

from fastapi.testclient import TestClient
from ui.app import app

client = TestClient(app)


def test_html_contains_auth_elements():
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    # Verify key UI components exist in index.html
    assert 'id="auth-modal"' in html, "auth-modal missing"
    assert 'id="btn-guest-login"' in html, "btn-guest-login missing"
    assert 'id="tab-login"' in html, "tab-login missing"
    assert 'id="tab-signup"' in html, "tab-signup missing"
    assert 'id="btn-auth-trigger"' in html, "btn-auth-trigger missing"
    assert 'id="auth-user-label"' in html, "auth-user-label missing"
    assert 'id="auth-error-banner"' in html, "auth-error-banner missing"
    print("PASS: index.html contains all required authentication UI components.")


def test_static_app_js_served():
    res = client.get("/static/app.js")
    assert res.status_code == 200
    js = res.text
    assert "checkAuthStatus" in js, "checkAuthStatus missing in app.js"
    assert "handleGuestLogin" in js, "handleGuestLogin missing in app.js"
    assert "handleAuthSubmit" in js, "handleAuthSubmit missing in app.js"
    assert "switchAuthTab" in js, "switchAuthTab missing in app.js"
    print("PASS: app.js contains all frontend authentication logic and is served properly.")


def test_complete_user_lifecycle():
    # 1. Start fresh
    client.cookies.clear()
    res = client.get("/api/auth/me")
    assert res.json()["user"] is None

    # 2. 1-Click Guest
    res_guest = client.post("/api/auth/guest")
    assert res_guest.status_code == 200
    guest_username = res_guest.json()["user"]["username"]
    assert guest_username.startswith("Guest_")

    # Verify session cookie was set
    assert "auth_session" in client.cookies
    res_me = client.get("/api/auth/me")
    assert res_me.json()["user"]["username"] == guest_username

    # 3. Register a permanent doctor account
    res_signup = client.post("/api/auth/signup", json={"username": "dr_curie", "password": "radium1898"})
    assert res_signup.status_code == 201
    assert res_signup.json()["user"]["username"] == "dr_curie"

    # 4. Check me returns dr_curie
    res_me2 = client.get("/api/auth/me")
    assert res_me2.json()["user"]["username"] == "dr_curie"

    # 5. Check duplicate signup rejection
    res_dup = client.post("/api/auth/signup", json={"username": "DR_CURIE", "password": "anypassword"})
    assert res_dup.status_code == 409
    assert "already taken" in res_dup.json()["detail"].lower()

    # 6. Logout
    res_out = client.post("/api/auth/logout")
    assert res_out.status_code == 200

    # 7. Log in again
    res_in = client.post("/api/auth/login", json={"username": "dr_curie", "password": "radium1898"})
    assert res_in.status_code == 200
    assert res_in.json()["user"]["username"] == "dr_curie"

    print("PASS: Full user lifecycle verified across guest, signup, duplicate rejection, and login.")


if __name__ == "__main__":
    try:
        test_html_contains_auth_elements()
        test_static_app_js_served()
        test_complete_user_lifecycle()
        print("\nALL E2E VERIFICATION CHECKS PASSED!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

