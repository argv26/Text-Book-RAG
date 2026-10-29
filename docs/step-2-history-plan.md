# Implementation Plan — Step 2: Session History & Sidebar UX

**Step Focus**: Persistent Consultation History, Left Sidebar Consultation List, Multi-Turn Session Switching  
**Parent Document**: [`docs/phase-2-requirements.md`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/docs/phase-2-requirements.md)  
**Status**: Fully Implemented & Verified (Tests Passing 100%)  

---

## 1. Problem Statement & Scope

In the usability feedback session ([`docs/user-feedback.md`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/docs/user-feedback.md)), the user's first feedback item was:
> *"1) left side past history chat dhi ledu"* (There is no past chat history on the left side).

### Deficiencies in Current State:
1. **Ephemeral State**: Sessions exist only in browser memory (`session_XXXXXX`) and orchestrator memory. Refreshing or returning to the app loses all past medical queries, retrieved citations, and answers.
2. **Missing Sidebar Navigation**: The left sidebar currently shows static library stats and starter prompts, but lacks the core navigation expected in modern AI tools (ChatGPT, Claude, Perplexity): a **chronological list of previous consultations** that users can resume, review, or delete.
3. **No Multi-Turn Context Restoration**: If an existing consultation is reopened, the agent orchestrator has no bridge to load past messages from disk, breaking multi-turn context after server restart or session switching.

### Step 2 Objectives:
1. Implement persistent SQLite storage for consultation sessions and message turns in `app_users.db`.
2. Provide clean REST endpoints in FastAPI for session lifecycle (list, create, read, rename, delete).
3. Integrate session persistence into `/api/chat` with automatic title generation from the clinical query.
4. Redesign the left sidebar to feature an accessible, interactive "Recent Consultations" list adhering to `SKILL-Ui.md`.
5. Support frictionless session switching, caching citations per session, and instant consultation deletion.

---

## 2. Technical Architecture & Database Schema

### 2.1 Database Schema (`app_users.db`)
Sessions and messages are stored in `app_users.db` alongside user accounts. This keeps user data completely isolated from the indexed textbook database `textbook_rag.db`.

```sql
-- Consultation Sessions Table
CREATE TABLE IF NOT EXISTS consultation_sessions (
    id TEXT PRIMARY KEY,                            -- 'ses_' + uuid4 hex
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New Consultation', -- e.g. "Diabetic Ketoacidosis Triad"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- Updated whenever a message is added
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON consultation_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON consultation_sessions(updated_at DESC);

-- Consultation Messages Table
CREATE TABLE IF NOT EXISTS consultation_messages (
    id TEXT PRIMARY KEY,                            -- 'msg_' + uuid4 hex
    session_id TEXT NOT NULL REFERENCES consultation_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    citations_json TEXT,                            -- Serialized JSON array of CitationRecord dicts
    tool_calls_json TEXT,                           -- Serialized JSON array of tool execution records
    follow_ups_json TEXT,                           -- Serialized JSON array of suggested follow-up strings
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON consultation_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON consultation_messages(created_at ASC);
```

### 2.2 Clinical Auto-Titling Heuristic
When the first user query arrives for a consultation, generate a clear, professional medical title (4–6 words, max 45 characters) without requiring extra LLM calls or latency:
- Strip common conversational and interrogative prefixes:
  `["what is the", "what are the", "how does", "how do", "can you explain", "explain the", "describe", "tell me about", "what causes"]`
- Strip trailing question marks and punctuation.
- Capitalize key clinical words (e.g. *"Diabetic Ketoacidosis Triad"*, *"Femoral Triangle Anatomy"*).
- Truncate cleanly at word boundaries if exceeding 45 characters.

### 2.3 Multi-Turn Context Hydration
When a user continues chatting in an existing session:
1. `api_chat` checks if `orchestrator.context_manager` has the session loaded.
2. If empty (e.g., after server restart or switching to an older session), `ui/sessions.py` loads the past message turns and verified citations from SQLite into `SessionState`.
3. The orchestrator continues conversational reasoning seamlessly with full context and token budgeting intact.

---

## 3. Sub-Step Breakdown & Component Specifications

### Sub-Step 2.1: Session & Message Persistence Module (`ui/sessions.py`)
- **Location**: `ui/sessions.py`
- **Responsibilities**:
  - `init_sessions_db(db_path=None)`: Creates tables and indices in `app_users.db`.
  - `generate_smart_title(query: str) -> str`: Produces clean clinical titles from questions.
  - `create_session(user_id: str, title: str = "New Consultation", session_id: str | None = None, db_path=None) -> dict`: Inserts new session.
  - `list_user_sessions(user_id: str, limit: int = 50, db_path=None) -> list[dict]`: Returns user's sessions sorted by `updated_at DESC`.
  - `get_session(session_id: str, user_id: str, db_path=None) -> dict | None`: Returns session metadata with authorization check.
  - `get_session_messages(session_id: str, user_id: str, db_path=None) -> list[dict] | None`: Returns chronologically ordered messages with parsed JSON fields (`citations`, `tool_calls`, `follow_ups`).
  - `save_message(session_id: str, role: str, content: str, citations=None, tool_calls=None, follow_ups=None, db_path=None) -> dict`: Appends message and touches `updated_at` on the session.
  - `update_session_title(session_id: str, user_id: str, new_title: str, db_path=None) -> bool`: Renames consultation.
  - `delete_session(session_id: str, user_id: str, db_path=None) -> bool`: Cascading delete of session and all messages.

