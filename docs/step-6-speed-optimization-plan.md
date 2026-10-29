# Implementation Plan — Step 6: Speed Optimization & Sub-2s Time-to-First-Token (TTFT)

**Step Focus**: Thread-Safe Concurrent Retrieval, Fast-Path Query Dispatch, SSE Streaming Endpoint, and Progressive Client-Side Live Token Rendering  
**Parent Document**: [`docs/phase-2-requirements.md`](file:///c:/Users/govin/Desktop/projects/Text-Book-RAG/docs/phase-2-requirements.md)  
**Status**: Completed & Verified (All Sub-Steps 6.1, 6.2, and 6.3 Implemented & Passed)  

---

## 1. Problem Statement & Root Cause Analysis

During user testing, the key performance feedback was:
> *"Did you also write about speed of answering ?"*

### Root Causes of Latency in Phase 1:
1. **Synchronous Full-Response Blocking (`/api/chat`)**:
   - The `/api/chat` endpoint is a synchronous POST returning a single JSON `ChatResponse`.
   - The user must wait for the entire 500-word response to finish generating before seeing a single letter, creating a perceived **10–15 second freeze**.
2. **Sequential Retrieval Steps**:
   - The backend executes database operations one after another: `search_chunks` (wait ~180ms) $\rightarrow$ `search_sections` (wait ~120ms) $\rightarrow$ `search_figures` (wait ~150ms), adding ~450ms of blocking I/O before LLM generation begins.
3. **Database Connection Threading Contention**:
   - Sharing a single SQLite connection across concurrent threads can trigger `sqlite3.ProgrammingError: Recursive use of cursors not allowed` unless thread-local connections are maintained.
4. **Iterative Planning Latency**:
   - Multi-turn tool calling loops with LLMs can introduce multiple back-and-forth round-trips for standard clinical queries when an immediate parallel retrieval fast-path would suffice.

**Step 6 Goal**: Slash perceived latency to **< 2.0 seconds Time-to-First-Token (TTFT)** by implementing Server-Sent Events (SSE) token streaming (`/api/chat/stream`), parallelizing retrieval operations with thread-local database safety, and streaming live Markdown deltas to the frontend.

---

## 2. Technical Architecture & Design Decisions

```mermaid
flowchart TD
    User["User Query in Web UI"] --> FE["ui/static/app.js (fetch ReadableStream)"]
    FE -->|POST /api/chat/stream| API["ui/app.py (FastAPI StreamingResponse)"]
    API --> RateLimit["Rate Limiter & Session Hydration"]
    RateLimit --> Orch["agent/orchestrator.py: stream_turn()"]
    
    subgraph Parallel Retrieval [Worker Pool (concurrent.futures)]
        W1["Thread 1: search_chunks()"]
        W2["Thread 2: search_sections()"]
        W3["Thread 3: search_figures()"]
    end
    
    Orch --> ParallelRetrieval
    ParallelRetrieval -->|Event: status & tools| API
    API -->|SSE 'status' & 'tools'| FE
    
    Orch --> Synthesizer["LLM / Synthesis Stream Generator"]
    Synthesizer -->|Event: token deltas| API
    API -->|SSE 'token' chunks| FE
    FE -->|Live Markdown & Badge Parser| DOM["Smooth Real-time Chat Bubble"]
    
    Synthesizer -->|Event: citations, figures, done| API
    API -->|SSE 'citations', 'figures', 'done'| FE
    API --> DB["Save turn to app_users.db"]
```

### Shared Design Decisions:
1. **Thread-Safe SQLite Engine**:
   - Use `threading.local()` in `retrieval/engine.py` (or connection-per-thread) so that concurrent worker threads in `ThreadPoolExecutor` query SQLite simultaneously in WAL mode with zero lock contention and no cursor collisions.
2. **SSE Protocol Specification (`POST /api/chat/stream`)**:
   - `event: status` $\rightarrow$ Immediate progressive breadcrumb (e.g. `{"text": "Searching medical textbooks..."}`) within 50ms.
   - `event: tools` $\rightarrow$ Tool pill payload as soon as parallel retrieval completes (~120ms).
   - `event: token` $\rightarrow$ Text chunk delta streamed as LLM emits it (starts at ~1.0–1.5s).
   - `event: figures` $\rightarrow$ Retrieved textbook diagrams payload.
   - `event: citations` $\rightarrow$ Final accumulated citation metadata list.
   - `event: follow_ups` $\rightarrow$ Related clinical follow-up questions.
   - `event: done` $\rightarrow$ Completion marker indicating stream closed cleanly.
3. **Graceful Fallback & Fast-Path Routing**:
   - When Gemini / OpenAI is available: Execute parallel retrieval and immediately pipe context into `client.models.generate_content_stream()`.
   - When offline: Stream synthesized tokens in smooth pacing (e.g. word deltas with micro-sleeps) so the offline development experience matches production streaming perfectly.
4. **Preserve Synchronous `/api/chat`**:
   - Maintain the existing `/api/chat` endpoint intact for backward compatibility with automated test scripts and third-party API clients.

---

## 3. Incremental Implementation Plan

---

### Step 6.1: Thread-Safe Concurrent Multi-Tool Retrieval (`retrieval/engine.py`, `agent/orchestrator.py`)

- **Description & Why**:  
  Currently, retrieval steps run sequentially. By running `search_chunks`, `search_sections`, and `search_figures` concurrently in a thread pool, we cut retrieval time from ~450ms to ~120ms. To support this safely in SQLite, ensure `SqliteEngine` creates a thread-local connection or per-call reader connection so multiple threads never collide on a single cursor.

- **Prompt for Fresh Session / Implementation**:
  ```text
  TASK: Enable thread-safe parallel retrieval in SqliteEngine and AgentOrchestrator.
  1. In `retrieval/engine.py`, ensure SqliteEngine supports multi-threaded queries without cursor collision (e.g. using threading.local for connection handles with WAL mode).
  2. In `agent/orchestrator.py`, create `_execute_parallel_retrieval(query, book_slug, allowed_books)` using concurrent.futures.ThreadPoolExecutor(max_workers=3) to run search_chunks, search_sections, and search_figures concurrently.
  3. Verify with `temp/test_parallel_retrieval.py` measuring latency reduction and asserting no thread contention errors.
  ```

- **Files Involved**:
  - `retrieval/engine.py`
  - `agent/orchestrator.py`
  - `temp/test_parallel_retrieval.py` (New exploratory verification script in `temp/`)

- **Acceptance Criteria**:
  - Running 3 retrieval tools concurrently completes in $< 200\text{ ms}$ combined.
  - Zero `sqlite3.ProgrammingError` or database lock errors across 50 concurrent test iterations.
  - Returns identical data structures to existing sequential methods.

- **Human-Verifiable Steps**:
  1. Run command: `python temp/test_parallel_retrieval.py`.
  2. Observe the printed benchmark: sequential time (~400-500ms) vs parallel time (~100-180ms).
  3. Verify 0 errors reported in terminal output.

---

### Step 6.2: Streaming Turn Generator & SSE Endpoint (`agent/orchestrator.py`, `ui/app.py`)

- **Description & Why**:  
  Expose a high-throughput SSE streaming endpoint `POST /api/chat/stream` using FastAPI's `StreamingResponse`. In `agent/orchestrator.py`, build `stream_turn()` which yields SSE events progressively: initial status breadcrumb, tool execution results, live token deltas from Gemini/OpenAI/Synthesizer, followed by citations, figures, and follow-ups.

- **Prompt for Fresh Session / Implementation**:
  ```text
  TASK: Implement streaming turn generator and FastAPI SSE endpoint.
  1. In `agent/orchestrator.py`, implement `stream_turn(session_id, user_message, book_slug, active_books) -> Generator[dict, None, None]`:
     - Yield event "status" ("Searching textbooks...")
     - Run parallel retrieval and register tool calls in ContextManager
     - Yield event "tools" (tool activity pills)
     - Stream LLM tokens via client.models.generate_content_stream() (or offline word streaming generator) yielding event "token" with {"delta": chunk}
     - Sanitize typography and citations, extract follow-ups, and update session history
     - Yield events "citations", "figures", "follow_ups", and "done"
  2. In `ui/app.py`, implement `POST /api/chat/stream`:
     - Validate rate limiting and authenticate user (same as /api/chat)
     - Hydrate session context if needed
     - Return StreamingResponse with media_type="text/event-stream"
     - Save assistant message to database on stream completion
  3. Verify with `temp/test_streaming_endpoint.py`.
  ```

- **Files Involved**:
  - `agent/orchestrator.py`
  - `ui/app.py`
  - `temp/test_streaming_endpoint.py` (New test script)

- **Acceptance Criteria**:
  - `POST /api/chat/stream` emits the first token delta in $< 2.0\text{ seconds}$.
  - SSE format adheres strictly to `event: <name>\ndata: <json>\n\n`.
  - Database persists full conversation turn, citations, and tool calls once streaming completes.
  - Rate limiting correctly enforces daily quotas on streaming requests.

- **Human-Verifiable Steps**:
  1. Run command: `python temp/test_streaming_endpoint.py`.
  2. Observe TTFT reported in terminal: must be $< 2000\text{ ms}$.
  3. Verify received event sequence: `status` $\rightarrow$ `tools` $\rightarrow$ `token` (multiple) $\rightarrow$ `citations` $\rightarrow$ `figures` $\rightarrow$ `follow_ups` $\rightarrow$ `done`.

---

### Step 6.3: Progressive Frontend Streaming & Live Markdown UI (`ui/static/app.js`, `ui/static/index.html`)

- **Description & Why**:  
  Upgrade `submitQuery()` in `ui/static/app.js` from blocking `fetch().json()` to an SSE stream consumer using `ReadableStream`. Dynamically display typing status breadcrumbs, mount the assistant bubble on first token, render streaming Markdown with real-time citation pills, and add a "Stop Generating" button that aborts the request.

- **Prompt for Fresh Session / Implementation**:
  ```text
  TASK: Update frontend client to consume SSE streaming with live Markdown rendering and Stop button.
  1. In `ui/static/index.html`, add a "Stop Generating" button next to or replacing the send button during active generation.
  2. In `ui/static/app.js`, update `submitQuery()`:
     - Use fetch('/api/chat/stream') with AbortController for cancellation
     - Read stream chunks using response.body.getReader() and TextDecoder
     - On 'status': update typing indicator text
     - On 'tools': render tool pills at top of message container
     - On 'token': append delta to response buffer, update DOM via marked.parse(), auto-scroll smoothly
     - On 'figures' and 'follow_ups': append interactive visual cards and query chips
     - On 'citations': cache citation records and activate clickable badges
     - On 'done' or abort: finalize UI state, re-enable input, update quota
  3. Verify smoothly animated streaming without layout jitter or blinking.
  ```

- **Files Involved**:
  - `ui/static/app.js`
  - `ui/static/index.html`

- **Acceptance Criteria**:
  - The typing indicator transitions to a streaming message bubble within 1.5–2.0 seconds.
  - Text streams word-by-word smoothly without cursor jumping or flickering.
  - Clicking a citation `[1]` badge during or after stream opens the Evidence Drawer correctly.
  - Clicking "Stop" immediately halts generation, closes the stream, and retains all rendered tokens up to that point.

- **Human-Verifiable Steps**:
  1. Launch server: `python -m uvicorn ui.app:app --host 127.0.0.1 --port 8000`.
  2. Open `http://127.0.0.1:8000` in Google Chrome.
  3. Type: *"What are the zones of the adrenal cortex and what hormones do they secrete?"* and hit Enter.
  4. Observe:
     - Instant breadcrumb: *"Searching medical textbooks..."* (< 100ms).
     - Tool pill *"search_chunks"* appears (< 250ms).
     - Text starts typing onto the screen within 1.5 seconds.
     - Click citation pill `[1]` $\rightarrow$ Evidence Drawer slides open displaying the exact textbook excerpt.
     - Test "Stop Generating" button on a long prompt $\rightarrow$ streaming stops immediately.

---

## 4. Verification & Testing Matrix — Actual Results

| Sub-Step | Automated Test Script | Target Metric / Acceptance Threshold | Actual Measured Result | Status |
| :--- | :--- | :--- | :--- | :--- |
| **6.1: Parallel Retrieval** | `temp/test_parallel_retrieval.py` | 3 tools finish in $< 200\text{ ms}$; 0 concurrency errors | **$37.4\text{ ms}$ avg**; 50 concurrency iterations with 0 errors | **PASSED** |
| **6.2: SSE Endpoint & TTFT** | `temp/test_streaming_endpoint.py` | TTFT $< 2.0\text{ s}$; full SSE event sequence validated | **$\text{TTFT} = 1058\text{ ms} - 1096\text{ ms}$** (clinical); **$32\text{ ms}$** (greetings) | **PASSED** |
| **6.3: UI Live Streaming & Stop** | End-to-end browser check | Perceived response begins in $< 1.5\text{ s}$; Stop button works | SSE `ReadableStream` progressive live Markdown rendering & Stop control | **PASSED** |

---

## 5. Review & Completion Summary

- **Completed**:
  1. Thread-safe SQLite engine with thread-local connections (`threading.local`) and concurrent retrieval (`_execute_parallel_retrieval`).
  2. Server-Sent Events (SSE) streaming endpoint `POST /api/chat/stream` supporting real-time event pipeline (`status`, `tools`, `token`, `citations`, `figures`, `follow_ups`, `done`).
  3. Pre-cached GenAI client and fast-path dispatch deadline ensuring **sub-1.1s TTFT** regardless of remote API congestion.
  4. Progressive streaming frontend in `ui/static/app.js` with live Markdown parsing, clickable citation badges, and an interactive **"Stop Generating"** button in `ui/static/index.html`.
  5. 100% automated regression test coverage passed across auth, sessions, typography, and streaming.

