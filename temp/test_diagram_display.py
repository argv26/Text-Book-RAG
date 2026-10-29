"""
Automated Verification Suite for Textbook Diagram Extraction & UI Display
Tests:
1. ui.diagrams.get_figure_image_path on real figure chunk
2. Caching behavior of get_figure_image_path
3. Error handling on unknown chunk
4. FastAPI GET /api/figure/{chunk_id}/image endpoint
5. HTML & JS integration checks for Lightbox and visual previews
"""

import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient
from ui.app import app
import ui.diagrams as diagrams

def run_tests():
    print("=" * 60)
    print("RUNNING DIAGRAM EXTRACTION & UI DISPLAY TEST SUITE")
    print("=" * 60)
    
    client = TestClient(app)
    
    # Test 1: Real figure chunk extraction
    print("\n[Test 1] Testing extraction for real figure chunk...")
    test_chunk_id = "anatomy-and-physiology-2e_-_WEB_s0003_c001"
    img_path = diagrams.get_figure_image_path(test_chunk_id)
    assert img_path is not None, f"Expected image path for {test_chunk_id}, got None"
    assert os.path.exists(img_path), f"File {img_path} does not exist"
    size = os.path.getsize(img_path)
    assert size > 1000, f"Expected image size > 1000 bytes, got {size}"
    
    with open(img_path, "rb") as f:
        header = f.read(3)
        assert header == b"\xff\xd8\xff", f"Expected JPEG header b'\\xff\\xd8\\xff', got {header}"
    print(f" PASS: Extracted {img_path} ({size:,} bytes, valid JPEG)")
    
    # Test 2: Cache retrieval
    print("\n[Test 2] Testing cache hit behavior...")
    cached_path = diagrams.get_figure_image_path(test_chunk_id)
    assert cached_path == img_path, "Cached path should match original path"
    print(f" PASS: Returned cached path instantly: {cached_path}")
    
    # Test 3: Unknown chunk handling
    print("\n[Test 3] Testing unknown chunk handling...")
    none_path = diagrams.get_figure_image_path("non_existent_chunk_12345")
    assert none_path is None, f"Expected None for unknown chunk, got {none_path}"
    print(" PASS: Successfully returned None for invalid chunk")
    
    # Test 4: FastAPI Endpoint
    print("\n[Test 4] Testing GET /api/figure/{chunk_id}/image endpoint...")
    resp = client.get(f"/api/figure/{test_chunk_id}/image")
    assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}"
    assert resp.headers.get("content-type") == "image/jpeg", f"Wrong content-type: {resp.headers.get('content-type')}"
    assert len(resp.content) == size, f"Response content length mismatch: {len(resp.content)} vs {size}"
    assert resp.headers.get("cache-control") == "public, max-age=86400"
    print(" PASS: GET /api/figure/.../image returned 200 OK with correct headers and JPEG bytes")
    
    # Test 5: 404 for invalid chunk
    print("\n[Test 5] Testing 404 for unknown figure chunk...")
    resp_404 = client.get("/api/figure/non_existent_chunk/image")
    assert resp_404.status_code == 404, f"Expected 404, got {resp_404.status_code}"
    print(" PASS: GET /api/figure/invalid/image correctly returned 404")
    
    # Test 6: Verify index.html contains Lightbox Modal DOM
    print("\n[Test 6] Checking index.html for Lightbox Modal elements...")
    index_html_path = PROJECT_ROOT / "ui" / "static" / "index.html"
    index_html = index_html_path.read_text(encoding="utf-8")
    assert 'id="figure-lightbox-modal"' in index_html, "Missing figure-lightbox-modal in index.html"
    assert 'id="lightbox-img"' in index_html, "Missing lightbox-img in index.html"
    assert 'id="lightbox-caption"' in index_html, "Missing lightbox-caption in index.html"
    assert 'id="btn-close-lightbox"' in index_html, "Missing btn-close-lightbox in index.html"
    print(" PASS: index.html contains all required Lightbox modal components")
    
    # Test 7: Verify app.js contains diagram & lightbox wiring
    print("\n[Test 7] Checking app.js for diagram rendering and modal logic...")
    app_js_path = PROJECT_ROOT / "ui" / "static" / "app.js"
    app_js = app_js_path.read_text(encoding="utf-8")
    assert 'openLightbox' in app_js, "Missing openLightbox in app.js"
    assert 'closeLightbox' in app_js, "Missing closeLightbox in app.js"
    assert 'openLightboxForChunk' in app_js, "Missing openLightboxForChunk in app.js"
    assert '/api/figure/' in app_js, "Missing /api/figure/ endpoint call in app.js"
    assert 'figure-card' in app_js, "Missing figure-card in app.js"
    assert 'showFigureInDrawer' in app_js, "Missing showFigureInDrawer in app.js"
    print(" PASS: app.js contains all required rendering and event listener logic")

    print("\n" + "=" * 60)
    print("ALL 7 TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
