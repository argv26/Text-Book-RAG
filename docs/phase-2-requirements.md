# Phase 2 Requirements Document: Multi-User Architecture, Performance, & Clinical UX Hardening

**Document Version**: 2.0  
**Date**: October 29, 2026  
**Status**: Pending Review & User Approval  
**Derived From**: Direct Clinical User Usability Feedback (`docs/user-feedback.md`)  

---

## 1. Executive Summary & Vision

Phase 1 established a functional Structure-Aware Medical RAG proof-of-concept deployed on the web. Usability testing with actual medical users revealed critical friction points:
1. Lack of user sessions and persistent consultation history.
2. Cluttered library control (inability to focus search on specific textbooks).
3. Slow answering latency (multi-second wait before first token).
4. Unpolished output (leaked LaTeX `\text{}` markup, broken hormone notation like `$T_4$`, raw internal database IDs `#1698`, and citation cache misses like `[14]`).
5. Need for deeper textbook-grade clinical depth, medical flowcharts, and cleaner UI formatting.

**Phase 2 Objective**: Evolve the platform from a single-session demo into a robust, multi-user, production-grade medical consultation system supporting authentication, user-specific book toggling, sub-2s streaming latency, verified citations, and high-yield visual clinical explanations.

---

## 2. User Authentication & Multi-User Architecture

To support multiple users without unnecessary complexity, we adopt a lightweight, frictionless identity system:

### 2.1 Authentication Modes
1. **Sign Up**:
   - Fields: `username`, `password` (minimum 6 characters).
   - Real-time or submit-time validation: *"Username already taken"* error if duplicate.
   - Passwords hashed securely using `bcrypt` (or `argon2`).
2. **Log In**:
   - Authenticate with `username` and `password`.
   - Issue signed, HTTP-only session cookies (or standard JWT bearer token).
3. **Continue as Guest**:
   - Instant 1-click access without entering credentials.
   - Assign an ephemeral guest session ID (`guest_<uuid>`).
   - Stores consultation history in browser `localStorage` or temporary backend guest storage (with option to migrate history if they register later).

### 2.2 Data Isolation & Schema Changes
- Database table `users`:
  - `user_id` (UUID or auto-increment integer, Primary Key)
  - `username` (VARCHAR, UNIQUE, Indexed)
  - `password_hash` (TEXT)
  - `is_guest` (BOOLEAN, default FALSE)
  - `created_at` (TIMESTAMP)
- Database table `consultation_sessions`:
  - `session_id` (UUID, Primary Key)
  - `user_id` (Foreign Key -> `users.user_id`)
  - `title` (VARCHAR - e.g. "Diabetic Ketoacidosis Triad", auto-generated from first query)
  - `created_at`, `updated_at` (TIMESTAMP)
- Database table `consultation_messages`:
  - `message_id` (UUID, Primary Key)
  - `session_id` (Foreign Key -> `consultation_sessions.session_id`)
  - `role` ('user' | 'assistant')
  - `content` (TEXT)
  - `citations_json` (TEXT / JSON)
  - `created_at` (TIMESTAMP)

---

## 3. Global Library & User-Level Scope Toggling

Based on feedback: **Books remain global; users do not delete books, but can toggle active books per consultation.**

### 3.1 Behavior & Specifications
1. **Global Book Registry**:
   - All ingested textbooks (*Williams Textbook of Endocrinology*, *Anatomy and Physiology 2e*, etc.) are available to all users.
   - Individual users cannot delete global book records or drop database tables.
2. **Per-User / Per-Consultation Book Toggling**:
   - In the left sidebar or consultation settings, each textbook has a simple switch/checkbox:
     - `[x] Williams Textbook of Endocrinology 15th Ed.` (Active)
     - `[ ] Anatomy and Physiology 2e` (Inactive / Muted)
   - When a book is toggled OFF:
     - Search queries strictly filter: `WHERE book_id IN (:active_book_ids)`.
     - The agent will never retrieve or cite chunks from unchecked books.
   - User's toggle preferences are persisted per user session in local preferences or `users.preferences`.

---

## 4. Performance & Answering Speed (< 2s TTFT)

### 4.1 Latency Bottlenecks in Phase 1
- Current ReAct loop runs multiple sequential LLM roundtrips:
  `Question -> LLM (tool call) -> FTS5 Search -> LLM (evaluate) -> LLM (final synthesis)`.
