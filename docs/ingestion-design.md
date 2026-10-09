
****Ingestion layer****

**A heading tree on top, chunks underneath**

Keep your idea as the *navigation layer*, but give each node something richer than its title:
- Each section node stores its title, its full heading path, page range, and a short LLM-written summary plus key terms (including synonyms and abbreviations).
- Under each leaf section, store smaller chunks with embeddings and a keyword index.
- At query time the LLM gets tools like `search_sections(query)`, `read_section(id)`, and `search_chunks(query, within_section)`. It can start from the tree and drill into the text, and fall back to chunk search when headings don't match.

This costs little extra at ingestion and fixes most of the failure cases above. It's also a good thing to measure: compare heading-only against tree plus chunks on your eval set, and you'll have a real result to put in your README.

**Ingestion design (v1)**

1. **Upload and store** the PDF, create a `book` record with status `processing`.
2. **Extract the structure,** in order of preference:
   - The PDF's built-in bookmarks/outline, which is the most reliable when present
   - The printed table of contents, parsed and matched to pages
   - Heading detection from font size and weight, as a fallback
   
   Output a tree: chapter > section > subsection, each with a start page and end page (the end page is the next sibling's start).
3. **Extract text per page** (PyMuPDF to start; OCR only for pages with no text layer). Remove repeated headers, footers, and page numbers, and fix hyphenation. Keep the page number attached to the text.
4. **Assign text to the tree.** Each page range's text belongs to its deepest node. Check that no pages are orphaned.
5. **Handle special content.** Tables, figure captions, and boxes should be tagged with a content type instead of flattened into the prose.
6. **Chunk within each leaf section,** with a size cap, small overlap, and the heading path and page range stored on every chunk.
7. **Enrich the nodes.** For each section, generate a summary and a keyword list with an LLM. This is the main ingestion cost, so log it per book.
8. **Embed and index.** Embed the chunks and also the node text (heading path plus summary). Store them in Postgres with pgvector, plus full-text search.
9. **Validate and finish.** Check coverage (every page assigned), tree sanity (no empty or huge nodes), and a few sample lookups, then mark the book `ready`.

**Tables to start with:** `books`, `sections` (id, book_id, parent_id, title, level, start_page, end_page, summary, keywords, embedding), `chunks` (id, section_id, text, page_start, page_end, content_type, embedding).

