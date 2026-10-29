# Implementation Plan — Step 1: User Authentication & Guest Sessions

**Step Focus**: Lightweight Multi-User Identity, Simple Registration/Login, and 1-Click Guest Mode  
**Parent Document**: [`docs/phase-2-requirements.md`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/docs/phase-2-requirements.md)  
**Status**: Fully Implemented & Verified (Tests Passing 100%)  

---

## 1. Problem Statement & Scope

In Phase 1, Text-Book-RAG operated without user identity:
- All users shared single IP-based rate limiting.
- Clinicians, evaluators, and test users had no way to distinguish individual query histories or identities.
- However, clinicians cannot be burdened with complicated OAuth, email confirmations, or multi-factor authentication for testing.

**Requirements**:
1. **Simple Sign-Up & Login**: `username` and `password` with immediate validation and clear warning if a username is already taken.
2. **1-Click Guest Mode**: Zero-friction button to test immediately as a guest (`guest_xxxxxx`) without registration.
3. **Decoupled User DB**: User accounts and sessions stored in `app_users.db`, completely isolated from `textbook_rag.db` (so re-indexing books never drops users).
4. **Session Persistence**: Sessions maintained via secure HTTP cookies and local storage tokens.
5. **Rate-Limiter Integration**: Quota tracked per user identity instead of shared IP collisions.

---

## 2. Technical Architecture & Data Model

### 2.1 Database Schema (`app_users.db`)
Located at project root `app_users.db` (managed by `ui/auth.py`):

```sql
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,               -- 'usr_' + uuid4 hex (or 'guest_' + uuid4 hex)
    username TEXT UNIQUE NOT NULL,      -- unique username, case-insensitive index
    password_hash TEXT,                 -- PBKDF2-HMAC-SHA256 hash (NULL for guests)
    salt TEXT,                          -- 16-byte random salt in hex (NULL for guests)
    is_guest INTEGER NOT NULL DEFAULT 0,-- 1 for ephemeral guest, 0 for registered user
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_lower ON users(LOWER(username));

CREATE TABLE IF NOT EXISTS auth_sessions (
    token TEXT PRIMARY KEY,             -- 32-byte cryptographically secure hex
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON auth_sessions(user_id);
```

### 2.2 Password Security & Cryptography
- Salt: `secrets.token_hex(16)` (16 bytes random hex).
- Hash: `hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100_000).hex()`.
- Verification: `hmac.compare_digest(stored_hash, computed_hash)` to protect against timing attacks.
- Zero extra external C-extensions or compiled dependencies (fully native Python standard library).

### 2.3 Session Management & Token Resolution
- Tokens generated with `secrets.token_hex(32)` with 30-day default expiry.
- Cookie: `auth_session=<token>; Path=/; HttpOnly; SameSite=Lax`.
- Dual resolution: Incoming requests resolve active user via:
  1. `request.cookies.get("auth_session")`
  2. Fallback: `Authorization: Bearer <token>` header (simplifies programmatic testing and curl).

---

## 3. Detailed Sub-Step Breakdown

### Sub-Step 1.1: Authentication Core Engine (`ui/auth.py`)

- **Objective**: Create dedicated auth service handling database connection, user registration, validation, login verification, guest generation, and session lifecycle.
- **Key Methods**:
  - `init_auth_db(db_path: Path | str | None)`: Ensures schema and tables exist.
  - `hash_password(password: str, salt: str | None = None) -> tuple[str, str]`: Generates (salt, hash).
  - `verify_password(password: str, salt: str, hash_val: str) -> bool`: Constant-time check.
  - `register_user(username: str, password: str) -> tuple[dict | None, str | None]`:
    - Checks length (username >= 3, password >= 4).
    - Checks if username exists case-insensitively. If taken, returns `(None, "Username already taken. Please choose another.")`.
    - Inserts new user record and returns user dict.
  - `authenticate_user(username: str, password: str) -> tuple[dict | None, str | None]`:
    - Looks up username, validates hash. Returns `(user_dict, None)` or `(None, "Invalid username or password.")`.
  - `create_guest_user() -> dict`:
    - Creates random guest account `guest_<hex>` with `is_guest=1`.
  - `create_session(user_id: str, days: int = 30) -> str`:
    - Creates token in `auth_sessions`, returns token.
  - `get_user_by_token(token: str) -> dict | None`:
    - Retrieves user if session exists and not expired.
  - `delete_session(token: str) -> bool`:
    - Removes session on logout.

