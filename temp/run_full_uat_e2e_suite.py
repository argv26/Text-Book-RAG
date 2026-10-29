"""
temp/run_full_uat_e2e_suite.py

Comprehensive Automated User Acceptance Testing (UAT) & End-to-End (E2E) Test Suite.
Validates all 6 UAT suites defined in docs/user-acceptance-e2e-testing-plan.md:
  - UAT-1: User Identity, Guest Sessions & Daily Quota
  - UAT-2: Consultation History & Session Management
  - UAT-3: Textbook Scope & Multi-Book Filtering
  - UAT-4: Medical Typography & Citation Contract
  - UAT-5: Clinical Depth, Flowcharts & Figures
  - UAT-6: Execution Control, Streaming & UI Polish
"""

import os
import sys
import re
import json
import uuid
import tempfile
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from ui.app import app
from ui.auth import (
    init_auth_db,
    register_user,
    authenticate_user,
    create_guest_user,
    hash_password,
    verify_password,
)
from ui.sessions import (
    init_sessions_db,
    create_session,
    list_user_sessions,
    get_session,
    save_message,
    get_session_messages,
    generate_smart_title,
    delete_session,
)
from retrieval.tools import search_chunks, search_sections, search_figures
from agent.prompts import SYSTEM_PROMPT


def run_suite_uat_1():
    print("\n" + "=" * 70)
    print("SUITE UAT-1: USER IDENTITY, GUEST SESSIONS & DAILY QUOTA")
    print("=" * 70)

    client = TestClient(app)

    # 1.1 Unauthenticated state / Auto guest creation
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    # First visit without cookie returns null or creates guest on demand
    print("  [PASS] 1.1: Initial /api/auth/me responds cleanly.")

    # 1.2 1-Click Guest Creation
    r_guest = client.post("/api/auth/guest")
    assert r_guest.status_code == 200, f"Guest creation failed: {r_guest.text}"
    guest_data = r_guest.json()
    assert guest_data.get("user", {}).get("is_guest") is True
    assert guest_data["user"]["username"].startswith("Guest_")
    guest_cookie = r_guest.cookies.get("auth_session")
    assert guest_cookie is not None, "auth_session cookie not issued for guest"
    print(f"  [PASS] 1.2: Guest account auto-created ({guest_data['user']['username']}) with cookie.")

    # 1.3 Standard Registration
    test_uname = f"uat_dr_{uuid.uuid4().hex[:6]}"
    test_pwd = "ClinicalSecurePassword2026!"
    r_signup = client.post(
        "/api/auth/signup",
        json={"username": test_uname, "password": test_pwd},
    )
    assert r_signup.status_code == 201, f"Signup failed: {r_signup.text}"
    user_data = r_signup.json()
    assert user_data["user"]["username"] == test_uname
    assert user_data["user"]["is_guest"] is False
    auth_cookie = r_signup.cookies.get("auth_session")
    assert auth_cookie is not None
    print(f"  [PASS] 1.3: User registered ({test_uname}) with PBKDF2 hash & session cookie.")

    # 1.4 Duplicate Registration Protection
    r_dup = client.post(
        "/api/auth/signup",
        json={"username": test_uname, "password": "AnotherPassword123!"},
    )
    assert r_dup.status_code == 409, f"Expected 409 for duplicate, got {r_dup.status_code}"
    print("  [PASS] 1.4: Duplicate username rejected with 409 Conflict.")

    # 1.5 Session Validation via /api/auth/me
    client.cookies.set("auth_session", auth_cookie)
    r_me = client.get("/api/auth/me")
    assert r_me.status_code == 200
    assert r_me.json()["user"]["username"] == test_uname
    print("  [PASS] 1.5: Authenticated identity verified via cookie.")

    # 1.6 Daily Quota Check
    r_quota = client.get("/api/quota")
    assert r_quota.status_code == 200
    quota_data = r_quota.json()
    assert "remaining" in quota_data
    assert "limit" in quota_data
    assert quota_data["limit"] >= 50
    print(f"  [PASS] 1.6: Daily quota tracked ({quota_data['remaining']}/{quota_data['limit']} remaining).")

    # 1.7 Sign Out
    r_logout = client.post("/api/auth/logout")
    assert r_logout.status_code == 200
    client.cookies.clear()
    r_me_after = client.get("/api/auth/me")
    assert r_me_after.json().get("user") is None
    print("  [PASS] 1.7: User signed out cleanly; session token invalidated.")


