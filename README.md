---
title: TextBook RAG
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
---

# TextBook RAG

A production-grade, structure-aware **Retrieval-Augmented Generation (RAG)** system for academic medical textbooks, with an interactive web chat interface.

---

## Why TextBook RAG?

Standard RAG systems fail on academic textbooks:

- **Multi-column layouts** cause reading-order collisions — page text is extracted in wrong order
- **PDF vs. printed pages** — page numbers never match between the PDF file and the physical book
- **Lost special content** — tables, callout boxes (*Key Points*, *Clinical Notes*) get flattened into incoherent prose
- **Vocabulary mismatch** — publisher titles say *"Adrenal Cortical Insufficiency"*, students search for *"Addison's"* or *"low cortisol"*

**TextBook RAG** solves all of this with a two-layer hierarchical architecture and structure-aware retrieval.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Ingestion Pipeline                          │
│                                                                 │
│  PDF → Page Map → Section Tree → Headings → Page Text          │
│       → Tag Blocks → Chunk → Load DB                           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   SQLite FTS5  /    │
                    │  PostgreSQL+pgvector │
                    └──────────┬──────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │             Retrieval Layer              │
          │  search_sections · search_chunks         │
          │  read_section · get_toc · get_chunk      │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │          Agent Orchestrator             │
          │  ContextManager · CitationRegistry       │
          │  Gemini / OpenAI / Offline Synthesizer   │
          └────────────────────┬────────────────────┘
                               │
          ┌────────────────────▼────────────────────┐
          │       FastAPI Web Chat Interface         │
          │  Evidence Drawer · Follow-up Chips       │
          │  Tool Badges · Multi-turn Sessions       │
          └─────────────────────────────────────────┘
```

---

## Quickstart

### 1. Clone & Install

```bash
git clone <repo-url>
cd Text-Book-RAG

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY (optional — app works offline without it)
```

### 3. Add Textbooks

Place PDF textbooks in `ingestion/test-books/`.

### 4. Run the Ingestion Pipeline

```bash
# Run full pipeline on all books in ingestion/test-books/
python run_pipeline.py --all

# Or for a single book
python run_pipeline.py --book "Williams Textbook of Endocrinology"
```

### 5. Start the Web UI

```bash
python -m ui.app
# Open http://127.0.0.1:8000
```

---

## Project Structure

```
Text-Book-RAG/
│
├── ingestion/                  # PDF → Database pipeline
│   ├── page_map.py             # PDF page ↔ printed page mapping
│   ├── section_tree.py         # Publisher TOC → hierarchical tree
│   ├── locate_headings.py      # Exact heading positions on pages
│   ├── extract_pages.py        # PyMuPDF 2-column gutter-sort extraction
│   ├── tag_special_content.py  # Tag tables, boxes, captions, prose
│   ├── chunk_sections.py       # 300–500 token chunks bounded by sections
│   ├── load_to_db.py           # SQLite FTS5 + PostgreSQL loader
│   └── schema.sql              # PostgreSQL DDL (pgvector + GIN FTS)
│
├── retrieval/                  # Retrieval tools and engine
│   ├── engine.py               # SqliteEngine + PostgresEngine
│   ├── models.py               # SectionHit, ChunkHit, SectionContent
│   └── tools.py                # LLM-callable tool functions + CLI
│
├── context/                    # Multi-turn session management
│   ├── manager.py              # ContextManager (token budget + citations)
│   ├── compactor.py            # Tool output virtualization + pruning
│   ├── citations.py            # CitationRegistry
│   ├── models.py               # Message, SessionState, CitationRecord
│   └── storage.py              # InMemorySessionStore
│
├── agent/                      # LLM orchestration
│   ├── orchestrator.py         # AgentOrchestrator (Gemini/OpenAI/Offline)
│   └── prompts.py              # Clinical tutor system prompt
│
├── ui/                         # Web application
│   ├── app.py                  # FastAPI server + REST API
│   ├── rate_limiter.py         # Daily query quota enforcement (SQLite-backed)
│   └── static/
│       ├── index.html          # Single-page chat application
│       ├── app.js              # Client-side logic
│       └── styles.css          # Light theme + typography
│
├── docs/                       # Architecture and specifications
├── run_pipeline.py             # Master CLI for full ingestion run
├── docker-compose.yml          # PostgreSQL + pgvector for production
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Features

| Feature | Description |
|---|---|
| **Structure-Aware Chunking** | Chunks never cross section boundaries; tables and callout boxes are typed and indexed separately |
| **Printed Page Citations** | Every answer cites the real printed page number from the physical textbook |
| **Clickable Evidence Drawer** | Click any `[1]` citation badge to see the exact passage, section heading, and page in a side drawer |
| **Multi-Turn Sessions** | Full conversation state with token budgeting, tool output compaction, and citation accumulation |
| **Offline-First** | Works 100% locally with SQLite FTS5 — no API keys, no Docker required |
| **LLM-Powered Mode** | Set `GEMINI_API_KEY` or `OPENAI_API_KEY` for full agentic multi-hop tool calling |
| **Follow-up Suggestions** | Interactive chips guide users to related clinical concepts |

---

## Corpus Statistics

| Metric | Anatomy & Physiology 2e | Williams Endocrinology 15e | Total |
|---|---|---|---|
| PDF Pages | 1,347 | 2,319 | **3,666** |
| Section Nodes | 409 | 2,944 | **3,353** |
| Retrieval Chunks | 3,240 | 9,016 | **12,010** |
| Median Chunk Size | 481 tokens | 498 tokens | ~490 tokens |
| Line Integrity | 100% | 100% | **100%** |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(none)* | Enables Gemini LLM mode |
| `OPENAI_API_KEY` | *(none)* | Enables OpenAI LLM mode |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Override Gemini model |
| `OPENAI_MODEL` | `gpt-4o` | Override OpenAI model |
| `DATABASE_URL` | *(none)* | PostgreSQL connection string; omit for SQLite |
| `DAILY_REQUEST_LIMIT` | `50` | Maximum queries allowed per IP/day (0 for unlimited) |
| `PORT` | `8000` | HTTP server port |

---

## Production Deployment

The application is fully containerized and self-contained with a pre-indexed database:

```bash
# 1-command Docker deployment:
docker compose up -d web
# Access at http://localhost:8000
```

See [docs/deployment-guide.md](docs/deployment-guide.md) for complete instructions on deploying to **Google Cloud Run**, **Render**, **Railway**, or any Linux VPS.

```bash
# Search textbook outline headings
python -m retrieval.tools search-sections "adrenal cortex"

# Search specific passages and tables
python -m retrieval.tools search-chunks "cortisol synthesis" --limit 10

# Read a full textbook section in reading order
python -m retrieval.tools read-section --book Williams_Textbook_of_Endocrinology_15th_edition_2 --section-id 6

# Browse table of contents
python -m retrieval.tools get-toc --book anatomy-and-physiology-2e_-_WEB
```

---

## Production Deployment (PostgreSQL)

```bash
# Start PostgreSQL + pgvector
docker compose up -d

# Set the database URL
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/textbook_rag

# Run ingestion against PostgreSQL
python run_pipeline.py --all

# Start the web server
python -m ui.app
```

---

## License

Designed for educational and research textbook retrieval workflows.