- On free-tier cloud environments, each round-trip takes 2–4s, resulting in a **10–15s delay** before the first word is displayed.

### 4.2 Phase 2 Speed Architecture
1. **Direct Fast-Path Retrieval (Default)**:
   - For standard clinical questions, immediately execute hybrid retrieval (parallel FTS5 keyword + Vector dense search) on the backend without waiting for an LLM planning turn.
   - Pipe retrieved context directly into the streaming synthesis LLM call.
   - **Target Time-to-First-Token (TTFT)**: `< 2.0 seconds`.
2. **Progressive Live Disclosures**:
   - While retrieval is executing, stream intermediate discovery pills to the frontend immediately:
     - `[Book: Williams Endocrinology > Chapter 23 found]`
     - `[3 relevant clinical passages extracted]`
   - Eliminates blank screen anxiety and lets users see immediate work.
3. **Execution Controls**:
   - Add a prominent **Stop Generation** button (square icon) replacing the send button during active streaming.
   - Client triggers `AbortController.abort()`, terminating both the HTTP stream and the backend background task.
   - Re-enable input box immediately on cancel.

---

## 5. Medical Accuracy, Typography & Clinical Depth

### 5.1 Medical Typography Standard (No Raw LaTeX Leaks)
- **Problem**: Output rendered `$\text{pH} < 7.3$`, `($< 15\text{ mmol/L}$)`, and `($T_4$ and $T_3$)`.
- **Requirements**:
  1. **Prompt Standard**: Enforce that the LLM must write all clinical measurements, laboratory values, and hormone acronyms in clean plain text and Unicode:
     - Hormones: `T4`, `T3`, `fT4`, `TSH`, `TRH`, `ACTH`, `FSH`, `LH` (Never `$T_4$`).
     - Lab Units: `mg/dL`, `mmol/L`, `mIU/L`, `ng/dL`, `pH < 7.3` (Never `\text{...}`).
     - Ranges: `200–250 mg/dL`, `β-hydroxybutyrate ≥ 3 mmol/L`.
  2. **Frontend Sanitization Filter**: A pre-render regex filter cleans any accidental `\text{...}` and converts simple LaTeX math patterns into readable plain Unicode text before parsing with Markdown.

### 5.2 Clinical Depth & Knowledge Boundaries
- **Requirements**:
  - Replace brief 2-paragraph summaries with **structured, textbook-grade clinical depth**:
    - **Overview & Definition**
    - **Pathophysiology & Molecular Mechanism** (e.g., receptor cross-talk, hormone deficiency cascade)
    - **Clinical Presentation & Cardinal Signs** (separated by demographics where appropriate, e.g., girls vs boys)
    - **Diagnostic Criteria & Biochemical Triad** (with exact lab thresholds)
    - **Management Protocol & Complications**
  - Synthesize strictly within the boundaries of the retrieved textbook chunks.

### 5.3 Clinical Flowcharts & Decision Trees
- **Requirements**:
  - Support clinical algorithms (e.g., DKA fluid resuscitation, diagnostic workup for precocious puberty) as visual flowcharts.
  - Native **Mermaid.js** rendering in the chat interface for dynamic decision trees.
  - Extract and display textbook figure plates and diagrams (4–5 high-yield diagrams) alongside the answer.

---

## 6. Citation Integrity & Evidence Drawer UX

### 6.1 Elimination of Citation Bugs (`[14]` Cache Miss & Discontinuity)
- **Root Cause**: The LLM copied original textbook bibliography citation numbers (`[14]`) from the raw text into the answer, while the RAG engine only retrieved 4 chunks (`[1]..[4]`).
- **Requirements**:
  1. **Citation Contract**: Agent prompt strictly prohibits copying external bibliographic citations from the text. Chunks must only be cited as `[1]`, `[2]`, `[3]`, up to `[k]`.
  2. **Citation Remapping / Sanitizer**: Backend post-processor verifies that every bracket `[x]` corresponds to an existing chunk index in `metadata.chunks`. Any out-of-bounds references are automatically remapped or stripped.
  3. **Ingestion Bibliography Filter**: During PDF ingestion, systematically identify and skip end-of-chapter "References", "Bibliography", and index pages so they are never stored as clinical evidence chunks.

