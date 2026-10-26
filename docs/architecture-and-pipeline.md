# Text-Book-RAG: Architecture & Pipeline Documentation

This document serves as the comprehensive source of truth for the **Text-Book-RAG** architecture, data models, completed pipeline stages, and future roadmap.

---

## 1. System Overview & Problem Statement

Standard RAG (Retrieval-Augmented Generation) systems routinely fail on academic, medical, and scientific textbooks because:
1. **Layout Complexity:** Multi-column text (e.g. 2-column pages), running headers/footers, and soft hyphens create stream-order collisions and noisy retrieval.
2. **Page vs. Print Discrepancies:** PDF physical page numbers differ from printed textbook page numbers (Roman numerals, multi-segment front-matter offsets).
3. **Loss of Special Content:** Tables, callout boxes (*Key Points*, *Learning Objectives*), and figure captions get flattened into incoherent prose.
4. **Vocabulary Mismatch:** Section titles use formal publisher terminology (*"Adrenal Cortical Insufficiency"*), while student and clinical queries use informal symptoms or abbreviations (*"Addison's"*, *"ACTH"*, *"low cortisol"*).

### Solution: Two-Layer Hierarchical Architecture
* **Navigation Layer (Heading Tree):** A strict hierarchy of Chapters $\rightarrow$ Sections $\rightarrow$ Subsections derived from the publisher outline, tracking heading paths, coordinates, page ranges, and metadata.
* **Retrieval Layer (Structured Chunks & Hybrid Search):** Leaf sections contain small, bounded passages (300–500 tokens) with 1-sentence sliding overlaps. Tables, figure captions, and callout boxes are segregated into dedicated typed chunks. Retrieval combines PostgreSQL GIN full-text search with pgvector dense embeddings.

---

## 2. Ingestion Pipeline Stages

```
   [PDF Textbook]
          │
          ▼
   ┌──────────────┐
   │ 0. Profiler  │ ──► profile.json (Layout, Fonts, Columns, Gutter, Box Styles)
   └──────────────┘
          │
          ▼
   ┌──────────────┐
   │ 1. Page Map  │ ──► page_map.py (Maps PDF Page Index ◄► Printed Page Number)
   └──────────────┘
          │
          ▼
   ┌──────────────┐
   │ 2. Tree Build│ ──► sections.json (Hierarchical section outline tree)
   └──────────────┘
          │
          ▼
   ┌──────────────┐
   │ 3. Headings  │ ──► headings.json (Pinpoints exact (page, y, line) cut-offs)
   └──────────────┘
          │
          ▼
   ┌──────────────┐
   │ 4. Extract   │ ──► pages.jsonl (PyMuPDF clean lines, 2-column gutter sort)
   └──────────────┘
          │
          ▼
   ┌──────────────┐
   │ 5. Tagging   │ ──► content_blocks.jsonl (Blocks: prose, table, box, caption)
   └──────────────┘
          │
          ▼
   ┌──────────────┐
   │ 6. Chunking  │ ──► chunks.jsonl (300-500 token chunks bounded by sections)
   └──────────────┘
          │
          ▼
   ┌──────────────┐
   │ 7. DB Loader │ ──► PostgreSQL / SQLite FTS5 (Zero-cost keyword search)
   └──────────────┘
```

---

## 3. Pipeline Implementation Details

### Stage 1: Page Mapping (`page_map.py`)
* Converts between 1-based PDF physical page numbers and book-printed page numbers (accounting for Roman numerals and front-matter offsets).

### Stage 2: Section Tree Construction (`section_tree.py`)
* Reads PDF table of contents / bookmarks (`doc.get_toc()`) into a validated tree (`sections.json`).
* Tracks `parent_id`, `heading_path`, `level`, `is_leaf`, and `use` (`teaching`, `review`, `skip`, `front_matter`).

### Stage 3: Heading Locating (`locate_headings.py`)
* Locates the exact line indices and vertical cut points on pages where sections start mid-page. Outputs `headings.json`.

### Stage 4: Page Text Extraction (`extract_pages.py`)
* PyMuPDF text extraction.
* Detects vertical gutters and sorts two-column pages into correct human reading order.
* Drops running headers and footers based on profile band boundaries.
* Normalizes Unicode NFKC, cleans ligatures, and strips soft hyphens. Outputs `pages.jsonl`.

### Stage 5: Special Content Tagging (`tag_special_content.py`)
* Segregates content into typed blocks:
  * **`table`:** PyMuPDF vector bounding boxes + table captions.
  * **`box`:** Callout boxes (*Key Points*, *Chapter Outline*, *Learning Objectives*) bounded by drawing shapes or list pitch.
  * **`figure_caption`:** Typography-isolated caption blocks (`size < body_size * 0.95`).
  * **`prose`:** Paragraphs grouped by measured pitch and break gap thresholds.