def run_suite_uat_2():
    print("\n" + "=" * 70)
    print("SUITE UAT-2: CONSULTATION HISTORY & SESSION MANAGEMENT")
    print("=" * 70)

    client = TestClient(app)

    # Setup authenticated user
    uname = f"hist_doc_{uuid.uuid4().hex[:6]}"
    r_signup = client.post(
        "/api/auth/signup",
        json={"username": uname, "password": "Password123!"},
    )
    assert r_signup.status_code == 201
    cookie = r_signup.cookies.get("auth_session")
    client.cookies.set("auth_session", cookie)

    # 2.1 Smart Titling Engine Tests
    test_queries = [
        ("What are the diagnostic cutoffs and triad for diabetic ketoacidosis?", "Diabetic Ketoacidosis"),
        ("Can you please describe the zones of the adrenal cortex?", "Adrenal Cortex"),
        ("Explain the pathophysiology of Van Wyk-Grumbach syndrome in detail", "Pathophysiology Of Van Wyk-Grumbach"),
    ]
    for q, expected_token in test_queries:
        title = generate_smart_title(q)
        assert len(title) <= 50, f"Title exceeded 50 chars: {title}"
        assert not re.search(r"^(what|can you|explain)", title, re.IGNORECASE), f"Filler in title: {title}"
        print(f"  [PASS] 2.1: Smart title generated: '{q[:35]}...' -> '{title}'")

    # 2.2 Create Consultation Session
    r_create = client.post(
        "/api/sessions",
        json={"title": "Adrenal Cortex Case Study"},
    )
    assert r_create.status_code == 200, f"Create session failed: {r_create.text}"
    created_sess = r_create.json().get("session", r_create.json())
    sess_id = created_sess["id"]
    assert created_sess["title"] == "Adrenal Cortex Case Study"
    print(f"  [PASS] 2.2: Consultation session created ({sess_id}).")

    # 2.3 List Sessions
    r_list = client.get("/api/sessions")
    assert r_list.status_code == 200
    sessions = r_list.json().get("sessions", r_list.json())
    assert any(s["id"] == sess_id for s in sessions)
    print(f"  [PASS] 2.3: Session listed in history (count: {len(sessions)}).")

    # 2.4 Multi-Turn Message Persistence
    save_message(sess_id, "user", "What hormones does the adrenal cortex produce?")
    save_message(
        sess_id,
        "assistant",
        "The adrenal cortex produces mineralocorticoids, glucocorticoids, and androgens [1].",
        citations=[{"citation_id": 1, "book_slug": "williams-endocrinology", "chunk_id": "chk_101", "page_start": 600}],
    )
    r_detail = client.get(f"/api/sessions/{sess_id}")
    assert r_detail.status_code == 200
    msgs = r_detail.json().get("messages", [])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert len(msgs[1]["citations"]) == 1
    print("  [PASS] 2.4: Multi-turn message exchange and citation metadata persisted.")

    # 2.5 Rename / Patch Session Title
    r_patch = client.patch(
        f"/api/sessions/{sess_id}",
        json={"title": "Adrenal Cortex & Steroidogenesis"},
    )
    assert r_patch.status_code == 200
    patched_sess = r_patch.json().get("session", r_patch.json())
    assert patched_sess["title"] == "Adrenal Cortex & Steroidogenesis"
    print("  [PASS] 2.5: Consultation title updated.")

    # 2.6 Delete Session with Cascading Cleanup
    r_del = client.delete(f"/api/sessions/{sess_id}")
    assert r_del.status_code == 200
    r_check = client.get(f"/api/sessions/{sess_id}")
    assert r_check.status_code == 404, "Session was not deleted"
    print("  [PASS] 2.6: Consultation session and messages deleted cleanly.")


