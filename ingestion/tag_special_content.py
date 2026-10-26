"""Step 5: Tag Special Content (Tables, Boxes, Figure Captions, and Prose).

Reads:
- ingestion/output/<book>/pages.jsonl (lines extracted per page)
- ingestion/analysis-result/<book>/profile.json (book profile and recipe)
- ingestion/test-books/<book>.pdf (for table bounding boxes and drawing rects)

Outputs:
- ingestion/output/<book>/content_blocks.jsonl (structured content blocks with content_type, sub_type, bbox, text, line_indices)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf

HERE = Path(__file__).resolve().parent
ANALYSIS_DIR = HERE / "analysis-result"
PDF_DIR = HERE / "test-books"
OUTPUT_DIR = HERE / "output"

FIG_REGEX = re.compile(r"^[\s\u2022\uf0b7\ufffd\*\-]*\b(fig\.|figure|illustration)\s*[A-Z]?\d+([.\-\u2013]\d+)*[A-Za-z]?\b", re.I)
TAB_REGEX = re.compile(r"^[\s\u2022\uf0b7\ufffd\*\-]*\btable\s*[A-Z]?\d+([.\-\u2013]\d+)*[A-Za-z]?\b", re.I)
BULLET_CHARS = {"\u2022", "*", "\u2013", "\u2014", "\u25e6", "\x07", "\uf0b7", "\ufffd", "-", "\xb7"}


@dataclass
class LineRef:
    idx: int
    t: str
    x0: float
    y0: float
    x1: float
    y1: float
    col: int
    style: str
    size: float


@dataclass
class ContentBlock:
    pdf_page: int
    block_id: int
    content_type: str        # 'prose' | 'table' | 'box' | 'figure_caption'
    sub_type: str | None     # e.g. 'KEY POINTS', 'LEARNING OBJECTIVES', 'table', 'figure_caption'
    text: str
    bbox: list[float]        # [min_x0, min_y0, max_x1, max_y1]
    col: int | None
    line_count: int
    line_indices: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_page": self.pdf_page,
            "block_id": self.block_id,
            "content_type": self.content_type,
            "sub_type": self.sub_type,
            "text": self.text,
            "bbox": [round(c, 2) for c in self.bbox],
            "col": self.col,
            "line_count": self.line_count,
            "line_indices": self.line_indices,
        }


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_pdf_for_book(book_name: str) -> Path | None:
    norm_target = _norm_name(book_name)
    for p in PDF_DIR.glob("*.pdf"):
        if _norm_name(p.stem) == norm_target:
            return p
    return None


def extract_font_size(style_str: str) -> float:
    m = re.search(r"\b([\d\.]+)pt\b", style_str)
    return float(m.group(1)) if m else 9.0


def is_bullet_line(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if t[0] in BULLET_CHARS:
        return True
    first_word = t.split()[0]
    return any(c in BULLET_CHARS for c in first_word)


def tag_page(
    pdf_page: int,
    raw_lines: list[dict],
    page_doc: pymupdf.Page | None,
    recipe: dict,
) -> list[ContentBlock]:
    if not raw_lines:
        return []

    lines = [
        LineRef(
            idx=i,
            t=l["t"],
            x0=float(l["x0"]),
            y0=float(l["y0"]),
            x1=float(l["x1"]),
            y1=float(l["y1"]),
            col=int(l.get("col", 0)),
            style=l.get("style", ""),
            size=extract_font_size(l.get("style", "")),
        )
        for i, l in enumerate(raw_lines)
    ]

    n_lines = len(lines)
    assigned: list[tuple[str, str | None, int] | None] = [None] * n_lines  # (content_type, sub_type, block_group_id)
    group_counter = 0

    tables_bboxes: list[tuple[float, float, float, float]] = []
    filled_rects: list[pymupdf.Rect] = []

    has_tab_candidate = any(TAB_REGEX.match(l.t) for l in lines)

    if page_doc is not None:
        if has_tab_candidate:
            try:
                tabs = page_doc.find_tables().tables
                tables_bboxes = [t.bbox for t in tabs]
            except Exception:
                tables_bboxes = []

        try:
            drawings = page_doc.get_drawings()
            for d in drawings:
                rect = d.get("rect")
                if rect and d.get("fill") and rect.width >= 120 and rect.height >= 18:
                    filled_rects.append(rect)
        except Exception:
            filled_rects = []

    body_style = recipe.get("typography", {}).get("body_style", "")
    body_size = extract_font_size(body_style)
    box_types = recipe.get("boxes", {}).get("box_types", [])
    fig_recipe = recipe.get("figures", {})
    cap_label_style = fig_recipe.get("caption_label_style")
    cap_cont_style = fig_recipe.get("caption_continuation_style")
    block_struct = recipe.get("block_structure", {})
    para_gap = block_struct.get("paragraph_break_gap_pt") or 14.5
    line_pitch = block_struct.get("line_pitch_pt") or 11.5

    # -------------------------------------------------------------------------
    # 1. Detect Tables
    # -------------------------------------------------------------------------
    for tx0, ty0, tx1, ty1 in tables_bboxes:
        group_id = group_counter
        group_counter += 1
        has_lines = False

        for l in lines:
            if assigned[l.idx] is not None:
                continue
            cy = (l.y0 + l.y1) / 2.0
            cx = (l.x0 + l.x1) / 2.0
            if (ty0 - 3.0 <= cy <= ty1 + 3.0) and (tx0 - 15.0 <= cx <= tx1 + 15.0):
                assigned[l.idx] = ("table", "table", group_id)
                has_lines = True

        # Associate table caption line above or below
        for l in lines:
            if assigned[l.idx] is None and TAB_REGEX.match(l.t):
                if (abs(l.y1 - ty0) <= 45.0) or (abs(l.y0 - ty1) <= 45.0):
                    assigned[l.idx] = ("table", "table", group_id)
                    has_lines = True

    # Standalone table captions
    for l in lines:
        if assigned[l.idx] is None and TAB_REGEX.match(l.t):
            group_id = group_counter
            group_counter += 1
            assigned[l.idx] = ("table", "table_caption", group_id)

    # -------------------------------------------------------------------------
    # 2. Detect Callout Boxes
    # -------------------------------------------------------------------------
    for l in lines:
        if assigned[l.idx] is not None:
            continue

        t_clean = l.t.strip().upper()
        matched_box = None
        for bt in box_types:
            lbl = bt["label"].strip().upper()
            if lbl.endswith("..."):
                prefix = lbl[:-3].strip()
                if t_clean.startswith(prefix):
                    matched_box = bt
                    break
            else:
                if t_clean == lbl or t_clean.startswith(lbl):
                    matched_box = bt
                    break

        if matched_box:
            group_id = group_counter
            group_counter += 1
            sub_type = matched_box["label"]
            assigned[l.idx] = ("box", sub_type, group_id)

            # Check if enclosed in drawing rectangle
            enclosing = [
                r for r in filled_rects
                if (r.y0 - 8.0 <= l.y0 and r.y1 + 8.0 >= l.y1 and r.x0 - 8.0 <= l.x0 and r.x1 + 8.0 >= l.x1)
            ]

            if enclosing:
                box_rect = min(enclosing, key=lambda r: (r.width * r.height))
                for other in lines:
                    if assigned[other.idx] is None:
                        ocy = (other.y0 + other.y1) / 2.0
                        ocx = (other.x0 + other.x1) / 2.0
                        if (box_rect.y0 - 3.0 <= ocy <= box_rect.y1 + 3.0) and (box_rect.x0 - 15.0 <= ocx <= box_rect.x1 + 15.0):
                            assigned[other.idx] = ("box", sub_type, group_id)
            else:
                # Containerless box (e.g. LEARNING OBJECTIVES)
                cur_idx = l.idx
                for nxt_idx in range(l.idx + 1, n_lines):
                    if assigned[nxt_idx] is not None:
                        break
                    nxt_l = lines[nxt_idx]
                    pitch = nxt_l.y0 - lines[cur_idx].y0
                    gap = nxt_l.y0 - lines[cur_idx].y1

                    # Continuation criteria for bulleted/unboxed lists
                    is_bullet = is_bullet_line(nxt_l.t)
                    if is_bullet or (pitch <= line_pitch + 4.0 and gap < 6.0):
                        assigned[nxt_idx] = ("box", sub_type, group_id)
                        cur_idx = nxt_idx
                    else:
                        break

    # -------------------------------------------------------------------------
    # 3. Detect Figure Captions
    # -------------------------------------------------------------------------
    for l in lines:
        if assigned[l.idx] is not None:
            continue

        if FIG_REGEX.match(l.t):
            # Verify it is not a regular body text sentence merely mentioning figure
            is_caption_style = (
                (l.style == cap_label_style) or
                (l.size < body_size * 0.95) or
                ("bold" in l.style.lower()) or
                (l.style != body_style)
            )
            if not is_caption_style:
                continue

            group_id = group_counter
            group_counter += 1
            assigned[l.idx] = ("figure_caption", "figure_caption", group_id)

            cur_idx = l.idx
            for nxt_idx in range(l.idx + 1, n_lines):
                if assigned[nxt_idx] is not None:
                    break
                nxt_l = lines[nxt_idx]
                gap = nxt_l.y0 - lines[cur_idx].y1
                pitch = nxt_l.y0 - lines[cur_idx].y0

                # Caption continuation lines must have smaller font size or caption style
                is_cont_style = (
                    (nxt_l.style == cap_cont_style) or
                    (nxt_l.size < body_size * 0.95)
                )

                if is_cont_style and gap <= 9.0 and abs(nxt_l.x0 - l.x0) <= 35.0:
                    assigned[nxt_idx] = ("figure_caption", "figure_caption", group_id)
                    cur_idx = nxt_idx
                else:
                    break

    # -------------------------------------------------------------------------
    # 4. Group Remaining Lines into Prose Paragraphs
    # -------------------------------------------------------------------------
    for l in lines:
        if assigned[l.idx] is None:
            can_merge = False
            if l.idx > 0 and assigned[l.idx - 1] is not None:
                prev_tag = assigned[l.idx - 1]
                if prev_tag[0] == "prose":
                    prev_l = lines[l.idx - 1]
                    pitch = l.y0 - prev_l.y0
                    gap = l.y0 - prev_l.y1
                    # Paragraph continuation within the same column:
                    # Must stay within the paragraph pitch threshold and not have a large gap
                    if l.col == prev_l.col and pitch < para_gap and gap <= 6.5:
                        assigned[l.idx] = ("prose", None, prev_tag[2])
                        can_merge = True

            if not can_merge:
                group_id = group_counter
                group_counter += 1
                assigned[l.idx] = ("prose", None, group_id)

    # -------------------------------------------------------------------------
    # Integrity Check: Ensure 100% of lines are tagged
    # -------------------------------------------------------------------------
    for i, tag in enumerate(assigned):
        if tag is None:
            raise ValueError(f"Line {i} on page {pdf_page} was not tagged!")

    # -------------------------------------------------------------------------
    # Build Structured Content Blocks
    # -------------------------------------------------------------------------
    groups: dict[int, list[LineRef]] = {}
    group_meta: dict[int, tuple[str, str | None]] = {}

    for l in lines:
        tag = assigned[l.idx]
        assert tag is not None
        gid = tag[2]
        if gid not in groups:
            groups[gid] = []
            group_meta[gid] = (tag[0], tag[1])
        groups[gid].append(l)

    blocks: list[ContentBlock] = []
    for block_num, (gid, glines) in enumerate(groups.items(), start=1):
        ctype, csubtype = group_meta[gid]
        full_text = "\n".join(gl.t for gl in glines)
        b_x0 = min(gl.x0 for gl in glines)
        b_y0 = min(gl.y0 for gl in glines)
        b_x1 = max(gl.x1 for gl in glines)
        b_y1 = max(gl.y1 for gl in glines)
        col = glines[0].col if len({gl.col for gl in glines}) == 1 else None

        blocks.append(
            ContentBlock(
                pdf_page=pdf_page,
                block_id=block_num,
                content_type=ctype,
                sub_type=csubtype,
                text=full_text,
                bbox=[b_x0, b_y0, b_x1, b_y1],
                col=col,
                line_count=len(glines),
                line_indices=[gl.idx for gl in glines],
            )
        )

    return blocks


def process_book(book_dir: Path, limit_pages: int | None = None) -> dict[str, Any]:
    book_name = book_dir.name
    print(f"\nProcessing book: {book_name}")
    start_time = time.time()

    pages_file = book_dir / "pages.jsonl"
    if not pages_file.exists():
        print(f"Error: {pages_file} does not exist. Run extract_pages.py first.")
        return {"status": "error", "message": f"{pages_file} not found"}

    profile_file = ANALYSIS_DIR / book_name / "profile.json"
    if not profile_file.exists():
        print(f"Error: {profile_file} does not exist.")
        return {"status": "error", "message": f"{profile_file} not found"}

    profile = json.loads(profile_file.read_text(encoding="utf-8"))
    recipe = profile.get("recipe", {})

    pdf_file = find_pdf_for_book(book_name)
    doc = None
    if pdf_file and pdf_file.exists():
        try:
            doc = pymupdf.open(pdf_file)
            print(f"  Opened PDF: {pdf_file.name} ({len(doc)} pages)")
        except Exception as e:
            print(f"  Warning: could not open PDF {pdf_file}: {e}")
    else:
        print(f"  Warning: No PDF found for {book_name}. Proceeding without PyMuPDF vector/table analysis.")

    out_file = book_dir / "content_blocks.jsonl"
    total_pages = 0
    total_lines = 0
    total_blocks = 0
    type_counts = Counter()

    with pages_file.open(encoding="utf-8") as fin, out_file.open("w", encoding="utf-8") as fout:
        for idx, line in enumerate(fin):
            if limit_pages is not None and idx >= limit_pages:
                break
            page_rec = json.loads(line)
            pno = page_rec["pdf_page"]
            raw_lines = page_rec.get("lines", [])
            total_pages += 1
            total_lines += len(raw_lines)

            page_doc = doc[pno - 1] if doc is not None and 1 <= pno <= len(doc) else None
            blocks = tag_page(pno, raw_lines, page_doc, recipe)

            # Verification: each line belongs to exactly one block
            tagged_indices = [idx for b in blocks for idx in b.line_indices]
            if sorted(tagged_indices) != list(range(len(raw_lines))):
                raise ValueError(f"Line integrity failure on PDF page {pno}: {len(tagged_indices)} vs {len(raw_lines)}")

            for b in blocks:
                fout.write(json.dumps(b.to_dict()) + "\n")
                type_counts[b.content_type] += 1
                total_blocks += 1

            if total_pages % 200 == 0:
                print(f"  Processed {total_pages} pages ({total_blocks} blocks created)...")

    elapsed = time.time() - start_time
    stats = {
        "book": book_name,
        "pages": total_pages,
        "lines": total_lines,
        "blocks": total_blocks,
        "breakdown": dict(type_counts),
        "elapsed_sec": round(elapsed, 2),
        "output_file": str(out_file),
    }

    print(f"  Finished in {stats['elapsed_sec']}s")
    print(f"  Total lines: {total_lines} -> Total blocks: {total_blocks}")
    print(f"  Block breakdown: {stats['breakdown']}")
    print(f"  Output saved to: {out_file}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Tag special content and generate content blocks.")
    parser.add_argument("--book", type=str, help="Name of book directory inside ingestion/output")
    parser.add_argument("--all", action="store_true", help="Process all books found in ingestion/output")
    parser.add_argument("--limit-pages", type=int, default=None, help="Process at most N pages per book")
    args = parser.parse_args()

    if args.book:
        target = OUTPUT_DIR / args.book
        if not target.exists():
            print(f"Error: {target} does not exist.")
            sys.exit(1)
        process_book(target, limit_pages=args.limit_pages)
    else:
        for book_dir in sorted(OUTPUT_DIR.iterdir()):
            if book_dir.is_dir():
                process_book(book_dir, limit_pages=args.limit_pages)


if __name__ == "__main__":
    main()
