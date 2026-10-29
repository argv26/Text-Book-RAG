# Structure-Aware Textbook RAG: API Reference

This document provides the complete HTTP API reference for the Textbook RAG application.

---

## 1. Authentication Endpoints

### `POST /api/auth/signup`
Creates a standard registered account.
- **Request Body**:
  ```json
  {
    "username": "dr_house",
    "password": "secretpassword"
  }
  ```
- **Response `201 Created`**:
  ```json
  {
    "user": { "id": "usr_...", "username": "dr_house", "is_guest": false },
    "token": "tok_..."
  }
  ```
- **Sets Cookie**: `auth_session` (HttpOnly, SameSite=Lax, Max-Age=30d)

### `POST /api/auth/login`
Authenticates existing credentials.
- **Request Body**:
  ```json
  {
    "username": "dr_house",
    "password": "secretpassword"
  }
  ```
- **Response `200 OK`**: User profile and session token.

### `POST /api/auth/guest`
Generates an ephemeral 1-click guest identity with no password required.
- **Response `200 OK`**:
  ```json
  {
    "user": { "id": "guest_...", "username": "guest_...", "is_guest": true },
    "token": "tok_..."
  }
  ```

### `GET /api/auth/me`
Retrieves the currently authenticated user profile, or `{ "user": null }` if unauthenticated.

### `POST /api/auth/logout`
Invalidates active session token and clears the `auth_session` cookie.

---

## 2. Consultation Sessions Endpoints

### `GET /api/sessions`
Lists all consultation sessions owned by the active user/guest.
- **Response `200 OK`**:
  ```json
  {
    "sessions": [
      {
        "id": "ses_...",
        "title": "Adrenal Cortex Zonation",
        "message_count": 4,
        "created_at": "2026-10-30T00:00:00Z",
        "updated_at": "2026-10-30T00:05:00Z"
      }
    ]
  }
  ```

### `POST /api/sessions`
Initializes a new consultation session.
- **Request Body**: `{ "title": "New Consultation" }`

### `GET /api/sessions/{session_id}`
Retrieves session metadata, complete message history, and accumulated citations.

### `PATCH /api/sessions/{session_id}`
Updates a session title.
- **Request Body**: `{ "title": "Updated Title" }`

### `DELETE /api/sessions/{session_id}`
Deletes a consultation session and cascade-deletes all associated messages.

---

## 3. Chat & Streaming Endpoints

### `POST /api/chat`
Processes a synchronous conversational turn.
- **Request Body**:
  ```json
  {
    "session_id": "ses_...",
    "message": "What is the classic clinical triad of DKA?",
    "book_slug": "optional_slug",
    "active_books": ["book1", "book2"]
  }
  ```
- **Response `200 OK`**:
  ```json
  {
    "reply": "Markdown synthesized clinical answer...",
    "citations": [ ... ],
    "tool_calls": [ ... ],
    "follow_ups": [ ... ],
    "figures": [ ... ]
  }
  ```

### `POST /api/chat/stream`
Processes a conversational turn with Server-Sent Events (SSE).
- **Request Body**: Same as `/api/chat`.
- **Response `text/event-stream` Events**:
  - `event: status` — `{ "text": "Searching medical textbooks..." }`
  - `event: tools` — `[{ "name": "search_chunks", ... }]`
  - `event: token` — `{ "delta": "word " }`
  - `event: citations` — `[ ... ]`
  - `event: figures` — `[ ... ]`
  - `event: follow_ups` — `[ ... ]`
  - `event: done` — `{ "reply": "..." }`

### `GET /api/quota`
Returns remaining daily request quota for the caller.
- **Response `200 OK`**:
  ```json
  {
    "limit": 50,
    "used": 3,
    "remaining": 47
  }
  ```

---

## 4. Content & Diagram Endpoints

### `GET /api/books`
Lists indexed textbooks and page statistics.

### `GET /api/chunk/{chunk_id}`
Retrieves full passage text, heading path, and page metadata for the Evidence Drawer.

### `GET /api/figure/{chunk_id}/image`
Serves high-resolution JPEG diagram extracted from the textbook page (cached with `max-age=86400`).

### `GET /api/session/{session_id}/citations`
Returns all accumulated verified citations across the conversation session.