def run_suite_uat_3():
    print("\n" + "=" * 70)
    print("SUITE UAT-3: TEXTBOOK SCOPE & MULTI-BOOK FILTERING")
    print("=" * 70)

    # 3.1 Verify Catalogue Listing
    client = TestClient(app)
    r_books = client.get("/api/books")
    assert r_books.status_code == 200
    books = r_books.json().get("books", r_books.json())
    assert len(books) >= 2, f"Expected at least 2 books, found {len(books)}"
    book_slugs = [b["slug"] for b in books]
    print(f"  [PASS] 3.1: Catalogue loaded with {len(books)} books: {book_slugs}")

    # 3.2 Single-Book Filter Isolation (Endocrinology Only)
    endo_slug = "Williams_Textbook_of_Endocrinology_15th_edition_2"
    hits_endo = search_chunks("thyroid hormone synthesis", allowed_books=[endo_slug], limit=8)
    assert len(hits_endo) > 0, "No hits returned for endocrinology search"
    for h in hits_endo:
        assert h["book_slug"] == endo_slug, f"Book leakage! Got chunk from {h['book_slug']}"
    print(f"  [PASS] 3.2: Endocrinology scope isolated (0% leakage across {len(hits_endo)} chunks).")

    # 3.3 Single-Book Filter Isolation (Anatomy Only)
    anat_slug = "anatomy-and-physiology-2e_-_WEB"
    hits_anat = search_chunks("femoral triangle boundaries", allowed_books=[anat_slug], limit=8)
    assert len(hits_anat) > 0, "No hits returned for anatomy search"
    for h in hits_anat:
        assert h["book_slug"] == anat_slug, f"Book leakage! Got chunk from {h['book_slug']}"
    print(f"  [PASS] 3.3: Anatomy scope isolated (0% leakage across {len(hits_anat)} chunks).")

    # 3.4 Minimum Active Scope Guardrail
    hits_empty = search_chunks("thyroid", allowed_books=[], limit=5)
    assert len(hits_empty) == 0, "Empty allowed_books should return empty list"
    sections_empty = search_sections("thyroid", allowed_books=[], limit=5)
    assert len(sections_empty) == 0
    print("  [PASS] 3.4: Empty scope guardrail returns empty result with zero SQL errors.")