### Sub-Step 2.2: FastAPI Endpoints & Chat Persistence (`ui/app.py`)
- **Endpoints to Expose**:
  - `GET /api/sessions`: Returns `{"sessions": [...]}` for the authenticated user or guest.
  - `POST /api/sessions`: Creates an explicit new session; returns `{"session": {...}}`.
  - `GET /api/sessions/{session_id}`: Returns `{"session": {...}, "messages": [...]}`. Returns 404 if not found or unauthorized.
  - `PATCH /api/sessions/{session_id}`: Accepts `{"title": "..."}`; returns updated session.
  - `DELETE /api/sessions/{session_id}`: Deletes consultation; returns `{"status": "deleted"}`.
- **Hook in `POST /api/chat`**:
  - Resolves active user (registered clinician or guest).
  - Ensures session exists in `consultation_sessions`; if not, creates it.
  - If title is default `"New Consultation"` and this is the first turn, auto-updates title to `generate_smart_title(message)`.
  - Saves user message to `consultation_messages`.
  - Hydrates `orchestrator.context_manager` if empty.
  - Runs agent turn.
  - Saves assistant response, citations, tool calls, and follow-ups to `consultation_messages`.

### Sub-Step 2.3: Redesigned Left Sidebar & Session Navigation UI (`ui/static/index.html` & `ui/static/app.js`)
- **Left Sidebar Redesign Structure**:
  ```
  +-----------------------------------------------------+
  | [Medical Icon] TextBook RAG                         |
  | Dr. Govind (or Guest)                    [Log Out]  |
  +-----------------------------------------------------+
  | [+ New Consultation]  (Ctrl + K)                    |
  +-----------------------------------------------------+
  | RECENT CONSULTATIONS (Flex-1 Scrollable Area)       |
  |  [Active] 💬 Diabetic Ketoacidosis Triad      [🗑] |
  |           💬 Femoral Triangle Anatomy          [🗑] |
  |           💬 Thyroid Hormone Regulation        [🗑] |
  |  (Empty State: "No consultations yet")              |
  +-----------------------------------------------------+
  | TEXTBOOK SCOPE & LIBRARY (Collapsible / Compact)    |
  |  [All Ingested Volumes (Auto-Route)              v] |
  |  * Williams Endocrinology 15e (1,438 pp.)           |
  |  * Anatomy & Physiology 2e (1,439 pp.)              |
  +-----------------------------------------------------+
  | [●] SQLite FTS5 Active                         v1.0 |
  +-----------------------------------------------------+
  ```
- **UI Interactions & Standards (per `SKILL-Ui.md`)**:
  - **Active Session Styling**: Left blue border indicator, `bg-blue-50/80 text-blue-700 font-semibold`.
  - **Smooth Session Switching**: Clicking a consultation immediately loads message turns into the chat feed, restores citations into the Evidence Drawer, and updates the active session ID.
  - **Accessible Delete Action**: Inline trash SVG button with tooltip. Clicking delete shows a brief confirmation dialog or immediate removal with toast notification.
  - **Keyboard Shortcut**: `Ctrl + K` (or `Cmd + K`) resets to a new consultation.
  - **Touch & Mobile Support**: Automatically closes mobile sidebar backdrop when switching consultations on mobile.

---

## 4. Verification Protocol & Acceptance Test Plan

### 4.1 Automated Test Suites (in `temp/`)

1. **Service Layer Unit Tests (`temp/test_sessions_service.py`)**:
   - `test_init_sessions_db`: Validates creation of tables and indexes in a temporary SQLite database.
   - `test_create_and_list_sessions`: Verifies sessions are created and listed in `updated_at DESC` order.
   - `test_user_isolation`: Verifies User A cannot read or delete User B's sessions.
   - `test_save_and_retrieve_messages`: Verifies message turns, citations, tool calls, and follow-ups round-trip intact through JSON serialization.
   - `test_smart_title_generation`: Validates removal of question words and formatting of medical terms.
   - `test_update_session_title`: Validates explicit renaming.
   - `test_cascade_delete`: Validates that deleting a session removes all associated message records.

