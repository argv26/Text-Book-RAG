"""Unit tests for backend citation normalization and medical typography sanitization."""
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agent.orchestrator import _clean_chunk_prose, _sanitize_citations, _sanitize_medical_typography


def test_typography_sanitizer():
    # 1. LaTeX \text{} tags
    raw1 = r"Biochemical criteria include $\text{pH} < 7.3$ and bicarbonate ($< 15\text{ mmol/L}$)."
    out1 = _sanitize_medical_typography(raw1)
    assert r"\text" not in out1, f"Failed: found \\text in '{out1}'"
    assert "pH < 7.3" in out1, f"Failed: 'pH < 7.3' missing in '{out1}'"
    assert "< 15 mmol/L" in out1, f"Failed: '< 15 mmol/L' missing in '{out1}'"
    print("[PASS] Test 1: LaTeX \\text{} tags stripped cleanly.")

    # 2. Thyroid hormones
    raw2 = r"Serum levels of thyroid hormones ($T_4$ and $T_3$) and $fT_4$ are decreased."
    out2 = _sanitize_medical_typography(raw2)
    assert "$T" not in out2, f"Failed: found $T in '{out2}'"
    assert "T4 and T3" in out2 or "T4" in out2, f"Failed: 'T4 and T3' missing in '{out2}'"
    assert "fT4" in out2, f"Failed: 'fT4' missing in '{out2}'"
    print("[PASS] Test 2: Thyroid hormone notation sanitized.")

    # 3. LaTeX symbols
    raw3 = r"Blood glucose is \sim 250 mg/dL, with \beta-hydroxybutyrate \ge 3 mmol/L and \alpha-subunit."
    out3 = _sanitize_medical_typography(raw3)
    assert r"\sim" not in out3 and "~" in out3, f"Failed: \\sim not converted in '{out3}'"
    assert r"\ge" not in out3 and "≥" in out3, f"Failed: \\ge not converted in '{out3}'"
    assert r"\beta" not in out3 and "β" in out3, f"Failed: \\beta not converted in '{out3}'"
    assert r"\alpha" not in out3 and "α" in out3, f"Failed: \\alpha not converted in '{out3}'"
    print("[PASS] Test 3: LaTeX math symbols converted to Unicode.")


def test_citation_sanitizer():
    # Out of range citation [14] when only [1], [2] exist
    valid_ids = {1, 2}
    reply = "According to initial trials [1], Sertoli cells [14] proliferate rapidly [2]."
    sanitized = _sanitize_citations(reply, valid_ids)

    assert "[1]" in sanitized, f"Failed: valid citation [1] lost in '{sanitized}'"
    assert "[2]" in sanitized, f"Failed: valid citation [2] lost in '{sanitized}'"
    assert "[14]" not in sanitized, f"Failed: invalid citation [14] remained as pill in '{sanitized}'"
    assert "(Ref 14)" in sanitized, f"Failed: expected '(Ref 14)' in '{sanitized}'"
    print("[PASS] Test 4: Out-of-bounds citations sanitized to '(Ref 14)'.")


def test_chunk_dehyphenation():
    raw_ocr = "Major compli-\n cation of severe endo-\ncrine dysfunc-\n  tion."
    cleaned = _clean_chunk_prose(raw_ocr)
    assert "complication" in cleaned, f"Failed: 'complication' not merged in '{cleaned}'"
    assert "endocrine" in cleaned, f"Failed: 'endocrine' not merged in '{cleaned}'"
    assert "dysfunction" in cleaned, f"Failed: 'dysfunction' not merged in '{cleaned}'"
    print("[PASS] Test 5: OCR hyphenated column-splits merged correctly.")


if __name__ == "__main__":
    test_typography_sanitizer()
    test_citation_sanitizer()
    test_chunk_dehyphenation()
    print("\n==========================================")
    print("ALL CITATION & TYPOGRAPHY TESTS PASSED 100%")
    print("==========================================")
