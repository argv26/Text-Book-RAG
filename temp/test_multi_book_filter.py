"""Automated verification test for Sub-Step 3.1: Multi-Book Filtering in Retrieval Engine & Tools."""
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from retrieval.engine import SqliteEngine
from retrieval.tools import search_chunks, search_sections


def test_multi_book_filtering():
    print("=================================================================")
    print("Testing Multi-Book Filtering in Retrieval Engine and Tools")
    print("=================================================================")

    engine = SqliteEngine()

    ENDOCRINOLOGY = "Williams_Textbook_of_Endocrinology_15th_edition_2"
    ANATOMY = "anatomy-and-physiology-2e_-_WEB"

    # 1. Test search_chunks with allowed_books restricted to Endocrinology
    # "femur" is heavily present in Anatomy. Restricting to Endocrinology must return 0 Anatomy chunks.
    endo_chunks = engine.search_chunks(query="femur", allowed_books=[ENDOCRINOLOGY], limit=10)
    for c in endo_chunks:
        assert c.book_slug == ENDOCRINOLOGY, f"Leaked chunk from {c.book_slug} when restricted to {ENDOCRINOLOGY}"
    print(f"[PASS] Allowed books [{ENDOCRINOLOGY}] returned {len(endo_chunks)} chunks, 0 leaked from Anatomy.")

    # 2. Test search_chunks with allowed_books restricted to Anatomy
    # "aldosterone" is an endocrine hormone. Restricting to Anatomy must return 0 Endocrinology chunks.
    anat_chunks = engine.search_chunks(query="aldosterone", allowed_books=[ANATOMY], limit=10)
    for c in anat_chunks:
        assert c.book_slug == ANATOMY, f"Leaked chunk from {c.book_slug} when restricted to {ANATOMY}"
    print(f"[PASS] Allowed books [{ANATOMY}] returned {len(anat_chunks)} chunks, 0 leaked from Endocrinology.")

    # 3. Test empty list allowed_books=[]
    empty_chunks = engine.search_chunks(query="thyroid", allowed_books=[], limit=5)
    assert empty_chunks == [], f"Expected empty list, got {empty_chunks}"
    empty_sections = engine.search_sections(query="thyroid", allowed_books=[], limit=5)
    assert empty_sections == [], f"Expected empty list, got {empty_sections}"
    print("[PASS] Empty allowed_books=[] returns empty list immediately without SQL errors.")

    # 4. Test default allowed_books=None (should return hits across volumes)
    all_chunks = engine.search_chunks(query="cell", allowed_books=None, limit=10)
    assert len(all_chunks) > 0, "Expected chunks across books"
    print(f"[PASS] Default allowed_books=None returned {len(all_chunks)} chunks.")

    # 5. Test search_sections with allowed_books
    sec_endo = engine.search_sections(query="adrenal", allowed_books=[ANATOMY], limit=5)
    for s in sec_endo:
        assert s.book_slug == ANATOMY, f"Leaked section from {s.book_slug}"
    print(f"[PASS] Sections restricted to [{ANATOMY}] returned {len(sec_endo)} sections, 0 leaked from Endocrinology.")

    # 6. Test high-level retrieval/tools.py functions
    tool_chunks = search_chunks(query="thyroid", allowed_books=[ENDOCRINOLOGY], limit=5)
    for c in tool_chunks:
        assert c["book_slug"] == ENDOCRINOLOGY, f"Tool leaked chunk from {c['book_slug']}"
    print(f"[PASS] retrieval.tools.search_chunks correctly enforces allowed_books filter.")

    tool_secs = search_sections(query="pituitary", allowed_books=[ANATOMY], limit=5)
    for s in tool_secs:
        assert s["book_slug"] == ANATOMY, f"Tool leaked section from {s['book_slug']}"
    print(f"[PASS] retrieval.tools.search_sections correctly enforces allowed_books filter.")

    print("\nALL MULTI-BOOK FILTERING VERIFICATION CHECKS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    test_multi_book_filtering()
