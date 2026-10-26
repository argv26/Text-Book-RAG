# Comprehensive UI & User Experience Test Plan — TextBook RAG

This document defines the formal, incremental testing plan for evaluating the end-to-end user experience, responsiveness, and clinical reliability of the TextBook RAG application prior to production deployment.

---

## Plan Overview

The testing is split into **4 independent, verifiable steps**:

1. **Step 1:** Automated End-to-End UX & API Test Suite (`temp/run_comprehensive_ux_test.py`)
2. **Step 2:** Frontend Polish & Session Continuity (`ui/static/app.js`, `ui/static/index.html`)
3. **Step 3:** Interactive Human Verification Walkthrough (Browser testing at `http://localhost:8000`)
4. **Step 4:** Production Verification Report & Deployment Gate

---

## Step 1: Automated End-to-End UX & API Test Suite

### 1. Description
Build and run a comprehensive automated Python test suite (`temp/run_comprehensive_ux_test.py`) that simulates a real user walking through all 5 key user journeys:
- **Journey 1:** First-time load: Verifies library books (`GET /api/books`) and initial quota state (`GET /api/quota`).
- **Journey 2:** First question: Sends a clinical query, verifies answer length, tool pills count, follow-up chips, and citation registration.
- **Journey 3:** Evidence Drawer: Fetches the cited chunk (`GET /api/chunk/{id}`) and validates book title, heading path, and printed page number.
- **Journey 4:** Multi-turn loop: Executes a second turn using a suggested follow-up chip, ensuring citation IDs accumulate without resetting (`[1]`, `[2]`, `[3]`, `[4]`).
- **Journey 5:** Edge cases & Rate limits: Tests empty messages (400), out-of-scope queries, and quota exhaustion (429 with `Retry-After`).

### 2. Prompt for Execution
```
Execute the comprehensive automated UX and API test script at temp/run_comprehensive_ux_test.py against ui.app.
Verify that:
1. /api/books returns 2 indexed textbooks (Anatomy & Physiology 2e, Williams Endocrinology 15e).
2. /api/quota returns current usage and remaining queries.
3. /api/chat produces a structured clinical response with tool pill logs, citations, and 2-3 follow-up exploration questions.
4. /api/chunk/{id} returns raw chunk text for the Evidence Drawer.
5. Turn 2 accumulates citations cleanly across turns.
6. Rate limiting enforces the daily quota and returns 429 when exhausted.
Report pass/fail status with timings for each journey.
```

### 3. Relevant Files
- `temp/run_comprehensive_ux_test.py`
- `ui/app.py`
- `ui/rate_limiter.py`
- `agent/orchestrator.py`
- `context/manager.py`

### 4. Acceptance Criteria
- All 5 test journeys pass with zero assertion errors.
- End-to-end test execution time is under 5 seconds.
- Quota decrements properly and resets after testing.

### 5. Verification Steps
1. Run `.venv\Scripts\python.exe temp/run_comprehensive_ux_test.py`.
2. Observe output logging each journey with `[PASS]`.
3. Confirm clean database state after test run.

---

## Step 2: Frontend Polish & Session Continuity

### 1. Description
Ensure the UI feels delightful, responsive, and persistent:
- **Session Continuity:** Save `session_id` and message history in browser `localStorage` so refreshing the browser preserves the active consultation and Evidence Drawer citations.
- **Textarea Ergonomics:** Auto-expanding height up to 160px; `Enter` sends, `Shift+Enter` inserts newline, `Ctrl+K` clears and starts a fresh consultation.
- **Mobile Responsiveness:** Ensure Evidence Drawer covers full width on screens < 640px with a prominent `✕` close button.

### 2. Prompt for Execution
```
Update ui/static/app.js and ui/static/index.html to:
1. Store session_id and active message history in localStorage so user conversations survive page refresh.
2. Add a clear confirmation when pressing Ctrl+K or clicking "New Consultation".
3. Ensure the Evidence Drawer displays full width on mobile screens (<640px) with smooth slide-in transitions.
4. Verify textarea auto-grow smoothly handles multi-line clinical queries without covering chat bubbles.
```

### 3. Relevant Files
- `ui/static/app.js`
- `ui/static/index.html`
- `ui/static/styles.css`

### 4. Acceptance Criteria
- Page refresh restores conversation history and citation badges.
- Clicking "New Consultation" resets state cleanly.
- Textarea auto-expands on typing and shrinks back upon message send.
- Drawer works smoothly across desktop and mobile screen sizes.

### 5. Verification Steps
1. Load `http://127.0.0.1:8000`.
2. Ask a question, verify response renders.
3. Refresh the browser (`F5`) $\rightarrow$ verify message and citation count persist.
4. Press `Ctrl+K` $\rightarrow$ verify session resets to empty hero state.

---

## Step 3: Interactive Human Verification Walkthrough

### 1. Description
A guided manual verification protocol that the user (or reviewer) performs in their browser to validate typography, colors, animations, and clinical responsiveness.

### 2. Guided Checklist
| Step | Action | Expected Result | Pass/Fail |
|---|---|---|---|
| **3.1** | Open `http://127.0.0.1:8000` | Crisp light background (`#f8fafc`), pure white cards, WhatsApp Roboto font (`#111b21`), quota badge shows `⚡ 50/50 left`. | [ ] |
| **3.2** | Click card *"Thyroid Hormone Synthesis"* | Hero fades out, typing indicator bounces, answer generates with structured bullets, clinical pearl callout, and tool pills. | [ ] |
| **3.3** | Click citation pill `[1]` | Right-side Evidence Drawer slides in showing book title, printed page, and raw textbook passage. | [ ] |
| **3.4** | Click `✕` or backdrop | Drawer closes smoothly. | [ ] |
| **3.5** | Click follow-up chip `✨ Explore Related...` | Instantly fires the follow-up question without typing. Answer appears with cumulative citations (`[3]`, `[4]`). | [ ] |
| **3.6** | Check quota badge | Badge reflects updated count (e.g. `⚡ 48/50 left`). | [ ] |
| **3.7** | Select book from dropdown | Select *"Williams Textbook of Endocrinology"*, ask question $\rightarrow$ verify answer cites only Williams volume. | [ ] |

---

## Step 4: Final Sign-off & Production Deployment

### 1. Description
After Steps 1, 2, and 3 pass:
- Clean up test files in `temp/`.
- Git commit final polished UI updates and push to GitHub.
- Trigger deployment to Google Cloud Run or Render.

---

## Approval Gate

> [!IMPORTANT]
> Awaiting user review and approval of this plan. Upon approval, we will immediately execute **Step 1 (Automated End-to-End UX Test Suite)**.