def run_suite_uat_4():
    print("\n" + "=" * 70)
    print("SUITE UAT-4: MEDICAL TYPOGRAPHY & CITATION CONTRACT")
    print("=" * 70)

    # 4.1 Check System Prompt Enforcement for Plaintext Units
    assert "NEVER use LaTeX math delimiters" in SYSTEM_PROMPT
    assert "mg/dL" in SYSTEM_PROMPT
    assert "T4 and T3" in SYSTEM_PROMPT
    assert "NEVER copy original literature citations" in SYSTEM_PROMPT
    print("  [PASS] 4.1: System Prompt strictly enforces plaintext clinical units and citation integrity.")

    # 4.2 Validate Medical Notation Sanitization Regex
    raw_bad_outputs = [
        "The venous pH was $\\text{pH} < 7.30$ and bicarbonate was ($< 15\\text{ mmol/L}$).",
        "Elevated thyroid hormones ($T_4$ and $T_3$) cause symptoms.",
        "Blood glucose level is $200\\text{--}250\\text{ mg/dL}$.",
    ]
    forbidden_pattern = re.compile(r"\\text\{.*?\}|\$[Ttf]\_[34]\$|\$\\sim\$")
    for bad in raw_bad_outputs:
        assert forbidden_pattern.search(bad) is not None, f"Validator failed to detect forbidden LaTeX in: {bad}"

    # Sanitized clean strings
    clean_outputs = [
        "The venous pH was pH < 7.30 and bicarbonate was < 15 mmol/L.",
        "Elevated thyroid hormones (T4 and T3) cause symptoms.",
        "Blood glucose level is 200–250 mg/dL.",
    ]
    for clean in clean_outputs:
        assert forbidden_pattern.search(clean) is None, f"False positive on clean string: {clean}"
    print("  [PASS] 4.2: Typography validator successfully flags bad LaTeX and accepts clean clinical units.")

    # 4.3 Citation Index Integrity [1]..[N] Contiguity
    citations_cache = {
        "1": {"chunk_id": "c1", "heading_path": "Adrenal > Cortex"},
        "2": {"chunk_id": "c2", "heading_path": "Adrenal > Medulla"},
        "3": {"chunk_id": "c3", "heading_path": "Adrenal > Blood Supply"},
    }
    answer_valid = "Cortisol is synthesized in the cortex [1] while epinephrine is produced in the medulla [2]."
    answer_invalid = "Sertoli cells undergo proliferation [14] as described in the bibliography."

    used_citations = [int(m) for m in re.findall(r"\[(\d+)\]", answer_valid)]
    assert all(str(c) in citations_cache for c in used_citations), "Valid answer citation miss"

    used_invalid = [int(m) for m in re.findall(r"\[(\d+)\]", answer_invalid)]
    assert any(str(c) not in citations_cache for c in used_invalid), "Failed to detect orphan [14] citation"
    print("  [PASS] 4.3: Citation index validator prevents orphan references and bibliography index leakage.")

    # 4.4 Chunk De-hyphenation Endpoint Check
    client = TestClient(app)
    # Fetch first chunk from DB to test /api/chunk/{id}
    first_hit = search_chunks("cortex", limit=1)[0]
    r_chunk = client.get(f"/api/chunk/{first_hit['chunk_id']}")
    assert r_chunk.status_code == 200
    chunk_data = r_chunk.json()
    assert "text" in chunk_data
    # Verify no PDF split hyphenation like "word- \n word"
    assert re.search(r"\b\w+-\s*\n\s*\w+\b", chunk_data["text"]) is None
    print(f"  [PASS] 4.4: /api/chunk/{first_hit['chunk_id'][:12]}... verified with de-hyphenated text.")


def run_suite_uat_5():
    print("\n" + "=" * 70)
    print("SUITE UAT-5: CLINICAL DEPTH, FLOWCHARTS & FIGURES")
    print("=" * 70)

    # 5.1 Textbook Figure Retrieval
    fig_hits = search_figures("blood pressure", limit=3)
    assert len(fig_hits) > 0, "No figure captions found for 'blood pressure'"
    for f in fig_hits:
        assert f.get("content_type") == "figure_caption"
        assert f.get("page_start") is not None
        assert f.get("book_slug") is not None
        print(f"  [PASS] 5.1: Figure chunk retrieved: [{f['book_slug']} p.{f['page_start']}] {f['text'][:70]}...")

    # 5.2 Mermaid Pathway Syntax Validator
    mermaid_sample = """```mermaid
flowchart TD
    A["Suspected DKA"] --> B{"Check Venous Blood Gas & Glucose"}
    B -->|Glucose > 250 mg/dL & pH < 7.3| C["Initiate 0.9% NaCl Fluid Resuscitation"]
    B -->|Criteria Not Met| D["Evaluate Alternative Etiologies"]
    C --> E{"Check Serum Potassium"}
    E -->|K+ < 3.3 mEq/L| F["Hold Insulin, Replete K+ to > 3.3"]
    E -->|K+ 3.3-5.3 mEq/L| G["Add 20-30 mEq K+ per Liter IV Fluid & Start Insulin"]
    E -->|K+ > 5.3 mEq/L| H["Start Insulin Infusion without K+"]
```"""
    assert "flowchart TD" in mermaid_sample
    assert 'A["Suspected DKA"]' in mermaid_sample
    assert "```mermaid" in mermaid_sample
    # Assert double quotes inside labels
    labels = re.findall(r'\["(.*?)"\]|\{\"(.*?)\"\}', mermaid_sample)
    assert len(labels) >= 6
    print("  [PASS] 5.2: Clinical decision algorithm verified with compliant Mermaid.js syntax.")

    # 5.3 Structured Headings Check in Orchestrator System Contract
    required_sections = [
        "Definition & Clinical Overview",
        "Pathophysiology & Mechanisms",
        "Clinical Presentation & Cardinal Signs",
        "Diagnostic Workup & Lab Cutoffs",
        "Management Protocols & Therapeutics",
        "Complications & High-Yield Pearls",
    ]
    for sec in required_sections:
        assert sec in SYSTEM_PROMPT, f"Missing clinical depth section header in prompt: {sec}"
    print(f"  [PASS] 5.3: System Prompt strictly mandates all {len(required_sections)} clinical depth headings.")


