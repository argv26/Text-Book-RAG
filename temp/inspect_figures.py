import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from retrieval.tools import search_chunks

results = search_chunks("blood pressure", content_type="figure_caption", limit=3)
print(f"Found {len(results)} figure hits:")
for r in results:
    print("---")
    print(f"ID: {r['chunk_id']}")
    print(f"Book: {r['book_slug']}")
    print(f"Page: {r['page_start']}")
    print(f"Snippet: {r['snippet'] or r['text'][:150]}")
