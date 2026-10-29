# Structure-Aware Textbook RAG: Architecture & System Design

This document details the system design, data models, ingestion pipeline, retrieval engines, and agent orchestration for the **Text-Book-RAG** project.

---

## 1. System Overview

Standard Retrieval-Augmented Generation (RAG) models often degrade on complex textbooks due to:
1. **Multi-Column Layouts & Header Collisions**: Standard PDF extraction merges adjacent columns or running headers into body text.
2. **Page vs. Print Discrepancies**: Physical PDF page indexes deviate from printed textbook pagination due to front-matter and appendices.
3. **Loss of Special Content**: Tables, boxes, learning objectives, and figure captions lose structural semantics when flattened into plain text.
4. **Vocabulary Mismatch**: Medical and academic literature uses precise taxonomic headings, whereas clinical queries use symptom-oriented vernacular.

### The Two-Layer Solution
* **Navigation Layer (Heading Tree)**: Hierarchical outline tracking `Chapters → Sections → Subsections` with physical and printed page coordinates, breadcrumb paths, and section summaries.
* **Retrieval Layer (Structured Chunks & Hybrid Search)**: Sections are subdivided into bounded passages (300–500 tokens) with 1-sentence overlaps. Specialized blocks (tables, figure captions, callouts) maintain dedicated typing. Search combines full-text search (BM25 / GIN) with dense vector embeddings.

---

## 2. Ingestion Pipeline

```mermaid
flowchart TD
    PDF["Textbook PDF"] --> Profiler["0. Profiler (profile.json)"]
    Profiler --> PageMap["1. Page Map (PDF vs Print Page Map)"]
    PageMap --> TreeBuild["2. Tree Build (Section Hierarchy)"]
    TreeBuild --> Headings["3. Locate Headings (Exact Cut-Offs)"]
    Headings --> Extract["4. Extract Pages (Multi-Column Gutter Sort)"]
    Extract --> Tagging["5. Content Tagging (Prose, Tables, Figures, Boxes)"]
    Tagging --> Chunking["6. Chunking (Bounded 300-500 Tokens)"]
    Chunking --> LoadDB["7. Load to Database (SQLite / Postgres)"]
```

### Pipeline Modules
- `ingestion/extract_pages.py`: Multi-column line extraction, column gutter sorting, running header/footer pruning using PyMuPDF.
- `ingestion/page_map.py`: Reconciles PDF physical indexes with printed page numbers.
- `ingestion/section_tree.py`: Parses publisher table of contents into hierarchical outline trees.
- `ingestion/locate_headings.py`: Pinpoints coordinate boundaries for sections.
- `ingestion/tag_special_content.py`: Detects and annotates callout boxes, tables, and figure captions.
- `ingestion/chunk_sections.py`: Generates semantic text chunks bounded strictly by section headings.
- `ingestion/load_to_db.py`: Indexes chunks, sections, books, and figures into SQLite and PostgreSQL.

---

## 3. Database Schema

### `sections`
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER | Primary key within book |
| `book_slug` | TEXT | Book identifier |
| `title` | TEXT | Section heading |
| `heading_path` | TEXT | Full breadcrumb hierarchy |
| `level` | INTEGER | Heading depth (1 = Chapter, 2 = Major Section, etc.) |
| `pdf_start`, `pdf_end` | INTEGER | Physical PDF page bounds |
| `page_start`, `page_end` | TEXT / INT | Printed textbook page numbers |
| `is_leaf` | BOOLEAN | Indicates terminal subsection |

### `chunks`
| Column | Type | Description |
| :--- | :--- | :--- |
| `chunk_id` | TEXT | Unique chunk identifier |
| `book_slug` | TEXT | Book identifier |
| `section_id` | INTEGER | Foreign key to `sections` |
| `content_type` | TEXT | `prose`, `table`, `box`, `figure_caption` |
| `sub_type` | TEXT | Detailed block classification |
| `page_start`, `page_end` | INTEGER | Printed page boundaries |
| `token_count` | INTEGER | Token size cap (300–500) |
| `text` | TEXT | Content text with soft hyphens de-hyphenated |

---

## 4. Modular Codebase Architecture

```
Text-Book-RAG/
├── agent/
│   ├── orchestrator.py    # Thin turn manager & streaming dispatcher
│   ├── offline.py         # Local smart synthesizer for offline execution
│   ├── providers.py       # Google Gemini & OpenAI tool-calling loops
│   ├── sanitizers.py      # Medical typography & citation integrity cleaners
│   ├── tools_spec.py      # Tool schema definitions & execution dispatcher
│   └── prompts.py         # System prompt and clinical persona
├── context/
│   ├── manager.py         # Multi-turn window manager & virtualization
│   ├── compactor.py       # Context summarization & token budgeting
│   ├── citations.py       # Session-scoped citation registries
│   └── models.py          # Session and message data models
├── retrieval/
│   ├── engine.py          # Factory function & re-exports
│   ├── base.py            # BaseEngine abstract specification
│   ├── sqlite.py          # SqliteEngine with FTS5 search
│   ├── postgres.py        # PostgresEngine with GIN & pgvector
│   ├── parallel.py        # Concurrent multi-tool executor
│   ├── tools.py           # Tool call implementations
│   └── models.py          # SectionHit, ChunkHit, SectionContent
├── ui/
│   ├── app.py             # FastAPI entrypoint (<65 lines)
│   ├── schemas.py         # Pydantic request & response models
│   ├── dependencies.py    # Auth, session, and rate-limiting dependencies
│   ├── auth.py            # User authentication & token management
│   ├── sessions.py        # Session persistence & auto-titling
│   ├── rate_limiter.py    # Per-user daily request quota enforcement
│   ├── diagrams.py        # PyMuPDF on-the-fly diagram extraction & caching
│   ├── routers/
│   │   ├── auth.py        # /api/auth endpoints
│   │   ├── sessions.py    # /api/sessions endpoints
│   │   ├── chat.py        # /api/chat & /api/chat/stream endpoints
│   │   └── content.py     # /api/books, /api/chunk, /api/figure endpoints
│   └── static/
│       ├── index.html     # Single-page interface
│       ├── styles.css     # Tailwind & typography styling
│       └── app.js         # Frontend controller & UI event management
└── tests/
    ├── unit/              # Isolated service & sanitizer unit tests
    └── integration/       # API, SSE streaming, and UI component tests
```

---

## 5. Agent Orchestration & Tool Calling

1. **User Query Analysis**:
   - Conversational pleasantries and greetings receive immediate responses.
   - Clinical inquiries trigger concurrent parallel retrieval (`chunks`, `sections`, `figures`).
2. **Execution Strategy**:
   - When `GEMINI_API_KEY` is present, multi-turn ReAct tool-calling loops run via Google GenAI (`gemini-flash-lite-latest` with automated fallback).
   - When `OPENAI_API_KEY` is present, tool-calling runs via OpenAI (`gpt-4o`).
   - If keys are omitted or external services are unreachable, `agent/offline.py` synthesizes answers from local textbook matches with 100% offline availability.
3. **Medical Typography & Citation Normalization**:
   - Raw LaTeX tags (e.g. `\text{...}`) and hormone math delimiters (`$T4$`, `$TR\beta2$`) are sanitized into clean Unicode.
   - Out-of-catalog bracketed references (e.g. `[14]` copied from textbook bibliographies) are converted to plaintext `(Ref 14)` so only registered citations become clickable pills.