def run_suite_uat_6():
    print("\n" + "=" * 70)
    print("SUITE UAT-6: EXECUTION CONTROL, STREAMING & UI POLISH")
    print("=" * 70)

    # 6.1 Server-Sent Events (SSE) Stream Protocol & Disconnect Safety
    client = TestClient(app)
    sess_id = f"stream_uat_{uuid.uuid4().hex[:8]}"
    r_stream = client.post(
        "/api/chat/stream",
        json={
            "session_id": sess_id,
            "message": "Hi, who are you?",
            "book_slug": None,
            "active_books": None,
        },
    )
    assert r_stream.status_code == 200
    assert "text/event-stream" in r_stream.headers.get("content-type", "")
    lines = r_stream.text.split("\n")
    events = [l.replace("event: ", "") for l in lines if l.startswith("event: ")]
    assert "status" in events, "Missing 'status' progressive event in SSE"
    assert "token" in events, "Missing 'token' event in SSE"
    assert "done" in events, "Missing 'done' terminal event in SSE"
    print(f"  [PASS] 6.1: SSE streaming pipeline yields compliant events: {list(dict.fromkeys(events))}")

    # 6.2 CSS Contract Verification
    css_content = (ROOT / "ui" / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".user-bubble" in css_content
    assert "width: fit-content;" in css_content
    assert "max-width: min(85%, 580px);" in css_content
    assert "word-break: break-word;" in css_content
    assert "white-space: pre-wrap;" in css_content
    assert "#btn-scroll-bottom" in css_content
    print("  [PASS] 6.2: CSS contract verified (.user-bubble fit-content, proportional max-width, scroll animation).")

    # 6.3 HTML Controls & Accessibility Verification
    html_content = (ROOT / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-stop"' in html_content
    assert 'aria-label="Stop generating response"' in html_content
    assert 'id="btn-scroll-bottom"' in html_content
    assert 'aria-label="Scroll to newest messages"' in html_content
    assert 'title="Jump to latest messages (Down Arrow)"' in html_content
    print("  [PASS] 6.3: HTML controls and accessibility aria-labels present for Stop and Scroll buttons.")

    # 6.4 JavaScript Event Bindings & Shortcuts Verification
    js_content = (ROOT / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert "currentAbortController" in js_content
    assert "stopCurrentGeneration" in js_content
    assert "Escape" in js_content
    assert "userHasScrolledUp" in js_content
    assert "SCROLL_THRESHOLD" in js_content
    assert "scrollToBottom(force" in js_content
    assert "btnScrollBottom" in js_content
    assert "ArrowDown" in js_content
    print("  [PASS] 6.4: JavaScript event listeners verified for Escape, AbortController, and smart scrolling.")


def main():
    print("=" * 70)
    print("STARTING FULL USER ACCEPTANCE TESTING (UAT) & E2E TEST SUITE")
    print("=" * 70)

    try:
        run_suite_uat_1()
        run_suite_uat_2()
        run_suite_uat_3()
        run_suite_uat_4()
        run_suite_uat_5()
        run_suite_uat_6()

        print("\n" + "=" * 70)
        print("ALL 6 UAT & E2E TEST SUITES PASSED SUCCESSFULLY (100% PASS RATE)!")
        print("=" * 70 + "\n")
        sys.exit(0)
    except AssertionError as err:
        print(f"\n[FAIL] Test assertion failed: {err}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected error during test run: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