- **Acceptance Criteria**:
  - Case-insensitive duplicate username registration returns explicit rejection error.
  - Guest accounts can be created instantly without passwords.
  - Passwords are validated using timing-safe comparisons.
  - Expired or non-existent tokens cleanly return `None`.

---

### Sub-Step 1.2: FastAPI API Endpoints & Request Context (`ui/app.py`)

- **Objective**: Wire authentication endpoints into FastAPI and integrate user identity into rate limiting.
- **Endpoints to Implement**:
  - `POST /api/auth/signup`:
    - Request: `{"username": "...", "password": "..."}`
    - Response 201: `{"user": {"id": "...", "username": "...", "is_guest": false}, "token": "..."}` + `Set-Cookie`.
    - Response 409: `{"detail": "Username already taken. Please choose another."}`
    - Response 400: `{"detail": "Username must be at least 3 characters."}`
  - `POST /api/auth/login`:
    - Request: `{"username": "...", "password": "..."}`
    - Response 200: `{"user": {...}, "token": "..."}` + `Set-Cookie`.
    - Response 401: `{"detail": "Invalid username or password."}`
  - `POST /api/auth/guest`:
    - Request: None
    - Response 200: `{"user": {"id": "guest_...", "username": "Guest_...", "is_guest": true}, "token": "..."}` + `Set-Cookie`.
  - `GET /api/auth/me`:
    - Reads session cookie/header.
    - Response 200: `{"user": {...}}` or `{"user": null}`.
  - `POST /api/auth/logout`:
    - Deletes session token, deletes cookie.
    - Response 200: `{"status": "logged_out"}`.
- **Integration with Rate Limiter & Chat**:
  - If user is authenticated (registered or guest), `client_id` for rate limiting becomes `user:{user_id}`, guaranteeing isolated quota tracking per doctor/evaluator.

- **Acceptance Criteria**:
  - All 5 endpoints respond with correct HTTP status codes (201, 200, 400, 401, 409).
  - Rate limiting tracks user ID when signed in.

---

### Sub-Step 1.3: Web UI Auth Modal & Profile Header (`ui/static/index.html` & `ui/static/app.js`)

- **Objective**: Create seamless user interface components for authentication:
  1. **Top Navigation Auth Pill**:
     - Unauthenticated: `[ 👤 Sign In / Guest ]` (clicking opens modal).
     - Guest: `[ 👤 Guest (guest_ab12) ]` with a subtle `Create Account` link.
     - Registered Doctor: `[ 👨‍⚕️ Dr. {username} ]` with a `Log Out` button.
  2. **Auth Modal**:
     - Tabbed design: **"Log In"** vs **"Sign Up"**.
     - Inputs: Username and Password.
     - Error banner displaying backend error messages (e.g., *"Username already taken. Please choose another."*).
     - Prominent primary 1-click button: **"⚡ Continue as Guest (No Account Required)"**.
     - Modal auto-displays if visitor has no session, or can be opened manually anytime.
  3. **Client-side Session Handling**:
     - `app.js` checks `GET /api/auth/me` on initial page load.
     - If authenticated, renders user badge and updates quota display.
     - If not authenticated, displays modal with 1-click guest option.

- **Acceptance Criteria**:
  - 1-click guest button immediately closes modal and allows query execution without reloading.
  - Sign-up with duplicate username renders inline error message in red banner without refreshing.
  - Logging out returns to unauthenticated / guest prompt.

---

## 4. Verification Protocol & Acceptance Test Plan

### 4.1 Automated Test Suites (Run via `temp/`)

1. **Service Layer Unit Tests (`temp/test_auth_service.py`)**:
   - Test table initialization in a temporary SQLite database.
   - Test `register_user` with valid credentials -> success.
   - Test `register_user` with duplicate username -> returns `Username already taken`.
   - Test `authenticate_user` with correct password -> returns user dict.
   - Test `authenticate_user` with wrong password -> returns `Invalid username or password`.
   - Test `create_guest_user` -> generates unique guest user with `is_guest=1`.
   - Test `create_session` and `get_user_by_token` -> returns user.
   - Test `delete_session` -> token no longer resolves.

