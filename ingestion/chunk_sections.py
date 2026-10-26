"""Step 6: Assign Content Blocks to Sections and Chunk within Leaf Sections.

Reads:
- ingestion/output/<book>/content_blocks.jsonl (from Step 5)
- ingestion/output/<book>/sections.json (from Step 2)
- ingestion/output/<book>/headings.json (from Step 3)

Outputs:
- ingestion/output/<book>/chunks.jsonl (structured retrieval chunks)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"

SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\(])")


@dataclass
class SectionInfo:
    id: int
    title: str
    level: int
    kind: str
    use: str
    parent_id: int | None
    pdf_start: int
    pdf_end: int
    heading_path: str
    is_leaf: bool


def est_tokens(text: str) -> int:
    """Character-based token estimation (~4 chars per token for English)."""
    return max(1, len(text.strip()) // 4)


def compute_hash(text: str) -> str:
    """Deterministic SHA-256 hash of content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences cleanly."""
    sentences = [s.strip() for s in SENT_RE.split(text) if s.strip()]
    return sentences if sentences else [text.strip()]


def load_sections(book_dir: Path) -> dict[int, SectionInfo]:
    sec_file = book_dir / "sections.json"
    if not sec_file.exists():
        raise FileNotFoundError(f"{sec_file} does not exist.")
    data = json.loads(sec_file.read_text(encoding="utf-8"))
    sections = {}
    for item in data:
        s = SectionInfo(
            id=item["id"],
            title=item["title"],
            level=item["level"],
            kind=item.get("kind", ""),
            use=item.get("use", "teaching"),
            parent_id=item.get("parent_id"),
            pdf_start=item["pdf_start"],
            pdf_end=item["pdf_end"],
            heading_path=item.get("heading_path", item["title"]),
            is_leaf=item.get("is_leaf", True),
        )
        sections[s.id] = s
    return sections


def load_heading_cutpoints(book_dir: Path, sections: dict[int, SectionInfo]) -> list[tuple[int, int, int]]:
    """Returns sorted list of (page, start_line, section_id)."""
    head_file = book_dir / "headings.json"
    headings = {}
    if head_file.exists():
        for item in json.loads(head_file.read_text(encoding="utf-8")):
            headings[item["id"]] = item

    cuts = []
    for sid, s in sections.items():
        h = headings.get(sid, {})
        if h.get("found") and h.get("page") and h.get("start") is not None:
            cuts.append((h["page"], h["start"], sid))
        else:
            cuts.append((s.pdf_start, 0, sid))

    # Sort strictly by (page, line)
    cuts.sort(key=lambda x: (x[0], x[1]))
    return cuts


def assign_blocks_to_sections(
    blocks: list[dict],
    cuts: list[tuple[int, int, int]],
) -> dict[int, list[dict]]:
    """Assigns each block to the active section based on (pdf_page, first_line)."""
    assigned = defaultdict(list)
    cut_idx = 0
    num_cuts = len(cuts)

    for b in blocks:
        bp = b["pdf_page"]
        bln = b["line_indices"][0] if b.get("line_indices") else 0

        # Advance active cut if next section has started
        while cut_idx + 1 < num_cuts and (cuts[cut_idx + 1][0], cuts[cut_idx + 1][1]) <= (bp, bln):
            cut_idx += 1

        active_sid = cuts[cut_idx][2]
        assigned[active_sid].append(b)

    return assigned


def chunk_prose_blocks(
    prose_blocks: list[dict],
    section: SectionInfo,
    book_slug: str,
    chunk_counter: int,
    min_tokens: int = 250,
    target_tokens: int = 400,
    max_tokens: int = 550,
) -> tuple[list[dict], int]:
    """Chunks prose blocks within a single section using sliding sentence overlap."""
    chunks = []
    cur_parts = []
    cur_tokens = 0
    cur_pages = []

    def flush_chunk():
        nonlocal cur_parts, cur_tokens, cur_pages, chunk_counter
        if not cur_parts:
            return
        body_text = "\n\n".join(cur_parts)
        p_start = min(cur_pages)
        p_end = max(cur_pages)
        embed_text = f"{section.heading_path}\n\n{body_text}"

        chunks.append({
            "chunk_id": f"{book_slug}_s{section.id:04d}_c{chunk_counter:03d}",
            "book": book_slug,
            "section_id": section.id,
            "heading_path": section.heading_path,
            "section_level": section.level,
            "section_use": section.use,
            "content_type": "prose",
            "sub_type": None,
            "page_start": p_start,
            "page_end": p_end,
            "token_count": est_tokens(body_text),
            "text": body_text,
            "embed_text": embed_text,
            "content_hash": compute_hash(body_text),
        })
        chunk_counter += 1

        # 1-sentence overlap from last paragraph for prose continuity
        last_sentences = split_into_sentences(cur_parts[-1])
        overlap = last_sentences[-1] if last_sentences else ""
        if overlap and est_tokens(overlap) < 80:
            cur_parts = [overlap]
            cur_tokens = est_tokens(overlap)
            cur_pages = [p_end]
        else:
            cur_parts = []
            cur_tokens = 0
            cur_pages = []

    for b in prose_blocks:
        b_text = b["text"].strip()
        b_tok = est_tokens(b_text)
        b_page = b["pdf_page"]

        if b_tok > max_tokens:
            # Paragraph exceeds max chunk size; split cleanly by sentence
            for s in split_into_sentences(b_text):
                s_tok = est_tokens(s)
                if cur_tokens + s_tok > max_tokens and cur_tokens >= min_tokens:
                    flush_chunk()
                cur_parts.append(s)
                cur_tokens += s_tok
                cur_pages.append(b_page)
        else:
            if cur_tokens + b_tok > max_tokens and cur_tokens >= min_tokens:
                flush_chunk()
            cur_parts.append(b_text)
            cur_tokens += b_tok
            cur_pages.append(b_page)

    if cur_parts:
        flush_chunk()

    return chunks, chunk_counter


def process_book_chunks(book_dir: Path) -> dict[str, Any]:
    book_slug = book_dir.name
    print(f"\nChunking book: {book_slug}")
    start_time = time.time()

    cb_file = book_dir / "content_blocks.jsonl"
    if not cb_file.exists():
        print(f"Error: {cb_file} does not exist. Run tag_special_content.py first.")
        return {"status": "error", "message": f"{cb_file} not found"}

    sections = load_sections(book_dir)
    cuts = load_heading_cutpoints(book_dir, sections)

    # 1. Load content blocks
    all_blocks = []
    with cb_file.open(encoding="utf-8") as f:
        for line in f:
            all_blocks.append(json.loads(line))

    # 2. Assign blocks to sections
    assigned = assign_blocks_to_sections(all_blocks, cuts)

    # Invariant: 100% block coverage check
    total_assigned = sum(len(v) for v in assigned.values())
    if total_assigned != len(all_blocks):
        raise ValueError(f"Block coverage mismatch: assigned {total_assigned} vs total {len(all_blocks)}")

    # 3. Generate chunks per section
    all_chunks = []
    type_counts = Counter()
    token_sizes = []

    for sid in sorted(sections.keys()):
        sec_blocks = assigned.get(sid, [])
        if not sec_blocks:
            continue

        section = sections[sid]
        chunk_counter = 1

        # Separate special content vs prose
        special_blocks = [b for b in sec_blocks if b["content_type"] != "prose"]
        prose_blocks = [b for b in sec_blocks if b["content_type"] == "prose"]

        # A. Emit special content blocks as individual chunks
        for sp in special_blocks:
            ctype = sp["content_type"]
            stype = sp.get("sub_type")
            b_text = sp["text"].strip()
            p_start = sp["pdf_page"]
            p_end = sp["pdf_page"]

            if ctype == "box":
                prefix = f"{section.heading_path} > {stype or 'Box'}"
            elif ctype == "table":
                prefix = f"{section.heading_path} > Table"
            else:
                prefix = f"{section.heading_path} > Figure"

            embed_text = f"{prefix}\n\n{b_text}"
            tok = est_tokens(b_text)

            chunk = {
                "chunk_id": f"{book_slug}_s{section.id:04d}_c{chunk_counter:03d}",
                "book": book_slug,
                "section_id": section.id,
                "heading_path": section.heading_path,
                "section_level": section.level,
                "section_use": section.use,
                "content_type": ctype,
                "sub_type": stype,
                "page_start": p_start,
                "page_end": p_end,
                "token_count": tok,
                "text": b_text,
                "embed_text": embed_text,
                "content_hash": compute_hash(b_text),
            }
            all_chunks.append(chunk)
            type_counts[ctype] += 1
            token_sizes.append(tok)
            chunk_counter += 1

        # B. Chunk prose blocks
        if prose_blocks:
            p_chunks, chunk_counter = chunk_prose_blocks(
                prose_blocks=prose_blocks,
                section=section,
                book_slug=book_slug,
                chunk_counter=chunk_counter,
            )
            for pc in p_chunks:
                all_chunks.append(pc)
                type_counts[pc["content_type"]] += 1
                token_sizes.append(pc["token_count"])

    # 4. Save to chunks.jsonl
    out_file = book_dir / "chunks.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c) + "\n")

    elapsed = time.time() - start_time
    avg_tokens = sum(token_sizes) / len(token_sizes) if token_sizes else 0
    prose_tokens = [c["token_count"] for c in all_chunks if c["content_type"] == "prose"]
    avg_prose = sum(prose_tokens) / len(prose_tokens) if prose_tokens else 0

    stats = {
        "book": book_slug,
        "total_blocks": len(all_blocks),
        "total_chunks": len(all_chunks),
        "breakdown": dict(type_counts),
        "avg_tokens": round(avg_tokens, 1),
        "avg_prose_tokens": round(avg_prose, 1),
        "elapsed_sec": round(elapsed, 2),
        "output_file": str(out_file),
    }

    print(f"  Finished in {stats['elapsed_sec']}s")
    print(f"  Blocks {stats['total_blocks']} -> Chunks {stats['total_chunks']}")
    print(f"  Breakdown: {stats['breakdown']}")
    print(f"  Average prose chunk size: {stats['avg_prose_tokens']} tokens")
    print(f"  Output saved to: {out_file}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Assign content blocks to sections and chunk within leaf sections.")
    parser.add_argument("--book", type=str, help="Book folder name inside ingestion/output")
    parser.add_argument("--all", action="store_true", help="Process all books in ingestion/output")
    args = parser.parse_args()

    if args.book:
        target = OUTPUT_DIR / args.book
        if not target.exists():
            print(f"Error: {target} does not exist.")
            sys.exit(1)
        process_book_chunks(target)
    else:
        for book_dir in sorted(OUTPUT_DIR.iterdir()):
            if book_dir.is_dir():
                process_book_chunks(book_dir)


if __name__ == "__main__":
    main()

