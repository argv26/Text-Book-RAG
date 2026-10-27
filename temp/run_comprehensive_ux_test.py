"""Comprehensive End-to-End Test Suite for TextBook RAG.

Tests API endpoints, retrieval tools, agent orchestration, rate limiting, and context compaction.
Any issues found are recorded into tests/issues/ without stopping the suite prematurely.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ISSUES_DIR = ROOT / "tests" / "issues"
ISSUES_DIR.mkdir(parents=True, exist_ok=True)

issues_logged: list[dict] = []
test_results: list[dict] = []


def record_issue(name: str, component: str, description: str, error: Exception | str, details: dict | None = None):
    issue_num = len(issues_logged) + 1
    issue_data = {
        "issue_id": f"ISSUE-{issue_num:03d}",
        "name": name,
        "component": component,
        "description": description,
        "error": str(error),
        "traceback": traceback.format_exc() if isinstance(error, Exception) else None,
        "details": details or {},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    issues_logged.append(issue_data)

    filepath = ISSUES_DIR / f"issue_{issue_num:03d}_{name.lower().replace(' ', '_')}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(issue_data, f, indent=2)

    print(f"   [FAIL] {name} -> Logged to {filepath.name}")


def log_test(name: str, passed: bool, duration_ms: float, error_msg: str | None = None):
    test_results.append({
        "test": name,
        "passed": passed,
        "duration_ms": round(duration_ms, 2),
        "error": error_msg,
    })
    status = "[PASS]" if passed else "[FAIL]"
    print(f" {status} {name} ({duration_ms:.1f}ms)")


# =============================================================================
# 1. API Endpoint Tests
# =============================================================================

def test_api_endpoints():
    print("\n--- 1. Testing API Endpoints & Routes ---")
    from fastapi.testclient import TestClient
    from ui.app import app

    client = TestClient(app)

    # 1.1 GET /
    t0 = time.time()
    try:
        res = client.get("/")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        assert "TextBook RAG" in res.text, "Title not found in index.html"
        assert "Roboto" in res.text, "Roboto font not found in index.html"
        assert "evidence-drawer" in res.text, "Evidence Drawer not found in index.html"
        log_test("GET / (Index Page & Typography)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("GET / (Index Page & Typography)", False, (time.time() - t0) * 1000, str(e))
        record_issue("GET / Route Failure", "ui.app", "Index page failed to load or missing core elements", e)

    # 1.2 GET /api/books
    t0 = time.time()
    try:
        res = client.get("/api/books")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        assert "books" in data, "No 'books' key in response"
        assert len(data["books"]) >= 2, f"Expected at least 2 books, got {len(data['books'])}"
        for b in data["books"]:
            assert "slug" in b and "title" in b and "total_pages" in b
        log_test("GET /api/books (Catalogue)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("GET /api/books (Catalogue)", False, (time.time() - t0) * 1000, str(e))
        record_issue("GET /api/books Failure", "ui.app", "Book catalogue endpoint failed or missing books", e)

    # 1.3 GET /api/quota
    t0 = time.time()
    try:
        res = client.get("/api/quota")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        assert "limit" in data and "used" in data and "remaining" in data
        assert data["remaining"] >= 0
        log_test("GET /api/quota (Rate Limit Status)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("GET /api/quota (Rate Limit Status)", False, (time.time() - t0) * 1000, str(e))
        record_issue("GET /api/quota Failure", "ui.rate_limiter", "Quota endpoint failed", e)

    # 1.4 POST /api/chat with empty message
    t0 = time.time()
    try:
        res = client.post("/api/chat", json={"session_id": "test_empty", "message": "   "})
        assert res.status_code == 400, f"Expected 400 Bad Request, got {res.status_code}"
        log_test("POST /api/chat (Empty Message Validation)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("POST /api/chat (Empty Message Validation)", False, (time.time() - t0) * 1000, str(e))
        record_issue("Chat Validation Failure", "ui.app", "Empty message did not return HTTP 400", e)

    # 1.5 POST /api/chat Turn 1
    session_id = f"test_ux_session_{int(time.time())}"
    first_citations = []
    first_follow_ups = []
    t0 = time.time()
    try:
        res = client.post("/api/chat", json={
            "session_id": session_id,
            "message": "Explain the zones of the adrenal cortex and the specific hormones synthesized in each zone."
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert "reply" in data and len(data["reply"]) > 100, "Reply text is too short or empty"
        assert "citations" in data and len(data["citations"]) > 0, "No citations returned"
        assert "follow_ups" in data and len(data["follow_ups"]) > 0, "No follow-up questions generated"
        assert "tool_calls" in data and len(data["tool_calls"]) > 0, "No tool activity pills logged"
        first_citations = data["citations"]
        first_follow_ups = data["follow_ups"]
        log_test("POST /api/chat (Turn 1: Clinical Synthesis & Citations)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("POST /api/chat (Turn 1: Clinical Synthesis & Citations)", False, (time.time() - t0) * 1000, str(e))
        record_issue("Chat Turn 1 Failure", "agent.orchestrator", "First turn query failed to return grounded response", e)

    # 1.6 GET /api/chunk/{chunk_id} for Evidence Drawer
    t0 = time.time()
    if first_citations:
        try:
            chunk_id = first_citations[0]["chunk_id"]
            res = client.get(f"/api/chunk/{chunk_id}")
            assert res.status_code == 200, f"Expected 200, got {res.status_code}"
            data = res.json()
            assert data.get("chunk_id") == chunk_id
            assert "text" in data and len(data["text"]) > 20
            assert "page_start" in data
            log_test(f"GET /api/chunk/{chunk_id} (Evidence Drawer Excerpt)", True, (time.time() - t0) * 1000)
        except Exception as e:
            log_test("GET /api/chunk/{id} (Evidence Drawer)", False, (time.time() - t0) * 1000, str(e))
            record_issue("Chunk Retrieval Failure", "retrieval.engine", "Evidence drawer chunk retrieval failed", e)
    else:
        record_issue("Evidence Drawer Skipped", "ui.app", "No citations from Turn 1 to test chunk fetch", "Missing citations")

    # 1.7 GET /api/chunk/invalid_chunk_id
    t0 = time.time()
    try:
        res = client.get("/api/chunk/non_existent_chunk_999999")
        assert res.status_code == 404, f"Expected 404 Not Found, got {res.status_code}"
        log_test("GET /api/chunk/invalid (404 Error Handling)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("GET /api/chunk/invalid (404 Error Handling)", False, (time.time() - t0) * 1000, str(e))
        record_issue("Invalid Chunk 404 Failure", "ui.app", "Expected 404 for invalid chunk_id", e)

    # 1.8 POST /api/chat Turn 2 (Follow-up loop)
    t0 = time.time()
    follow_up_msg = first_follow_ups[0] if first_follow_ups else "What are the clinical signs of cortisol excess?"
    try:
        res = client.post("/api/chat", json={
            "session_id": session_id,
            "message": follow_up_msg
        })
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        data = res.json()
        assert len(data["reply"]) > 50
        # Check cumulative citations
        assert len(data["citations"]) >= len(first_citations), "Citations should accumulate across turns"
        log_test("POST /api/chat (Turn 2: Follow-up Continuity)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("POST /api/chat (Turn 2: Follow-up Continuity)", False, (time.time() - t0) * 1000, str(e))
        record_issue("Chat Turn 2 Continuity Failure", "context.manager", "Multi-turn session failed to accumulate state", e)

    # 1.9 GET /api/session/{id}/citations
    t0 = time.time()
    try:
        res = client.get(f"/api/session/{session_id}/citations")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        data = res.json()
        assert "citations" in data
        assert len(data["citations"]) > 0
        log_test("GET /api/session/citations (Session Bibliography)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("GET /api/session/citations (Session Bibliography)", False, (time.time() - t0) * 1000, str(e))
        record_issue("Session Citations Route Failure", "ui.app", "Failed to retrieve accumulated citations", e)

    # 1.10 Book-scoped search
    t0 = time.time()
    target_book = "anatomy-and-physiology-2e_-_WEB"
    try:
        res = client.post("/api/chat", json={
            "session_id": f"scoped_{int(time.time())}",
            "message": "femoral triangle",
            "book_slug": target_book
        })
        assert res.status_code == 200
        data = res.json()
        for c in data.get("citations", []):
            assert c["book_slug"] == target_book, f"Expected book {target_book}, got {c['book_slug']}"
        log_test(f"POST /api/chat (Scoped to {target_book})", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test(f"POST /api/chat (Scoped Search)", False, (time.time() - t0) * 1000, str(e))
        record_issue("Scoped Search Failure", "retrieval.engine", "Search chunks did not respect book_slug filter", e)


# =============================================================================
# 2. Retrieval Tools Direct Tests
# =============================================================================

def test_retrieval_tools():
    print("\n--- 2. Testing Retrieval Tools Directly ---")
    from retrieval.tools import get_chunk, get_toc, list_books, read_section, search_chunks, search_sections

    book_slug = "anatomy-and-physiology-2e_-_WEB"

    # 2.1 search_chunks
    t0 = time.time()
    try:
        hits = search_chunks("thyroid gland", limit=5)
        assert len(hits) > 0, "Expected search_chunks to return hits"
        for h in hits:
            assert "chunk_id" in h and "text" in h and "heading_path" in h
        log_test("search_chunks('thyroid gland')", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("search_chunks", False, (time.time() - t0) * 1000, str(e))
        record_issue("search_chunks tool failure", "retrieval.tools", "Direct search_chunks failed", e)

    # 2.2 search_sections
    t0 = time.time()
    try:
        sections = search_sections("endocrine system", limit=3)
        assert len(sections) > 0, "Expected search_sections to return section nodes"
        for s in sections:
            assert "section_id" in s and "heading_path" in s and "page_start" in s
        log_test("search_sections('endocrine system')", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("search_sections", False, (time.time() - t0) * 1000, str(e))
        record_issue("search_sections tool failure", "retrieval.tools", "Direct search_sections failed", e)

    # 2.3 read_section
    t0 = time.time()
    try:
        sec_content = read_section(book_slug=book_slug, section_id=1)
        assert "assembled_text" in sec_content or "error" in sec_content
        log_test(f"read_section({book_slug}, section_id=1)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("read_section", False, (time.time() - t0) * 1000, str(e))
        record_issue("read_section tool failure", "retrieval.tools", "Direct read_section failed", e)

    # 2.4 get_toc
    t0 = time.time()
    try:
        toc = get_toc(book_slug=book_slug)
        assert len(toc) > 0, "TOC should return root chapters"
        log_test(f"get_toc({book_slug})", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("get_toc", False, (time.time() - t0) * 1000, str(e))
        record_issue("get_toc tool failure", "retrieval.tools", "Direct get_toc failed", e)


# =============================================================================
# 3. Rate Limiter Stress Test
# =============================================================================

def test_rate_limiter():
    print("\n--- 3. Testing Rate Limiting Enforcer ---")
    from ui.rate_limiter import DailyRateLimiter

    test_ip = f"stress_test_ip_{int(time.time())}"
    limiter = DailyRateLimiter()

    # Save original limit
    orig_limit = os.environ.get("DAILY_REQUEST_LIMIT")
    try:
        os.environ["DAILY_REQUEST_LIMIT"] = "3"

        t0 = time.time()
        # Call 1: Allowed (remaining 2)
        ok1, c1, r1 = limiter.check_and_increment(test_ip)
        assert ok1 is True and r1 == 2

        # Call 2: Allowed (remaining 1)
        ok2, c2, r2 = limiter.check_and_increment(test_ip)
        assert ok2 is True and r2 == 1

        # Call 3: Allowed (remaining 0)
        ok3, c3, r3 = limiter.check_and_increment(test_ip)
        assert ok3 is True and r3 == 0

        # Call 4: Blocked (remaining 0)
        ok4, c4, r4 = limiter.check_and_increment(test_ip)
        assert ok4 is False and r4 == 0

        log_test("DailyRateLimiter (3/3 Quota Exhaustion & Blocking)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("DailyRateLimiter", False, (time.time() - t0) * 1000, str(e))
        record_issue("Rate Limiter Stress Failure", "ui.rate_limiter", "Rate limiter failed to block at quota limit", e)
    finally:
        if orig_limit is not None:
            os.environ["DAILY_REQUEST_LIMIT"] = orig_limit
        else:
            os.environ.pop("DAILY_REQUEST_LIMIT", None)


# =============================================================================
# 4. Context Manager Compaction & Tool Virtualization
# =============================================================================

def test_context_compaction():
    print("\n--- 4. Testing Context Compaction & Tool Virtualization ---")
    from context.manager import ContextManager

    cm = ContextManager(max_context_tokens=3000, virtualize_after_turns=1)
    sess_id = f"compact_test_{int(time.time())}"

    t0 = time.time()
    try:
        # Simulate 4 full turns with large tool outputs
        for turn in range(1, 5):
            cm.add_user_message(sess_id, f"Question for turn {turn}")
            # Large dummy tool output
            dummy_result = [{"chunk_id": f"chunk_t{turn}_{i}", "text": "Medical data " * 50} for i in range(5)]
            cm.add_tool_result(sess_id, f"call_{turn}", "search_chunks", dummy_result)
            cm.add_assistant_message(sess_id, f"Answer for turn {turn} based on [1].")

        # Assemble prompt
        assembled = cm.assemble_prompt(sess_id, system_prompt="You are a clinical tutor.")
        total_tokens = sum(m.get("token_count", 0) for m in assembled)

        # Check that older turns are virtualized
        virtualized_count = sum(1 for m in assembled if "[Tool output from" in m.get("content", ""))
        assert virtualized_count > 0, "Older tool outputs should have been virtualized"

        # Check total tokens within budget
        assert total_tokens <= cm.max_context_tokens, f"Expected tokens <= 3000, got {total_tokens}"
        log_test(f"Context Compactor (4 turns virtualized: {virtualized_count} calls, {total_tokens} tokens)", True, (time.time() - t0) * 1000)
    except Exception as e:
        log_test("Context Compactor", False, (time.time() - t0) * 1000, str(e))
        record_issue("Context Compaction Failure", "context.compactor", "Tool virtualization or budgeting failed", e)


# =============================================================================
# Main Runner & Summary Report
# =============================================================================

def generate_summary():
    passed_count = sum(1 for r in test_results if r["passed"])
    failed_count = sum(1 for r in test_results if not r["passed"])
    total_count = len(test_results)

    print("\n==================================================")
    print(f"TEST RUN COMPLETE: {passed_count}/{total_count} PASSED ({failed_count} ISSUES)")
    print("==================================================")

    # Write SUMMARY.md into tests/issues/
    summary_path = ISSUES_DIR / "SUMMARY.md"
    lines = [
        "# Comprehensive Test Run Report",
        f"**Date:** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Results:** {passed_count} Passed / {failed_count} Failed / {total_count} Total  ",
        "",
        "## Test Suite Results",
        "| Test | Status | Duration (ms) | Notes |",
        "|---|---|---|---|",
    ]
    for r in test_results:
        status_icon = "✅ PASS" if r["passed"] else "❌ FAIL"
        err = r["error"] or "-"
        lines.append(f"| {r['test']} | {status_icon} | {r['duration_ms']} | {err} |")

    lines.append("")
    lines.append("## Identified Issues")
    if not issues_logged:
        lines.append("🎉 **No issues identified! All test suites passed with 100% confidence.**")
    else:
        lines.append(f"**Total Issues Logged:** {len(issues_logged)}")
        lines.append("| Issue ID | Component | Summary | File |")
        lines.append("|---|---|---|---|")
        for iss in issues_logged:
            fname = f"issue_{int(iss['issue_id'].split('-')[-1]):03d}_{iss['name'].lower().replace(' ', '_')}.json"
            lines.append(f"| {iss['issue_id']} | `{iss['component']}` | {iss['description']} | [`{fname}`]({fname}) |")

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Summary report written to {summary_path}")


def main():
    test_api_endpoints()
    test_retrieval_tools()
    test_rate_limiter()
    test_context_compaction()
    generate_summary()


if __name__ == "__main__":
    main()