* **100% Line Integrity Check:** Asserts every extracted line belongs to exactly one block. Outputs `content_blocks.jsonl`.

### Stage 6: Section Assignment & Chunking (`chunk_sections.py`)
* Assigns every block to its deepest active section using heading cut-points.
* Accumulates prose into **300–500 token chunks** with 1-sentence sliding overlaps.
* Emits tables, callout boxes, and figure captions as standalone semantic chunks.
* **Invariant:** Chunks never cross section boundaries. Outputs `chunks.jsonl`.

### Stage 7: Database Loader & Full-Text Search (`load_to_db.py`, `schema.sql`)
* Schema supporting PostgreSQL (`pgvector`) and local SQLite FTS5 fallback.
* Idempotent batch insertion (`ON CONFLICT (content_hash) DO NOTHING`).
* Built-in search CLI for testing BM25/FTS keyword lookups across sections and chunks.

---

## 4. Corpus Statistics Across Test Books

| Metric | Anatomy & Physiology 2e | Williams Endocrinology 15e | Total Corpus |
| :--- | :--- | :--- | :--- |
| **Total PDF Pages** | 1,347 | 2,319 | **3,666** |
| **Extracted Text Lines** | 55,800 | 267,152 | **322,952** |
| **Tagged Content Blocks** | 14,835 | 23,457 | **38,292** |
| **Section Tree Nodes** | 409 | 2,944 | **3,353** |
| **Retrieval Chunks** | 3,240 | 9,016 | **12,010** |
| - *Prose Chunks* | 1,849 | 7,635 | 9,484 |
| - *Callout Box Chunks* | 525 | 122 | 647 |
| - *Figure Caption Chunks* | 713 | 883 | 1,596 |
| - *Table Chunks* | 153 | 376 | 529 |
| **Median Prose Chunk Size** | 481 tokens | 498 tokens | ~490 tokens |
| **Line & Block Integrity** | 100% Pass | 100% Pass | 100% Pass |

---

## 5. Database Schema

```sql
-- Books Table
CREATE TABLE books (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(255) UNIQUE NOT NULL,
    title VARCHAR(500) NOT NULL,
    total_pages INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Sections Table
CREATE TABLE sections (
    id INT NOT NULL,
    book_slug VARCHAR(255) NOT NULL REFERENCES books(slug) ON DELETE CASCADE,
    parent_id INT,
    title TEXT NOT NULL,
    level INT NOT NULL,
    kind VARCHAR(100),
    use VARCHAR(50) DEFAULT 'teaching',
    pdf_start INT NOT NULL,
    pdf_end INT NOT NULL,
    heading_path TEXT NOT NULL,
    is_leaf BOOLEAN DEFAULT TRUE,
    summary TEXT,
    keywords TEXT[],
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(heading_path, '') || ' ' || coalesce(title, '') || ' ' || coalesce(summary, ''))
    ) STORED,
    PRIMARY KEY (book_slug, id)
);

-- Chunks Table
CREATE TABLE chunks (
    id SERIAL PRIMARY KEY,
    chunk_id VARCHAR(255) UNIQUE NOT NULL,
    book_slug VARCHAR(255) NOT NULL REFERENCES books(slug) ON DELETE CASCADE,
    section_id INT NOT NULL,
    content_type VARCHAR(50) NOT NULL,
    sub_type VARCHAR(100),
    page_start INT NOT NULL,
    page_end INT NOT NULL,
    token_count INT NOT NULL,
    text TEXT NOT NULL,
    embed_text TEXT NOT NULL,
    content_hash VARCHAR(64) UNIQUE NOT NULL,
    embedding vector(1536),
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(embed_text, '') || ' ' || coalesce(text, ''))
    ) STORED,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_slug, section_id) REFERENCES sections(book_slug, id) ON DELETE CASCADE
);
```

---

## 6. Future Roadmap

1. **Dense Vector Embeddings (Step 9):** Generate embeddings using local sentence-transformers (e.g. `all-MiniLM-L6-v2` / `BGE-M3`) or cloud APIs (Gemini `text-embedding-004`).
2. **Hybrid Search Fusion:** Combine PostgreSQL Full-Text Search (keyword exactness) with pgvector HNSW similarity (semantic conceptual search) using Reciprocal Rank Fusion (RRF).
3. **Retrieval Agent & Tools:** Provide tool interfaces (`search_sections`, `search_chunks`, `read_section`) for interactive querying.
4. **LLM Node Enrichment:** Hierarchical summarization and clinical keyword generation to enhance tree navigation.

