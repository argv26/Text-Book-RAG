import sqlite3

conn = sqlite3.connect("textbook_rag.db")
cur = conn.cursor()
rows = cur.execute("SELECT book_slug, page_start, text FROM chunks WHERE content_type='figure_caption' LIMIT 5").fetchall()
for b, p, txt in rows:
    print(f"[{b} p.{p}] {txt[:150]}...")
