# Next Steps — Text-Book-RAG Roadmap

## What We've Built So Far (Completed ✅)

| Component | Status |
|---|---|
| PDF ingestion pipeline (7 stages) | ✅ Done |
| SQLite FTS5 + PostgreSQL/pgvector schema | ✅ Done |
| Retrieval tools (`search_chunks`, `search_sections`, `read_section`, `get_toc`, `get_chunk`) | ✅ Done |
| Context manager (token budgeting, session state, tool virtualization, citation registry) | ✅ Done |
| Agent orchestrator (Gemini / OpenAI / Offline Synthesizer fallback) | ✅ Done |
| Web Chat UI (FastAPI + Light theme, Evidence Drawer, Follow-up chips) | ✅ Done |

---

## Next Steps (Priority Order)

### 1. 🔍 Dense Vector Embeddings (Semantic Search)
**Why:** Right now, search is FTS5 keyword-based only. Semantic search lets users find chunks with paraphrased or clinical synonym queries (e.g. query *"low blood pressure and fatigue"* should match a chunk about *Addison's Disease*).

**What to build:**
- Generate embeddings for every chunk using `gemini text-embedding-004` or local `all-MiniLM-L6-v2` (via `sentence-transformers`).
- Store vectors in the existing `embedding vector(1536)` column in PostgreSQL / a local FAISS/SQLite-vec index for offline mode.
- Add `search_chunks_semantic(query, limit)` retrieval tool.

**Files to create:**
- `ingestion/embed_chunks.py` — batch embedder
- `retrieval/semantic.py` — vector similarity search

---

### 2. ⚡ Hybrid Search Fusion (FTS5 + Vector)
**Why:** Neither keyword nor semantic search is sufficient alone. Hybrid fusion gives best-of-both: exact abbreviation matching + conceptual relevance.

**What to build:**
- Reciprocal Rank Fusion (RRF) across FTS5 keyword hits and vector similarity hits.
- Route automatically: use keyword-only for SQLite offline mode, hybrid for PostgreSQL online mode.

---

### 3. 🤖 Full Multi-Turn LLM Tool-Calling Loop (Gemini)
**Why:** The current Gemini path in `orchestrator.py` does a single-shot generate call without a tool-calling loop. A proper agentic loop lets the model call tools iteratively (search → read → synthesize).

**What to build:**
- Fix `_run_gemini_turn()` in `agent/orchestrator.py` to use a proper multi-turn `while tool_calls:` loop (like the OpenAI path already does).
- Add live streaming support for progressive token delivery to the frontend.

---

### 4. 🌊 Streaming Responses to the UI
**Why:** Long answers currently appear all at once after a multi-second wait. Streaming makes the interface feel fast and alive, improving user engagement.

**What to build:**
- Add a `GET /api/chat/stream` SSE (Server-Sent Events) endpoint in `ui/app.py`.
- Update `ui/static/app.js` to consume the SSE stream and progressively render the markdown as tokens arrive.

---

### 5. 🗂️ Persistent Session Storage (Redis / SQLite)
**Why:** Currently sessions are in-memory only — a server restart loses all conversation history. Persistent storage enables multi-device access, session resumption, and analytics.

**What to build:**
- Add `RedisSessionStore` or `SqliteSessionStore` implementation in `context/storage.py`.
- Wire into `ContextManager` via env flag `SESSION_BACKEND=redis|sqlite|memory`.

---

### 6. 🧠 Section Summary & Keyword Enrichment
**Why:** FTS5 search only matches words present in the raw text. Clinical keywords, synonyms, and short summaries per section greatly improve recall.

**What to build:**
- `ingestion/enrich_sections.py` — use an LLM to generate 2-sentence summaries and clinical keyword lists per leaf section.
- Store in the existing `summary` and `keywords` columns in the `sections` table.

---

### 7. 📦 Production Hardening
**Why:** Before showing to real users, the app needs auth, rate limiting, and proper error handling.

**What to build:**
- Basic API key authentication middleware in `ui/app.py`.
- Rate limiting per session (`slowapi` or a simple token bucket).
- Graceful error messages for DB failures and LLM API outages.
- Health-check endpoint `GET /health`.

---

### 8. 🐳 Docker Compose Full Stack
**Why:** Currently requires manual venv setup. A complete Docker Compose stack (FastAPI app + PostgreSQL + pgvector) enables one-command deployment.

**What to build:**
- Add `Dockerfile` for the FastAPI app.
- Update `docker-compose.yml` to include both the app and the database service.

---

## Recommended Next Priority

> **For immediate value:** Steps **3** (Fix Gemini tool-calling loop) and **4** (Streaming) will make the live chat experience dramatically better once a `GEMINI_API_KEY` is set. Step **1** (Dense Embeddings) is the biggest quality boost for retrieval accuracy.