### 6.2 Evidence Drawer Polish
- **Requirements**:
  - Remove all internal developer metrics: **Hide `SECTION ID: #1698`** and **`PROSE • 587 TOKENS`**.
  - Show clean medical metadata:
    - **Textbook**: *Williams Textbook of Endocrinology, 15th Edition*
    - **Chapter & Section**: *Chapter 23: Physiology and Disorders of Puberty > Peripheral Precocious Puberty*
    - **Calibrated Printed Page**: *Page 1422* (verified against physical book index).
  - Add de-hyphenation to passage excerpts so broken words like `compli \n cation` are cleanly rejoined into `complication`.

---

## 7. UI / UX Redesign & Component Polish

### 7.1 Sidebar & Session Management
- **Top**: User profile badge (`Guest` or `Dr. Username`) with Login/Sign Up or Logout action.
- **Consultation History**: Chronological list of past consultations (*Today*, *Yesterday*, *Previous 7 Days*).
- **Actions**: "+ New Consultation" (Ctrl+K), Rename consultation, Delete consultation.
- **Library Toggles**: Clean checkboxes/switches to enable/disable specific textbooks.

### 7.2 Chat Bubble Sizing & Layout
- **User Bubble Sizing Fix**: Remove fixed width/excessive padding. Style user message bubble with `width: fit-content; max-width: 70%; padding: 0.65rem 1.1rem; margin-left: auto;`.
- **Keyboard Navigation**: Ensure Enter and Down Arrow smoothly scroll and submit, and are never locked out while viewing past turns.
- **Evidence Drawer**: Slide-over drawer with smooth animations and close-on-escape.

---

## 8. Incremental Implementation Plan (Phase 2)

Following project guidelines, Phase 2 is decomposed into small, self-contained, verifiable steps:

| Step | Focus Area | Key Deliverables | Verification Criterion | Status |
|---|---|---|---|---|
| **Step 1** | **User Authentication & Guest Sessions** | SQLite `users` table, password hashing, `/api/auth/signup`, `/api/auth/login`, `/api/auth/guest`, session cookies. | User can sign up, duplicate username shows error, guest mode works in 1 click. | **Completed & Verified** |
| **Step 2** | **Session History & Sidebar UX** | `consultation_sessions` & `messages` tables, `/api/sessions`, left sidebar history list. | Conversations persist across reloads; user can switch between past consultations. | Next Up |
| **Step 3** | **Global Library Toggles** | UI checkboxes for books; backend query filter `WHERE book_id IN (...)`. | Unchecking Anatomy guarantees 0 anatomy chunks retrieved for medical questions. |
| **Step 4** | **Typography, Formatting & Citation Hardening** | Clean Unicode prompt rules, LaTeX tag stripper, citation remapping (fix `[14]`), remove `#1698` and tokens from drawer. | Units render as `pH < 7.3`, `T4, T3`; citations strictly sequential `[1]..[N]` without cache errors. |
| **Step 5** | **Response Depth, Diagrams & Flowcharts** | Structured comprehensive prompt boundaries, Mermaid.js clinical algorithm rendering, diagram retrieval. | Comprehensive topic breakdown generated with interactive flowchart for protocols. |
| **Step 6** | **Speed Optimization (<2s TTFT)** | Fast-path hybrid search pipeline, async parallel retrieval, progressive discovery streaming. | Time-to-First-Token drops to < 2.0 seconds on live test query. |
| **Step 7** | **UI Polish, Chat Bubbles & Stop Button** | Dynamic `fit-content` chat bubble CSS, `AbortController` stop button, responsive layout polish. | Short questions form compact bubbles; stop button instantly cancels active generation. |

---

## 9. Approval & Next Steps

This document captures 100% of the user feedback and architectural constraints discussed:
- **Simplicity**: No complex OAuth needed—clean username/password and 1-click guest mode.
- **Data safety**: Global books remain intact; users toggle visibility rather than deleting.
- **Clinical excellence**: Fixes medical notation, citation cache misses, and superficial answers.

> [!IMPORTANT]
> **Approval Gate**: In accordance with the project interaction guidelines, implementation will not commence until this requirement document is reviewed and approved by the user. Once approved, we will begin with **Step 1: User Authentication & Guest Sessions**.

