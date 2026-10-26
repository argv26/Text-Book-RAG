"""Master Pipeline Orchestrator for Text-Book-RAG.

Executes the entire ingestion pipeline end-to-end:
  1. extract_pages.py      (PyMuPDF clean lines & 2-column gutter sort)
  2. locate_headings.py    (Locate section heading cut-points on pages)
  3. tag_special_content.py (Tag tables, boxes, captions, prose)
  4. chunk_sections.py     (Assign to outline tree & generate 300-500 tok chunks)
  5. load_to_db.py         (Load into PostgreSQL / SQLite with full-text search)

Usage:
  python run_pipeline.py --all               # Run entire pipeline on all books
  python run_pipeline.py --book <name>       # Run for a specific book
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
INGESTION_DIR = HERE / "ingestion"


def run_stage(title: str, script_name: str, extra_args: list[str]) -> bool:
    script_path = INGESTION_DIR / script_name
    cmd = [PYTHON, str(script_path)] + extra_args
    print(f"\n{'='*70}")
    print(f"STAGE: {title} ({script_name})")
    print(f"{'='*70}")
    start = time.time()
    res = subprocess.run(cmd)
    elapsed = time.time() - start
    if res.returncode != 0:
        print(f"Error: {script_name} failed with exit code {res.returncode}")
        return False
    print(f"[OK] Completed in {elapsed:.2f}s")
    return True


def main():
    parser = argparse.ArgumentParser(description="Run the Text-Book-RAG ingestion pipeline end-to-end.")
    parser.add_argument("--book", type=str, help="Book folder name")
    parser.add_argument("--all", action="store_true", help="Process all books")
    parser.add_argument("--skip-extract", action="store_true", help="Skip extract_pages.py if pages.jsonl already exists")
    args = parser.parse_args()

    book_arg = ["--book", args.book] if args.book else (["--all"] if args.all else [])

    pipeline_stages = [
        ("Step 3: Extract Clean Text per Page", "extract_pages.py", book_arg),
        ("Step 4: Locate Section Headings", "locate_headings.py", book_arg),
        ("Step 5: Tag Special Content (Tables, Boxes, Captions)", "tag_special_content.py", book_arg),
        ("Step 6: Section Assignment & Chunking", "chunk_sections.py", book_arg),
        ("Step 7: Database Setup & Loader", "load_to_db.py", book_arg),
    ]

    print("\nStarting Text-Book-RAG Ingestion Pipeline...")
    overall_start = time.time()

    for title, script, s_args in pipeline_stages:
        if args.skip_extract and script == "extract_pages.py":
            print(f"Skipping {script} (--skip-extract set).")
            continue
        success = run_stage(title, script, s_args)
        if not success:
            print("\nPipeline stopped due to stage failure.")
            sys.exit(1)

    total_time = time.time() - overall_start
    print(f"\n{'*'*70}")
    print(f"SUCCESS: Pipeline completed in {total_time:.2f}s!")
    print(f"{'*'*70}\n")


if __name__ == "__main__":
    main()
