"""
ui/diagrams.py

High-performance Medical Diagram Extraction & Caching Engine for Text-Book-RAG.
Extracts high-resolution visual figures, diagrams, and anatomical plates directly
from source textbook PDFs using PyMuPDF and PageMap offset calibration.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "ui" / "static" / "figures_cache"
DB_PATH = ROOT / "textbook_rag.db"
BOOKS_DIR = ROOT / "ingestion" / "test-books"
PROFILES_DIR = ROOT / "ingestion" / "analysis-result"

# In-memory cache for PageMap instances
_PAGE_MAPS: dict[str, Any] = {}


def _get_page_map(book_slug: str):
    """Loads and caches PageMap for the given book slug if available."""
    if book_slug in _PAGE_MAPS:
        return _PAGE_MAPS[book_slug]

    profile_path = PROFILES_DIR / book_slug / "profile.json"
    if profile_path.exists():
        try:
            from ingestion.page_map import PageMap
            pm = PageMap.from_profile(profile_path)
            _PAGE_MAPS[book_slug] = pm
            return pm
        except Exception:
            pass
    _PAGE_MAPS[book_slug] = None
    return None


def _resolve_pdf_path(book_slug: str) -> Path | None:
    """Finds the source PDF on disk for a book slug."""
    if not BOOKS_DIR.exists():
        return None

    # Exact or near matches
    candidates = list(BOOKS_DIR.glob("*.pdf"))
    for p in candidates:
        stem_slug = p.stem.replace(" ", "_").replace("-", "_").lower()
        clean_target = book_slug.replace("-", "_").lower()
        if clean_target in stem_slug or stem_slug in clean_target:
            return p

    # Fallback to direct name matching
    if "anatomy" in book_slug.lower():
        for p in candidates:
            if "anatomy" in p.name.lower():
                return p
    if "endocrinology" in book_slug.lower() or "williams" in book_slug.lower():
        for p in candidates:
            if "endocrinology" in p.name.lower() or "williams" in p.name.lower():
                return p

    return candidates[0] if candidates else None


def _generate_card_image(target_path: Path, book_slug: str, page_start: int | None, raw_text: str) -> Path | None:
    """Generates a visual clinical reference card JPEG when raw PDF is absent."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return None

    try:
        doc = pymupdf.open()
        page = doc.new_page(width=720, height=480)
        # Background & border
        page.draw_rect(pymupdf.Rect(0, 0, 720, 480), color=(0.93, 0.95, 0.98), fill=(0.96, 0.98, 1.0))
        page.draw_rect(pymupdf.Rect(20, 20, 700, 460), color=(0.82, 0.86, 0.92), fill=(1.0, 1.0, 1.0))
        # Top banner
        page.draw_rect(pymupdf.Rect(20, 20, 700, 80), color=(0.15, 0.35, 0.8), fill=(0.15, 0.35, 0.8))

        clean_book = (book_slug or "Textbook").replace("_", " ").replace("-", " ").title()
        page.insert_text((40, 56), f"CLINICAL TEXTBOOK REFERENCE  •  {clean_book}", fontsize=12, color=(1.0, 1.0, 1.0))

        if page_start:
            page.draw_rect(pymupdf.Rect(580, 36, 680, 66), color=(0.1, 0.25, 0.6), fill=(0.1, 0.25, 0.6))
            page.insert_text((600, 56), f"Page {page_start}", fontsize=12, color=(1.0, 1.0, 1.0))

        match = re.search(r"\b(FIGURE\s+[\d\.]+[A-Za-z]?|Fig\.\s*[\d\.]+)\b", raw_text, re.IGNORECASE)
        fig_title = match.group(0).upper() if match else "TEXTBOOK FIGURE & DIAGRAM"
        page.insert_text((40, 120), fig_title, fontsize=18, color=(0.12, 0.18, 0.3))

        clean_text = re.sub(r"(\w+)-\s*\n\s*(\w+)", r"\1\2", raw_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        page.insert_textbox(pymupdf.Rect(40, 140, 680, 430), clean_text, fontsize=12, color=(0.25, 0.3, 0.38))

        pix = page.get_pixmap(dpi=130)
        pix.save(str(target_path), "jpeg", jpg_quality=85)
        doc.close()
        return target_path
    except Exception as e:
        print(f"[ui.diagrams] Card fallback error: {e}")
        return None


def get_figure_image_path(chunk_id: str) -> Path | None:
    """Resolves or generates a cached JPEG image for a textbook figure chunk.

    Args:
        chunk_id: The chunk identifier from textbook_rag.db.

    Returns:
        Path to the cached image on disk, or None if extraction is unavailable.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target_cache_file = CACHE_DIR / f"{chunk_id}.jpg"
    if target_cache_file.exists() and target_cache_file.stat().st_size > 0:
        return target_cache_file

    if not DB_PATH.exists():
        return None

    # Query chunk metadata from database
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT chunk_id, book_slug, page_start, text, content_type FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if not row:
            return None
        _, book_slug, page_start, raw_text, _ = row
    finally:
        conn.close()

    pdf_path = _resolve_pdf_path(book_slug)
    if not pdf_path or not pdf_path.exists():
        return _generate_card_image(target_cache_file, book_slug, page_start, raw_text)

    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return _generate_card_image(target_cache_file, book_slug, page_start, raw_text)

    # Convert printed page number to 0-based PDF page index
    pm = _get_page_map(book_slug)
    pdf_page_idx = None
    if pm and page_start is not None:
        try:
            # pm.printed_to_pdf returns 1-based page number
            pdf_page_idx = pm.printed_to_pdf(int(page_start)) - 1
        except Exception:
            pass

    if pdf_page_idx is None:
        # Fallback to direct page_start - 1
        pdf_page_idx = max(0, int(page_start) - 1) if page_start else 0

    try:
        doc = pymupdf.open(str(pdf_path))
        if pdf_page_idx >= len(doc):
            pdf_page_idx = min(len(doc) - 1, max(0, pdf_page_idx))
        page = doc[pdf_page_idx]

        # Extract figure label token from text (e.g. "FIGURE 20.12", "Figure 1.1")
        fig_token = None
        match = re.search(r"\b(FIGURE\s+[\d\.]+[A-Za-z]?)\b", raw_text, re.IGNORECASE)
        if match:
            fig_token = match.group(1).strip()

        clip_rect = None
        if fig_token:
            rects = page.search_for(fig_token)
            if not rects:
                # Try simple format e.g. "Figure 1.1" vs "FIGURE 1.1"
                rects = page.search_for(fig_token.upper())
            if rects:
                r = rects[0]
                # OpenStax & Williams textbooks: figure is typically positioned above the caption
                y_top = max(0, r.y0 - 450)
                y_bottom = min(page.rect.y1, r.y1 + 100)
                clip_rect = pymupdf.Rect(page.rect.x0, y_top, page.rect.x1, y_bottom)

        # Check for large embedded image object on the page
        embedded_imgs = page.get_images()
        dominant_img_xref = None
        for img_info in embedded_imgs:
            xref = img_info[0]
            width, height = img_info[2], img_info[3]
            if width >= 300 and height >= 200:
                dominant_img_xref = xref
                break

        # Priority 1: Cropped page rendering around figure caption
        if clip_rect and clip_rect.height > 100:
            pix = page.get_pixmap(clip=clip_rect, dpi=130)
            pix.save(str(target_cache_file), "jpeg", jpg_quality=85)
            doc.close()
            return target_cache_file

        # Priority 2: Raw embedded image extraction
        if dominant_img_xref:
            extracted = doc.extract_image(dominant_img_xref)
            if extracted and extracted.get("image"):
                target_cache_file.write_bytes(extracted["image"])
                doc.close()
                return target_cache_file

        # Priority 3: Full high-resolution page rendering
        pix = page.get_pixmap(dpi=120)
        pix.save(str(target_cache_file), "jpeg", jpg_quality=85)
        doc.close()
        return target_cache_file

    except Exception as e:
        print(f"Failed to extract figure {chunk_id}: {e}")
        return None