2. **FastAPI Integration Tests (`temp/test_auth_api.py`)**:
   - Use FastAPI `TestClient` to test all HTTP endpoints:
     - `POST /api/auth/signup` -> 201 + cookie check.
     - `POST /api/auth/signup` duplicate -> 409 Conflict with detail message.
     - `POST /api/auth/login` valid -> 200 OK.
     - `POST /api/auth/login` bad pass -> 401 Unauthorized.
     - `POST /api/auth/guest` -> 200 OK + `guest_` prefix in username.
     - `GET /api/auth/me` with cookie -> returns current user.
     - `POST /api/auth/logout` -> clears session.

### 4.2 Human-Verifiable Browser Testing Steps

| Step | Action | Expected Visual & Functional Result |
| :--- | :--- | :--- |
| **H1** | Open `http://127.0.0.1:8000/` in an incognito window. | Modal appears welcoming user with Log In / Sign Up tabs and prominent "Continue as Guest" button. |
| **H2** | Click **"⚡ Continue as Guest"**. | Modal immediately closes. Top navbar shows `👤 Guest (guest_xxxx)` badge. Daily quota shows `50/50 left`. |
| **H3** | In top navbar, click **"Sign In / Create Account"**. Select **"Sign Up"** tab. Enter username `dr_house`, password `vicodin123`. Click **"Create Account"**. | Account is created. Navbar updates to `👨‍⚕️ Dr. dr_house`. Modal closes. |
| **H4** | Click **"Log Out"**. Re-open modal, select **"Sign Up"**, enter username `dr_house` again with any password. Click **"Create Account"**. | Form does not reload; red error message appears: *"Username already taken. Please choose another."* |
| **H5** | Select **"Log In"** tab. Enter `dr_house` and wrong password `wrongpass`. | Red error message appears: *"Invalid username or password."* |
| **H6** | Enter correct password `vicodin123` and log in. Refresh the page (F5). | Page reloads still signed in as `👨‍⚕️ Dr. dr_house` without re-prompting modal. |

---

## 5. Incremental Execution Prompts

### Prompt for Step 1 Implementation:
```text
TASK: Implement Step 1 (User Authentication & Guest Sessions) as defined in docs/step-1-auth-plan.md.
EXECUTION SCOPE:
1. Create `ui/auth.py` implementing SQLite `app_users.db`, password hashing (PBKDF2-HMAC-SHA256), session tokens, and guest mode.
2. Add auth endpoints in `ui/app.py`: `/api/auth/signup`, `/api/auth/login`, `/api/auth/guest`, `/api/auth/me`, `/api/auth/logout`, and bind user ID to rate limiting.
3. Update `ui/static/index.html` and `ui/static/app.js` with the Auth Modal and user status badge.
4. Create test suite in `temp/test_auth_service.py` and `temp/test_auth_api.py` and run verification.
```

---

## 6. Implementation & Verification Status

> [!NOTE]
> **Implementation Complete & Fully Verified**
> - **Backend Service** (`ui/auth.py`): SQLite schema in `app_users.db`, PBKDF2-HMAC-SHA256 password hashing with 100k rounds and timing-attack-safe comparison, duplicate username rejection with 409 Conflict, 1-click guest accounts, and 32-byte secure session management.
> - **API Integration** (`ui/app.py`): `/api/auth/signup`, `/api/auth/login`, `/api/auth/guest`, `/api/auth/me`, `/api/auth/logout`. Integrated user identity into daily rate-limiting (`user:{user_id}`).
> - **Frontend UI** (`ui/static/index.html` & `ui/static/app.js`): Accessible Auth Modal with tabs for Sign In / Sign Up, hero 1-click guest button, inline red error banner, top header badge with user dropdown menu, keyboard navigation (ESC dismiss, focus management), and SVG icons adhering to `SKILL-Ui.md`.
> - **Verification Results**:
>   - `temp/test_auth_service.py`: **ALL 9 TESTS PASSED** (DB init, user registration, duplicate rejection, constraints, auth verification, guest user, session resolution & expiry, session deletion).
>   - `temp/test_auth_api.py`: **ALL 9 TESTS PASSED** (200, 201, 400, 401, 409 status codes, cookie management, guest session).
>   - `temp/test_auth_e2e.py`: **ALL E2E CHECKS PASSED** (HTML component verification, app.js serving, complete user lifecycle).