2. **FastAPI Integration Tests (`temp/test_sessions_api.py`)**:
   - `test_unauthenticated_session_endpoints`: Verifies unauthenticated calls fail or handle guest sessions cleanly.
   - `test_list_sessions_endpoint`: Verifies `GET /api/sessions`.
   - `test_create_session_endpoint`: Verifies `POST /api/sessions`.
   - `test_get_session_endpoint`: Verifies `GET /api/sessions/{session_id}` returns full dialogue and citations.
   - `test_delete_session_endpoint`: Verifies `DELETE /api/sessions/{session_id}`.
   - `test_chat_auto_persists_session_and_messages`: Verifies that sending a query to `/api/chat` creates a session, auto-titles it, and persists both user and assistant turns.

3. **End-to-End Persistence & Hydration Test (`temp/test_sessions_e2e.py`)**:
   - Simulates a user asking a question in Session 1, starting Session 2 with a new question, switching back to Session 1, and continuing the conversation with a follow-up to verify multi-turn context retention.

### 4.2 Human-Verifiable Browser Testing Steps

| Step | Action | Expected Visual & Functional Result |
| :--- | :--- | :--- |
| **H1** | Open `http://127.0.0.1:8000/` and sign in or continue as Guest. | Sidebar renders "+ New Consultation" button and "Recent Consultations" header. Empty state shown if no past history. |
| **H2** | Ask: *"What is the classic triad of Diabetic Ketoacidosis?"* | Query runs. In the left sidebar under "Recent Consultations", a new session appears auto-titled *"Diabetic Ketoacidosis Triad"*. |
| **H3** | Click **"+ New Consultation"** (or press `Ctrl+K`). | Chat feed clears and returns to the initial Welcome screen. The DKA consultation remains visible in the sidebar list. |
| **H4** | Ask a new question: *"What are the boundaries of the femoral triangle?"* | A second consultation appears in the sidebar titled *"Femoral Triangle Boundaries"*, highlighted as the active session. |
| **H5** | Click the first consultation (*"Diabetic Ketoacidosis Triad"*) in the sidebar. | Chat feed immediately reloads the full DKA question, answer, and verified citation pills. Evidence Drawer badge updates with DKA citations. |
| **H6** | Refresh the browser page (`F5`). | Page reloads. Both consultations remain intact in the sidebar. The selected consultation's messages are preserved. |
| **H7** | Click the trash icon on the femoral triangle consultation. Confirm deletion. | The consultation is removed from the sidebar and deleted from the database. |

---

## 5. Execution Summary & Verification Status

> [!NOTE]
> **Implementation Complete & Fully Verified (100% Tests Passing)**
> - **Backend Persistence Service** ([`ui/sessions.py`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/ui/sessions.py)):
>   - SQLite tables `consultation_sessions` and `consultation_messages` created in `app_users.db` with cascading foreign keys and indexes.
>   - Multi-user isolation enforcing that doctors and guest sessions only access their own consultations.
>   - Clinical smart auto-titler `generate_smart_title(query)` generating concise topic titles without extra LLM latency.
>   - Serialization and deserialization of verified citations, tool execution traces, and follow-up clinical questions.
> - **FastAPI REST Endpoints** ([`ui/app.py`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/ui/app.py)):
>   - `GET /api/sessions`: Returns user's consultations sorted by `updated_at DESC`.
>   - `POST /api/sessions`: Explicitly creates a consultation session.
>   - `GET /api/sessions/{session_id}`: Fetches consultation turn history and all accumulated citations.
>   - `PATCH /api/sessions/{session_id}`: Renames consultation.
>   - `DELETE /api/sessions/{session_id}`: Deletes consultation and cleans up both SQLite and memory cache.
>   - `POST /api/chat` integration: Transparently handles guest identity, auto-creates session, generates clinical title on first turn, saves user/assistant messages, and hydrates orchestrator memory on resumption.
> - **Frontend UI Redesign** ([`ui/static/index.html`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/ui/static/index.html) & [`ui/static/app.js`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/ui/static/app.js)):
>   - Redesigned left sidebar with dedicated "Recent Consultations" scrollable section, active item indicator, relative timestamps ("Just now", "5m ago"), and turn counters.
>   - Accessible inline trash button with confirmation prompt for deleting consultations.
>   - Instant session switching that restores the exact dialogue, markdown formatting, citations cache, and Evidence Drawer state.
>   - "+ New Consultation" button with `Ctrl + K` global shortcut.
> - **Verification Suite Results**:
>   - `temp/test_sessions_service.py`: **ALL 10 TESTS PASSED** (DB schema, smart titling, session creation, sorting, user isolation, message serialization, title update, cascade delete).
>   - `temp/test_sessions_api.py`: **ALL 9 TESTS PASSED** (endpoint routing, guest auto-creation, chat auto-titling, dialogue persistence, deletion).
>   - `temp/test_sessions_e2e.py`: **ALL E2E CHECKS PASSED** (multi-turn conversation switching, context memory hydration from database).
>   - `temp/test_sessions_ui.py`: **ALL UI CHECKS PASSED** (index.html DOM elements, app.js handlers, styles.css).
>   - `temp/test_auth_service.py` & `temp/test_auth_api.py`: **ALL PASSING** (No regressions).
